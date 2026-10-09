"""Feeds live state to the deck (deck.py) over HTTP. Which features run, and on which keys, comes from
the "notify" section of profile.json, so the same file serves home and work:

  "notify": {
    "mute":     {"id": "mute"},                              the key with "id": "mute" (or {"page": 0, "key": 0})
    "claude":   {"id": "claude"},                            waiting Claude Code sessions (hook_claude.py)
    "whatsapp": {"id": "whatsapp"},                          Dock badge + sender/text from WhatsApp's DB
    "gmail":    {"id": "gmail"},                             NEW Primary mail via `gws`
    "services": {"id": "status", "check": [["PC", "http://..."], ...]},
    "calendar": {"id": "calendar", "source": "google" | "eventkit", "filter": "", "hint": "Tools → 📅"}
  }
New arrivals also flash on the info bar. Stdlib only (runs on any macOS python3).
"""
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

HOME = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HOME, "state")
ICONS = os.path.join(HOME, "icons")
DECK = "http://127.0.0.1:8885"
TICK = 5
os.makedirs(STATE, exist_ok=True)
with open(os.path.join(HOME, "profile.json")) as _f:
    PROFILE = json.load(_f)
CONF = PROFILE.get("notify", {})


def post(path, body):
    try:
        req = urllib.request.Request(DECK + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def where(feature):
    """(page, key) of the key a feature paints: by the key's "id" (follows it when it's moved in the
    dashboard), or a fixed {"page","key"}. None if that key isn't on the deck any more."""
    c = CONF[feature]
    if "id" in c:
        for pi, pg in enumerate(PROFILE["pages"]):
            for k, spec in pg["keys"].items():
                if spec.get("id") == c["id"]:
                    return pi, int(k)
        return None
    return c.get("page", 0), c["key"]


def put(feature, **spec):
    """Paint the key configured for a feature."""
    pos = where(feature)
    if pos:
        post("/set", {"page": pos[0], "key": pos[1], **spec})


def log(msg):
    print(msg, flush=True)


# ---------- mute ----------

def muted():
    r = subprocess.run(["osascript", "-e", "output muted of (get volume settings)"], capture_output=True, text=True)
    return r.stdout.strip() == "true"


def paint_mute(m):
    put("mute", label="Mute", icon="muted.png" if m else "volume.png")


# ---------- Claude sessions waiting (files written by hook_claude.py) ----------

CLAUDE_DIR = os.path.join(STATE, "claude")


def claude_waiting():
    """Sessions that want attention: finished (done) or waiting for the user (needs). Working ones don't count."""
    out = []
    for f in os.listdir(CLAUDE_DIR) if os.path.isdir(CLAUDE_DIR) else []:
        try:
            e = json.load(open(os.path.join(CLAUDE_DIR, f)))
        except Exception:
            continue
        if e.get("status", "done") in ("done", "needs"):
            out.append(e)
    return sorted(out, key=lambda e: e.get("ts", 0))


def paint_claude(n):
    put("claude", label="Claude", icon="claude.png", badge=n, dim=not n)


# ---------- WhatsApp ----------

WA_NAME = "‎WhatsApp"            # the app registers with a leading LRM mark
WA_DB = os.path.expanduser("~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite")
WA_SQL = """
SELECT m.Z_PK, s.ZSESSIONTYPE, s.ZPARTNERNAME, COALESCE(g.ZCONTACTNAME, m.ZPUSHNAME), m.ZTEXT
FROM ZWAMESSAGE m
JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER
WHERE m.Z_PK > ? AND m.ZISFROMME = 0
ORDER BY m.Z_PK
"""


def wa_badge():
    """Unread count from the Dock badge; None if WhatsApp is not running."""
    asn = subprocess.run(["lsappinfo", "find", f"name={WA_NAME}"], capture_output=True, text=True).stdout.strip()
    if not asn:
        return None
    out = subprocess.run(["lsappinfo", "info", "-only", "StatusLabel", asn.split('"')[0].rstrip("-")],
                         capture_output=True, text=True).stdout
    m = re.search(r'"label"\s*=\s*"([^"]*)"', out)
    if not m or not m.group(1):
        return 0
    digits = re.sub(r"\D", "", m.group(1))
    return int(digits) if digits else 1     # "•" badge = something unread


def wa_new_messages(after_pk):
    """Incoming messages after after_pk from WhatsApp's local DB (needs Full Disk Access).
    Returns (max_pk, [text, ...]) or None if the DB can't be read."""
    try:
        c = sqlite3.connect(f"file:{WA_DB}?mode=ro", uri=True, timeout=2)
        try:
            if after_pk is None:
                return c.execute("SELECT max(Z_PK) FROM ZWAMESSAGE").fetchone()[0] or 0, []
            out, top = [], after_pk
            for pk, stype, chat, member, text in c.execute(WA_SQL, (after_pk,)):
                top = max(top, pk)
                body = " ".join((text or "📎").split())
                who = f"{member or '?'} @ {chat}" if stype == 1 else (chat or member or "?")
                out.append(f"{who}: {body}")
            return top, out
        finally:
            c.close()
    except sqlite3.Error:
        return None


def paint_wa(n):
    put("whatsapp", label="WhatsApp", icon="whatsapp.png", badge=n or 0, dim=not n)


# ---------- Gmail (gws CLI) ----------

SEEN_MARK = os.path.join(HOME, ".mail_seen")
MAIL_QUERY = "in:inbox is:unread category:primary"


def gws_mail(params, *path):
    r = subprocess.run(["gws", "gmail", "users", "messages", *path, "--params", json.dumps(params)],
                       capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout)


def unread_ids():
    d = gws_mail({"userId": "me", "q": MAIL_QUERY, "maxResults": 20}, "list")
    return [m["id"] for m in d.get("messages", [])]


def headline(mid):
    d = gws_mail({"userId": "me", "id": mid, "format": "metadata"}, "get")
    h = {x["name"]: x["value"] for x in d.get("payload", {}).get("headers", [])}
    sender = h.get("From", "?")
    sender = re.sub(r"\s*<[^>]+>", "", sender).strip('" ') or sender
    return f"{sender}: {h.get('Subject', '')}"


def paint_mail(n):
    put("gmail", label="Mail", icon="gmail.png", badge=n or 0, dim=not n)


# ---------- services ----------

def is_up(url):
    try:
        urllib.request.urlopen(url, timeout=4)
        return True
    except urllib.error.HTTPError:
        return True          # it answered - auth pages count as up
    except Exception:
        return False


# ---------- calendar ----------

MEETING_FILE = os.path.join(STATE, "next_meeting.json")
LINK_RE = re.compile(r"https://(?:[\w.-]*zoom\.us/j/|meet\.google\.com/|teams\.microsoft\.com/l/meetup-join/|teams\.live\.com/meet/)[^\s\"<>]+")
CAL_APP = os.path.join(HOME, "bin", "StreamDeck Calendar.app")


def meeting_link(e):
    if e.get("hangoutLink"):
        return e["hangoutLink"]
    for ep in e.get("conferenceData", {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return ep["uri"]
    m = LINK_RE.search(f"{e.get('location', '')} {e.get('description', '')}")
    return m.group(0) if m else None


def next_meeting_google():
    """Current or next timed event in the next 12h. Google's timeMin bounds the END time,
    so a meeting already in progress is included."""
    now = time.time()
    params = {"calendarId": "primary", "timeMin": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
              "timeMax": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 12 * 3600)),
              "maxResults": 20, "singleEvents": True, "orderBy": "startTime"}
    r = subprocess.run(["gws", "calendar", "events", "list", "--params", json.dumps(params)],
                       capture_output=True, text=True, timeout=30)
    for e in json.loads(r.stdout).get("items", []):
        st, en = e.get("start", {}).get("dateTime"), e.get("end", {}).get("dateTime")
        if st and en and e.get("status") != "cancelled":
            ts = lambda x: datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp()
            return {"summary": e.get("summary", "(no title)"), "start": ts(st), "end": ts(en),
                    "link": meeting_link(e)}
    return None


def next_meeting_eventkit(filt):
    """Same, from the macOS calendar store (Outlook/Exchange accounts added to macOS Calendar).
    Runs bin/StreamDeck Calendar.app through `open` so the Calendars permission belongs to that app."""
    out = os.path.join(STATE, "calnext.out")
    if os.path.exists(out):
        os.remove(out)
    args = ["open", "-W", "-g", "--stdout", out, "-a", CAL_APP] + (["--args", filt] if filt else [])
    subprocess.run(args, timeout=90, stderr=subprocess.DEVNULL)
    for _ in range(100):                   # `open -W` can return before a fast helper has written its line
        if os.path.exists(out) and open(out).read().strip():
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("calendar helper gave no output")
    d = json.loads(open(out).read().strip())
    if "error" in d:
        raise RuntimeError(d["error"])
    return d or None


# ---------- slow loop: services + calendar ----------

def slow_loop():
    last_down, cal, cal_next, alerted, cal_err = None, None, 0, set(), None
    while True:
        if "services" in CONF:
            res = [(name, is_up(url)) for name, url in CONF["services"]["check"]]
            down = [n for n, ok in res if not ok]
            with open(os.path.join(STATE, "status.txt"), "w") as f:
                f.write("  ·  ".join(f"{n} {'✓' if ok else '✗'}" for n, ok in res))
            put("services", label="Status", icon="status.png", badge=len(down), dim=False)
            if last_down is not None and set(down) - set(last_down):
                post("/screen", {"text": "⚠ Down: " + ", ".join(sorted(set(down) - set(last_down))), "seconds": 20})
            last_down = down

        if "calendar" in CONF:
            c = CONF["calendar"]
            if time.time() >= cal_next:
                cal_next = time.time() + 120
                try:
                    cal = (next_meeting_eventkit(c.get("filter", "")) if c.get("source") == "eventkit"
                           else next_meeting_google())
                    with open(MEETING_FILE, "w") as f:
                        json.dump(cal or {}, f, ensure_ascii=False)
                    if cal_err:
                        log("calendar: ok again")
                    cal_err = None
                except Exception as e:
                    if repr(e) != cal_err:
                        log(f"calendar failed: {e!r}")
                    cal_err = repr(e)
            mins = int((cal["start"] - time.time()) // 60) if cal else None
            put("calendar", label="Calendar", icon="calendar.png",
                badge=f"{mins}m" if mins is not None and 0 <= mins < 60 else 0, dim=False)
            if cal and mins is not None and mins <= 5 and (cal["summary"], cal["start"]) not in alerted:
                alerted.add((cal["summary"], cal["start"]))
                hint = f" · {c.get('hint', '📅')} to join" if cal.get("link") else ""
                post("/screen", {"text": f"📅 {'Now' if mins <= 0 else f'In {mins} min'}: {cal['summary']}{hint}",
                                 "seconds": 60})
                subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        time.sleep(60)


# ---------- main loop: fast features ----------

def main():
    log(f"notify: {', '.join(CONF) or 'nothing configured'}")
    if "services" in CONF or "calendar" in CONF:
        threading.Thread(target=slow_loop, daemon=True).start()
    claude_seen, claude_n, claude_n_ready = set(), -1, False
    wa_last, wa_pk, wa_db_ok = -1, None, None
    mail_new, mail_seen_ids, repaint_next = [], None, 0
    seen_mtime = os.path.getmtime(SEEN_MARK) if os.path.exists(SEEN_MARK) else 0
    mute_last = None
    while True:
        if "claude" in CONF:
            waiting = claude_waiting()
            if len(waiting) != claude_n:
                claude_n = len(waiting)
                paint_claude(claude_n)
            for e in waiting:
                k = (e.get("session"), e.get("ts"), e.get("status"))
                if k not in claude_seen:
                    claude_seen.add(k)
                    if claude_n_ready:                 # not for sessions that were already waiting at startup
                        post("/screen", {"text": f"Claude · {e.get('project', '?')}: {e.get('msg', 'done')}",
                                         "seconds": 12})
            claude_n_ready = True

        if "mute" in CONF:
            m = muted()
            if m != mute_last:
                paint_mute(m)
                mute_last = m

        if "whatsapp" in CONF:
            res = wa_new_messages(wa_pk)
            if (res is not None) != wa_db_ok:
                wa_db_ok = res is not None
                log("whatsapp db: " + ("readable, showing senders" if wa_db_ok else
                    "NOT readable (grant Full Disk Access to Python.app) - badge count only"))
            if res:
                wa_pk, msgs = res
                if msgs:
                    post("/screen", {"text": msgs[-1] if len(msgs) == 1 else f"{msgs[-1]}  (+{len(msgs) - 1})",
                                     "seconds": 15})
            n = wa_badge()
            if n != wa_last:
                paint_wa(n)
                if not wa_db_ok and n and wa_last is not None and wa_last >= 0 and n > wa_last:
                    post("/screen", {"text": f"WhatsApp · {n} unread", "seconds": 10})
                wa_last = n

        if "gmail" in CONF and os.path.exists(SEEN_MARK) and os.path.getmtime(SEEN_MARK) > seen_mtime:
            seen_mtime = os.path.getmtime(SEEN_MARK)
            mail_new = []
            paint_mail(0)

        if time.time() >= repaint_next:            # once a minute: repaint (deck may have restarted) + mail
            repaint_next = time.time() + 60
            if "whatsapp" in CONF:
                paint_wa(wa_last if wa_last != -1 else None)
            if "mute" in CONF:
                paint_mute(mute_last)
            if "claude" in CONF:
                paint_claude(claude_n)
            if "gmail" in CONF:
                paint_mail(len(mail_new))
                try:
                    ids = unread_ids()
                    if mail_seen_ids is None:          # first poll: baseline, don't flood
                        mail_seen_ids = set(ids)
                        log(f"mail baseline: {len(ids)} unread in Primary")
                    else:
                        fresh = [i for i in ids if i not in mail_seen_ids]
                        mail_seen_ids.update(ids)
                        if fresh:
                            mail_new += fresh
                            paint_mail(len(mail_new))
                            post("/screen", {"text": headline(fresh[0]), "seconds": 15})
                except Exception as e:
                    log(f"mail poll failed: {e!r}")
        time.sleep(TICK)


if __name__ == "__main__":
    main()
