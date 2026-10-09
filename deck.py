"""Stream Deck Neo daemon. Owns the device, exposes it over HTTP on 127.0.0.1:8885.

Keys/pages come from profile.json next to this file ({home} and {actions} are expanded in commands).
Survives the Neo being unplugged/replugged: it waits for a device and reconnects on its own.
Runs on Python 3.9+ with streamdeck 0.9.6+ (3.9 needs 0.9.6 exactly - 0.9.7/0.9.8 break on 3.9).

POST /set    {"key":0,"page":0,"label":"Hi","color":"#30a46c","icon":"x.png","badge":3,"dim":false}
POST /clear  {}                                          drop live state, back to profile defaults
POST /ask    {"prompt":"Run rm?","info":"Bash","options":[{"label":"OK","color":"#30a46c"},...],"timeout":60}
             blocks until a key is pressed (one ask at a time; a second gets {"busy":true})
POST /screen {"text":"Deploy done","seconds":10}       info bar message, then back to clock
POST /timer  {"seconds":1500,"label":"Focus"} | {"cancel":true}   countdown on the info bar
POST /page   {"page":1}
GET  /state                                              connection, page, timer, last presses
Keys: "cmd" runs on press; "down"/"up" run on press/release (hold keys). Touch keys 8/9 flip pages.
"""
import base64
import io
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.dirname(os.path.abspath(__file__))

# Bundled dylibs (the portable kit ships lib/ so no Homebrew is needed). Pillow dlopen()s fribidi by bare
# name when ImageFont is imported, and macOS also searches the CWD for bare names - so import it from inside
# lib/. Preloading by path does NOT work (different install name). Without fribidi, Hebrew renders reversed.
_LIB = os.path.join(HOME, "lib")
_cwd = os.getcwd()
if os.path.isdir(_LIB):
    os.chdir(_LIB)
from PIL import Image, ImageDraw, ImageEnhance, ImageFont  # noqa: E402
os.chdir(_cwd)
from StreamDeck.DeviceManager import DeviceManager  # noqa: E402
from StreamDeck.ImageHelpers import PILHelper  # noqa: E402

if os.path.exists(os.path.join(_LIB, "libhidapi.dylib")):
    # The library falls back to <HOMEBREW_PREFIX>/lib/libhidapi.dylib; point that at our folder so it loads
    # the bundled copy itself (setting HIDAPI_INSTANCE directly skips its ctypes signatures -> crashes).
    from StreamDeck.Transport.LibUSBHIDAPI import LibUSBHIDAPI  # noqa: E402
    LibUSBHIDAPI.Library.HOMEBREW_PREFIX = HOME

PORT = 8885
ICON_DIR = os.path.join(HOME, "icons")
# Has Hebrew AND Latin (ArialHB/SFHebrew lack Latin letters -> tofu boxes); Pillow's raqm handles RTL
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
TOUCH_PREV, TOUCH_NEXT = 8, 9
SPEC_KEYS = ("label", "color", "icon", "badge", "dim", "sub")


PROFILE = os.path.join(HOME, "profile.json")
NOTIFY_LABEL = os.environ.get("STREAMDECK_NOTIFY_LABEL", "streamdeck-agents.notify")
DEFAULT_TOUCH = [[110, 110, 120], [0, 110, 255], [170, 60, 255], [255, 140, 0], [0, 180, 120]]


def load_profile():
    """Commands may use {home}, {actions}, and {page}/{key} = where the key currently sits
    (so a key that repaints itself, like Record, keeps working after being moved)."""
    with open(PROFILE) as f:
        p = json.load(f)
    base = {"home": HOME, "actions": os.path.join(HOME, "actions")}
    pages = []
    for pi, pg in enumerate(p["pages"]):
        keys = {}
        for k, spec in pg["keys"].items():
            spec = dict(spec)
            for c in ("cmd", "down", "up"):
                if c in spec:
                    spec[c] = spec[c].format(page=pi, key=int(k), **base)
            keys[int(k)] = spec
        pages.append({"name": pg["name"], "keys": keys})
    colors = [tuple(c) for c in p.get("touch_colors", DEFAULT_TOUCH)]
    return pages, colors, p.get("page_idle", 30), p.get("brightness", 100)


PAGES, TOUCH_COLORS, PAGE_IDLE, BRIGHTNESS = load_profile()
_fonts = {}


def font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(FONT_PATH, size)
    return _fonts[size]


lock = threading.RLock()
deck = None                  # the open device, or None while unplugged
page = 0
live = [{} for _ in PAGES]   # page -> key -> spec overrides pushed over HTTP (/set)
asking = None                # active /ask: {"queue": Queue, "keys": {key: index}, "labels": {...}}
recent = []
last_press = 0.0
screen_msg = None            # (text, until_ts) overrides the clock while active
timer = None                 # (label, end_ts) countdown on the bar
redraw_bar = threading.Event()

# Sessions page: built on the fly from state/claude/*.json (hook_claude.py) - shown only while sessions exist
SESSIONS = {"name": "Sessions", "keys": {}}
SESSION_COLORS = {"needs": "#f76b15", "done": "#30a46c", "working": "#0091ff"}
SESSION_TOUCH = (255, 120, 0)
# Control overlay: an agent's Pause/Continue/Stop keys over some slots, on every page (mcp deck_controls_*)
overlay = None               # {"title", "keys": {k: {"label", "color"}}, "paused": bool}
events = []                  # presses on overlay keys: {"id", "label", "ts"}
event_seq = 0


def pages():
    return PAGES + ([SESSIONS] if SESSIONS["keys"] else [])


def spec_for(p, k):
    if overlay and k in overlay["keys"]:
        o = overlay["keys"][k]
        paused = overlay.get("paused") and o["label"] == "Pause"
        return {"label": "Paused" if paused else o["label"], "color": "#ffb224" if paused else o.get("color", "#30363d"),
                "sub": o.get("sub")}
    pg = pages()
    conf = pg[p]["keys"].get(k) if p < len(pg) else None
    lv = live[p] if p < len(live) else {}
    if conf is None and k not in lv:
        return None
    s = {"label": "", "color": "#30363d"}
    s.update({x: conf[x] for x in SPEC_KEYS if conf and x in conf})
    s.update(lv.get(k, {}))
    return s


def device_call(fn):
    """Run a device write; a failure means it was unplugged - drop it so device_loop reconnects."""
    global deck
    with lock:
        d = deck
        if d is None:
            return
        try:
            fn(d)
        except Exception:
            try:
                d.close()
            except Exception:
                pass
            deck = None


# ---------- info bar ----------

def draw_screen(lines):
    """lines: list of (text, size, color). Stacked and centered on the 248x58 bar."""
    d0 = deck
    if d0 is None:
        return
    img = PILHelper.create_screen_image(d0)
    d = ImageDraw.Draw(img)
    fonts = [font(s) for _, s, _ in lines]
    total = sum(f.size for f in fonts) + 4 * (len(lines) - 1)
    y = (img.height - total) / 2
    for (text, _, color), f in zip(lines, fonts):
        while d.textlength(text, font=f) > img.width - 8 and len(text) > 1:
            text = text[:-2] + "…"
        d.text((img.width / 2, y), text, font=f, fill=color, anchor="mt")
        y += f.size + 4
    native = PILHelper.to_native_screen_format(d0, img)
    device_call(lambda dv: dv.set_screen_image(native))


def show_message(text, seconds):
    global screen_msg
    screen_msg = (text, time.time() + seconds)
    draw_screen([(text, 18 if len(text) <= 22 else 13, "white")])


def show_progress(label, frac, seconds):
    """Label + a real progress bar on the info bar (held like a message)."""
    global screen_msg
    screen_msg = (label, time.time() + seconds)
    d0 = deck
    if d0 is None:
        return
    frac = max(0.0, min(1.0, frac))
    img = PILHelper.create_screen_image(d0)
    d = ImageDraw.Draw(img)
    f = font(14)
    while d.textlength(label, font=f) > img.width - 10 and len(label) > 1:
        label = label[:-2] + "…"
    d.text((img.width / 2, 8), label, font=f, fill="white", anchor="mt")
    x0, y0, x1, y1 = 14, 34, img.width - 14, 46
    d.rounded_rectangle([x0, y0, x1, y1], radius=6, fill="#30363d")
    if frac > 0:
        d.rounded_rectangle([x0, y0, x0 + max(12, int((x1 - x0) * frac)), y1], radius=6,
                            fill="#30a46c" if frac >= 1 else "#0091ff")
    native = PILHelper.to_native_screen_format(d0, img)
    device_call(lambda dv: dv.set_screen_image(native))


def clock_loop():
    global screen_msg, timer
    shown = None
    while True:
        if redraw_bar.is_set():
            redraw_bar.clear()
            shown = None
        now = time.time()
        if screen_msg and now >= screen_msg[1]:
            screen_msg, shown = None, None
        if timer and now >= timer[1]:
            label = timer[0]
            timer, shown = None, None
            show_message(f"{label} done ✓", 30)
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        if deck is None:
            shown = None
        elif asking is not None or screen_msg is not None:
            pass
        elif timer:
            left = int(timer[1] - now)
            view = (f"{left // 60:02d}:{left % 60:02d}", timer[0])
            if view != shown:
                draw_screen([(view[0], 26, "#ffb224"), (view[1], 13, "#8b949e")])
                shown = view
        else:
            n = len(pages())
            dots = " ".join("●" if i == page else "○" for i in range(n)) if n > 1 else ""
            view = (time.strftime("%H:%M"), f"{time.strftime('%a %d %b')}   {dots}".rstrip())
            if view != shown:
                draw_screen([(view[0], 26, "white"), (view[1], 13, "#8b949e")])
                shown = view
        if page != 0 and asking is None and now - last_press > PAGE_IDLE:
            flip(-page)                        # wander back to the first page
            shown = None
        time.sleep(0.5)


# ---------- keys ----------

_icons = {}


def load_icon(path, size):
    """PNG from icons/ (or absolute path), fitted into size x size; cached by mtime."""
    p = path if os.path.isabs(path) else os.path.join(ICON_DIR, path)
    key = (p, size, os.path.getmtime(p))
    if key not in _icons:
        im = Image.open(p).convert("RGBA")
        im.thumbnail((size, size), Image.LANCZOS)
        _icons[key] = im
    return _icons[key]


def render(d0, label, color, pressed=False, icon=None, badge=0, dim=False, sub=None):
    img = PILHelper.create_image(d0)
    d = ImageDraw.Draw(img)
    if icon and os.path.exists(icon if os.path.isabs(icon) else os.path.join(ICON_DIR, icon)):
        d.rectangle([0, 0, img.width, img.height], fill="#2c2c2e")
        im = load_icon(icon, 84 if not pressed else 72)
        if dim:
            im = Image.composite(ImageEnhance.Brightness(im).enhance(0.85), im, im)
        img.paste(im, ((img.width - im.width) // 2, (img.height - im.height) // 2), im)
        if badge:
            txt = str(badge) if isinstance(badge, str) or badge < 100 else "99+"
            f = font(18)
            w = max(30, int(d.textlength(txt, font=f)) + 14)
            box = [img.width - w - 2, 2, img.width - 2, 32]
            d.rounded_rectangle(box, radius=15, fill="#ff3b30", outline="black", width=2)
            d.text(((box[0] + box[2]) / 2, 17), txt, font=f, fill="white", anchor="mm")
    else:
        d.rectangle([0, 0, img.width, img.height], fill="white" if pressed else color)
        f = font(20 if len(label) <= 6 else 14)
        while d.textlength(label, font=f) > img.width - 8 and len(label) > 1:
            label = label[:-2] + "…"
        d.text((img.width / 2, img.height * (0.4 if sub else 0.5)), label, font=f,
               fill="black" if pressed else "white", anchor="mm")
        if sub:
            d.text((img.width / 2, img.height * 0.72), sub, font=font(12),
                   fill="#333333" if pressed else "#f0f0f0", anchor="mm")
    return PILHelper.to_native_key_format(d0, img)


def paint(key, label, color, pressed=False, icon=None, badge=0, dim=False, sub=None):
    d0 = deck
    if d0 is not None:
        native = render(d0, label, color, pressed, icon, badge, dim, sub)
        device_call(lambda dv: dv.set_key_image(key, native))


def restore_page():
    d0 = deck
    if d0 is None:
        return
    for k in range(d0.key_count()):
        s = spec_for(page, k)
        if s:
            paint(k, **s)
        else:
            device_call(lambda dv, k=k: dv.set_key_image(k, None))
    color = SESSION_TOUCH if page >= len(PAGES) else TOUCH_COLORS[page % len(TOUCH_COLORS)]
    for t in (TOUCH_PREV, TOUCH_NEXT):
        device_call(lambda dv, t=t: dv.set_key_color(t, *color))


def reload_profile():
    """Re-read profile.json (after a dashboard save) and redraw - no restart."""
    global PAGES, TOUCH_COLORS, PAGE_IDLE, BRIGHTNESS, live, page
    PAGES, TOUCH_COLORS, PAGE_IDLE, BRIGHTNESS = load_profile()
    live = [{} for _ in PAGES]               # positions changed: live badges come back from notify.py
    page = min(page, len(PAGES) - 1)
    device_call(lambda dv: dv.set_brightness(BRIGHTNESS))
    restore_page()
    redraw_bar.set()
    # notify.py maps features to keys at startup - restart it so badges follow moved keys
    subprocess.Popen(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{NOTIFY_LABEL}"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def flip(delta):
    global page, last_press
    last_press = time.time()                 # a switch from voice/API counts as activity (else idle-return fires at once)
    pg = pages()
    page = (page + delta) % len(pg)
    if asking is None:
        restore_page()
        if len(pg) > 1:
            show_message(f"{pg[page]['name']}  ·  {page + 1}/{len(pg)}", 1.5)


def run(cmd):
    if cmd:
        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# Double press: keys that open an app quit it on a double press (or any key with an explicit "double" command).
# Only those keys wait DOUBLE_WINDOW before running their single-press command; every other key stays instant.
DOUBLE_WINDOW = 0.33
_pending = {}                # key -> Timer for a single press that may still become a double


def double_cmd(conf):
    if conf.get("double"):
        return conf["double"]
    m = re.fullmatch(r"open -(a|b) (['\"]?)(.+?)\2", (conf.get("cmd") or "").strip())
    if m:
        return f"{os.path.join(HOME, 'actions', 'quit.sh')} {shlex.quote(m.group(3))}"
    u = re.search(r"https?://([^/\s'\";]+)", conf.get("cmd") or "")      # link key -> close that site's tab
    if (conf.get("cmd") or "").startswith("open ") and u:
        return f"{os.path.join(HOME, 'actions', 'closetab.sh')} {shlex.quote(u.group(1))}"
    return None


_last_run = {}               # key -> time its command last ran (keys without a double action)
REPEAT_GUARD = 0.4           # a second press this soon on such a key is a slip, not "open it twice"


def press(key, conf):
    single, double = conf.get("cmd"), double_cmd(conf)
    label = conf.get("label", key)
    if not double:
        now = time.time()
        if now - _last_run.get(key, 0) < REPEAT_GUARD:
            print(f"press {label}: repeat ignored", flush=True)
            return
        _last_run[key] = now
        return run(single)
    t = _pending.pop(key, None)
    if t:                                   # second press inside the window: it's a double
        t.cancel()
        print(f"press {label}: double -> close", flush=True)
        return run(double)

    def fire():
        _pending.pop(key, None)
        print(f"press {label}: single", flush=True)
        run(single)
    t = threading.Timer(DOUBLE_WINDOW, fire)
    _pending[key] = t
    t.start()


def on_key(_deck, key, state):
    global last_press
    last_press = time.time()
    recent.append((time.time(), key, state))
    del recent[:-20]
    a = asking
    if a is not None and key in a["keys"]:
        if state:
            label, color = a["labels"][key]
            paint(key, label, color, pressed=True)
        else:
            a["queue"].put(a["keys"][key])
        return
    if key in (TOUCH_PREV, TOUCH_NEXT):
        if state and len(pages()) > 1:
            flip(-1 if key == TOUCH_PREV else 1)
        return
    if a is not None:
        return
    s = spec_for(page, key)
    if s:
        paint(key, **s, pressed=state)
    if overlay and key in overlay["keys"]:
        if not state:
            overlay_press(overlay["keys"][key]["label"])
        return
    pg = pages()
    conf = pg[page]["keys"].get(key, {}) if page < len(pg) else {}
    if state:
        if conf.get("down"):
            run(conf["down"])
        else:
            press(key, conf)
    else:
        run(conf.get("up"))


# ---------- Claude sessions page ----------

SESS_DIR = os.path.join(HOME, "state", "claude")
APP_SESSIONS = os.path.expanduser("~/Library/Application Support/Claude/claude-code-sessions")
_titles = {}                 # cli session id -> title from the Claude app (sidebar name)


def session_title(sid):
    if sid not in _titles:
        _titles[sid] = None
        for root, _, files in os.walk(APP_SESSIONS):
            for f in files:
                if f.startswith("local_") and f.endswith(".json"):
                    try:
                        d = json.load(open(os.path.join(root, f)))
                    except Exception:
                        continue
                    if d.get("cliSessionId"):
                        _titles.setdefault(d["cliSessionId"], d.get("title"))
                        if d["cliSessionId"] == sid and d.get("title"):
                            _titles[sid] = d["title"]
    return _titles.get(sid)


def sessions_loop():
    global page
    shown = None
    rank = {"needs": 0, "done": 1, "working": 2}
    sub = {"needs": "needs you", "done": "✓ done", "working": "working…"}
    while True:
        items = []
        for f in os.listdir(SESS_DIR) if os.path.isdir(SESS_DIR) else []:
            try:
                e = json.load(open(os.path.join(SESS_DIR, f)))
            except Exception:
                continue
            if e.get("status") == "working" and time.time() - e.get("ts", 0) > 6 * 3600:
                continue                      # a crashed session never sent SessionEnd
            if e.get("status") in rank:
                items.append(e)
        items.sort(key=lambda e: (rank[e["status"]], -e.get("ts", 0)))
        keys = {}
        for i, e in enumerate(items[:8]):
            keys[i] = {"label": session_title(e["session"]) or e.get("project", "?"), "sub": sub[e["status"]],
                       "color": SESSION_COLORS[e["status"]],
                       "cmd": f"{os.path.join(HOME, 'actions', 'claude_jump.sh')} {e['session']}"}
        sig = json.dumps(keys, sort_keys=True)
        if sig != shown:
            shown = sig
            on_sessions_page = page >= len(PAGES)
            SESSIONS["keys"] = keys
            if on_sessions_page and not keys:
                page = 0
            if asking is None and (on_sessions_page or not keys):
                restore_page()
            redraw_bar.set()
        time.sleep(2)


# ---------- control overlay ----------

def overlay_press(label):
    global event_seq
    event_seq += 1
    events.append({"id": event_seq, "label": label, "ts": time.time()})
    del events[:-50]
    if overlay:
        if label == "Pause":
            overlay["paused"] = True
            show_message(f"⏸ {overlay['title']} · paused", 3600)
        elif label in ("Continue", "Resume"):
            overlay["paused"] = False
            show_message(f"▶ {overlay['title']}", 4)
        elif label == "Stop":
            show_message(f"⏹ {overlay['title']} · stopping…", 30)
    restore_page()


# ---------- device lifecycle ----------

def connect():
    global deck
    for d in DeviceManager().enumerate():
        if not d.is_visual():
            continue
        d.open()
        d.reset()
        d.set_brightness(BRIGHTNESS)
        with lock:
            deck = d
        restore_page()
        redraw_bar.set()
        d.set_key_callback(on_key)
        print(f"connected: {d.deck_type()} {d.get_serial_number()}", flush=True)
        return True
    return False


def device_loop():
    """Keep a device open: connect when one appears, notice when it is unplugged."""
    global deck
    waiting_logged = False
    while True:
        d = deck
        if d is not None:
            try:
                ok = d.connected()
            except Exception:
                ok = False
            if not ok:
                print("disconnected", flush=True)
                with lock:
                    try:
                        d.close()
                    except Exception:
                        pass
                    deck = None
        if deck is None:
            try:
                if connect():
                    waiting_logged = False
                elif not waiting_logged:
                    print("waiting for a Stream Deck…", flush=True)
                    waiting_logged = True
            except Exception as e:
                print(f"connect failed: {e!r}", flush=True)
        time.sleep(2)


# ---------- ask ----------

ask_gate = threading.Lock()


def do_ask(body):
    if deck is None:
        return {"choice": None, "label": None, "offline": True}
    if not ask_gate.acquire(blocking=False):   # one question at a time; caller falls back
        return {"choice": None, "label": None, "busy": True}
    try:
        return _ask(body)
    finally:
        ask_gate.release()


def _ask(body):
    global asking
    n = deck.key_count()
    opts = body["options"][: n - 1]
    start = n - len(opts)                      # options fill the last N keys
    with lock:
        for k in range(n):
            device_call(lambda dv, k=k: dv.set_key_image(k, None))
        if body.get("info"):                   # key 0 = non-interactive context label
            paint(0, str(body["info"])[:10], "#30363d")
        if body.get("prompt"):                 # info bar shows what is being asked
            draw_screen([(str(body["prompt"]), 13, "white")])
        labels = {}
        for i, o in enumerate(opts):
            labels[start + i] = (o["label"], o.get("color", "#0091ff"))
            paint(start + i, *labels[start + i])
        asking = {"queue": queue.Queue(), "keys": {start + i: i for i in range(len(opts))}, "labels": labels}
        q = asking["queue"]
    try:
        idx = q.get(timeout=body.get("timeout", 60))
        return {"choice": idx, "label": opts[idx]["label"]}
    except queue.Empty:
        return {"choice": None, "label": None, "timeout": True}
    finally:
        asking = None
        restore_page()
        redraw_bar.set()


# ---------- dashboard ----------

DASH_DIR = os.path.join(HOME, "dashboard")
DASH_TOKEN = secrets.token_urlsafe(24)       # injected into the served page; required for writes
ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".png": "image/png"}


def icon_list():
    return sorted(f for f in os.listdir(ICON_DIR) if f.endswith(".png"))


def save_icon(name, data_url):
    """Uploaded image -> square PNG with the same transparent margin as the built-in icons."""
    raw = base64.b64decode(data_url.split(",", 1)[-1])
    return save_icon_image(Image.open(io.BytesIO(raw)).convert("RGBA"), name)


APPICON = os.path.join(HOME, "bin", "appicon")          # tiny Swift tool: an app's real icon as PNG


def app_icon(app, name=None):
    if not os.path.exists(APPICON):
        raise RuntimeError("app icon tool not built (bin/appicon) - run tools/build_native.sh")
    tmp = os.path.join(HOME, "state", "appicon.png")
    r = subprocess.run([APPICON, app, tmp], capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not os.path.exists(tmp):
        raise ValueError(f"app '{app}' not found on this Mac")
    try:
        return save_icon_image(Image.open(tmp).convert("RGBA"), name or app)
    finally:
        os.remove(tmp)


def save_icon_image(im, name):
    bbox = im.getchannel("A").getbbox()
    if bbox:
        im = im.crop(bbox)
    side = max(im.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2), im)
    pad = int(side * 1.25)
    out = Image.new("RGBA", (pad, pad), (0, 0, 0, 0))
    out.paste(sq, ((pad - side) // 2, (pad - side) // 2), sq)
    if pad > 640:
        out = out.resize((640, 640), Image.LANCZOS)
    stem = "".join(c for c in os.path.splitext(name)[0].lower() if c.isalnum() or c in "-_")[:40] or "icon"
    fname, n = f"{stem}.png", 2
    while os.path.exists(os.path.join(ICON_DIR, fname)):
        fname, n = f"{stem}-{n}.png", n + 1
    out.save(os.path.join(ICON_DIR, fname))
    return fname


SFSYMBOL = os.path.join(HOME, "bin", "sfsymbol")       # tiny Swift tool: renders an SF Symbol to PNG


def symbol_icon(symbol, color, name=None):
    """An icon in the house style: rounded gradient tile + white SF Symbol (same as the built-in ones)."""
    if not os.path.exists(SFSYMBOL):
        raise RuntimeError("SF Symbol renderer not built (bin/sfsymbol) - run tools/build_native.sh")
    tmp = os.path.join(HOME, "state", "sym.png")
    r = subprocess.run([SFSYMBOL, symbol, tmp], capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not os.path.exists(tmp):
        raise ValueError(f"unknown SF Symbol '{symbol}'")
    S = 512
    top = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    bottom = tuple(max(0, int(c * 0.78)) for c in top)
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.225), fill=255)
    tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    tile.paste(grad, (0, 0), mask)
    g = Image.open(tmp).convert("RGBA")
    g = g.crop(g.getbbox())
    g.thumbnail((int(S * 0.56), int(S * 0.56)), Image.LANCZOS)
    tile.paste(g, ((S - g.width) // 2, (S - g.height) // 2), g)
    P = int(S * 1.25)
    out = Image.new("RGBA", (P, P), (0, 0, 0, 0))
    out.paste(tile, ((P - S) // 2, (P - S) // 2), tile)
    stem = "".join(c for c in (name or symbol).lower() if c.isalnum() or c in "-_.").replace(".", "-")[:40]
    fname, n = f"{stem}.png", 2
    while os.path.exists(os.path.join(ICON_DIR, fname)):
        fname, n = f"{stem}-{n}.png", n + 1
    out.save(os.path.join(ICON_DIR, fname))
    os.remove(tmp)
    return fname


def save_profile(p):
    """Validate, back up the old one (state/profile-backups, last 20), write, reload."""
    if not isinstance(p.get("pages"), list) or not p["pages"]:
        raise ValueError("profile needs at least one page")
    for pg in p["pages"]:
        if not isinstance(pg.get("name"), str) or not isinstance(pg.get("keys"), dict):
            raise ValueError("each page needs a name and keys")
        for k in pg["keys"]:
            if not (0 <= int(k) <= 7):
                raise ValueError(f"key index {k} out of range 0-7")
    bdir = os.path.join(HOME, "state", "profile-backups")
    os.makedirs(bdir, exist_ok=True)
    shutil.copy(PROFILE, os.path.join(bdir, time.strftime("profile-%Y%m%d-%H%M%S.json")))
    for old in sorted(os.listdir(bdir))[:-20]:
        os.remove(os.path.join(bdir, old))
    tmp = PROFILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(p, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PROFILE)
    reload_profile()


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, inject_token=False):
        if not os.path.isfile(path):
            return self._send({"error": "not found"}, 404)
        data = open(path, "rb").read()
        if inject_token:
            data = data.replace(b"__DECK_TOKEN__", DASH_TOKEN.encode())
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(os.path.splitext(path)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _dashboard_write_ok(self):
        """Writes that can change commands must come from the dashboard page itself, not another site."""
        return (self.headers.get("Origin") in ALLOWED_ORIGINS
                and secrets.compare_digest(self.headers.get("X-Deck-Token", ""), DASH_TOKEN))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/dashboard", "/dashboard/"):
            return self._file(os.path.join(DASH_DIR, "index.html"), inject_token=True)
        if path.startswith("/dashboard/"):
            name = os.path.basename(path)
            return self._file(os.path.join(DASH_DIR, name))
        if path.startswith("/icons/"):
            return self._file(os.path.join(ICON_DIR, os.path.basename(path)))
        if path == "/events":
            after = int((self.path.split("after=", 1)[1:] or ["0"])[0].split("&")[0] or 0)
            return self._send({"events": [e for e in events if e["id"] > after], "last": event_seq,
                               "paused": bool(overlay and overlay.get("paused")), "overlay": bool(overlay)})
        if path == "/api/token":
            # fresh token for an open dashboard after a service restart; other sites can't read this
            # response (no CORS headers) and their writes fail the Origin check anyway
            return self._send({"token": DASH_TOKEN})
        if path == "/api/profile":
            with open(PROFILE) as f:
                return self._send({"profile": json.load(f), "icons": icon_list(), "page": page,
                                   "connected": deck is not None})
        if self.path == "/state":
            d = deck
            self._send({"connected": d is not None,
                        "type": d.deck_type() if d else None, "keys": d.key_count() if d else None,
                        "page": page, "pages": [p["name"] for p in pages()],
                        "timer": {"label": timer[0], "left": int(timer[1] - time.time())} if timer else None,
                        "recent": recent[-5:]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        global timer, overlay
        n = int(self.headers.get("Content-Length", 0))
        if n > 15 * 1024 * 1024:
            return self._send({"error": "too large"}, 413)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if self.path.startswith("/api/"):
                if not self._dashboard_write_ok():
                    origin = self.headers.get("Origin")
                    why = "bad token" if origin in ALLOWED_ORIGINS else f"origin {origin} not allowed"
                    print(f"dashboard write refused: {why}", flush=True)
                    return self._send({"error": f"forbidden ({why})"}, 403)
                if self.path == "/api/profile":
                    save_profile(body["profile"])
                    return self._send({"ok": True})
                if self.path == "/api/icon_app":
                    return self._send({"ok": True, "icon": app_icon(body["app"], body.get("name"))})
                if self.path == "/api/icon_symbol":
                    return self._send({"ok": True, "icon": symbol_icon(body["symbol"], body.get("color", "#3d8bff"),
                                                                       body.get("name"))})
                if self.path == "/api/icon":
                    return self._send({"ok": True, "icon": save_icon(body.get("name", "icon"), body["data"]),
                                       "icons": icon_list()})
                return self._send({"error": "not found"}, 404)
            if self.path == "/set":
                p, k = int(body.get("page", 0)), int(body["key"])
                if body.get("clear"):                      # drop the live override -> back to the profile
                    live[p].pop(k, None)
                else:
                    live[p][k] = {x: body[x] for x in SPEC_KEYS if x in body}
                if p == page and asking is None:
                    paint(k, **spec_for(p, k))
                self._send({"ok": True})
            elif self.path == "/clear":
                for d in live:
                    d.clear()
                restore_page()
                self._send({"ok": True})
            elif self.path == "/screen":
                if "progress" in body:
                    show_progress(str(body.get("text", "")), float(body["progress"]), float(body.get("seconds", 600)))
                else:
                    show_message(str(body.get("text", "")), float(body.get("seconds", 10)))
                self._send({"ok": True})
            elif self.path == "/timer":
                timer = None if body.get("cancel") else (str(body.get("label", "Timer")),
                                                         time.time() + float(body["seconds"]))
                self._send({"ok": True})
            elif self.path == "/overlay":
                if body.get("clear"):
                    overlay = None
                    if body.get("text"):
                        show_message(str(body["text"]), float(body.get("seconds", 10)))
                    else:
                        globals()["screen_msg"] = None
                        redraw_bar.set()
                else:
                    overlay = {"title": str(body.get("title", "Task"))[:40], "paused": False,
                               "keys": {int(k): v for k, v in body["keys"].items()}}
                    show_message(f"▶ {overlay['title']}", float(body.get("seconds", 8)))
                restore_page()
                self._send({"ok": True, "last": event_seq})
            elif self.path == "/page":
                flip(int(body["page"]) - page)
                self._send({"ok": True, "page": page})
            elif self.path == "/ask":
                self._send(do_ask(body))
            else:
                self._send({"error": "not found"}, 404)
        except Exception as e:
            self._send({"error": str(e)}, 400)


class Server(ThreadingHTTPServer):
    request_queue_size = 128     # default 5 drops connections when the dashboard loads ~25 icons at once
    daemon_threads = True

    def server_bind(self):  # skip HTTPServer's getfqdn(), which can hang
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # restart right after a crash
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name, self.server_port = "localhost", self.server_address[1]


def main():
    threading.Thread(target=device_loop, daemon=True).start()
    threading.Thread(target=clock_loop, daemon=True).start()
    threading.Thread(target=sessions_loop, daemon=True).start()
    print(f"deck daemon on http://127.0.0.1:{PORT} ({len(PAGES)} page(s))", flush=True)
    try:
        Server(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if deck is not None:
            deck.reset()
            deck.close()


if __name__ == "__main__":
    main()
