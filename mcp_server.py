#!/usr/bin/env python3
"""MCP server (stdio) that lets AI agents use the Stream Deck Neo: show things on its display, ask the user a
question answered by pressing a key, show progress, light up keys, and add/change keys.
It only talks to the local deck service (http://127.0.0.1:8885). Stdlib only.

Register with Claude Code:  claude mcp add --scope user streamdeck -- <python3> ~/streamdeck/mcp_server.py
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request

DECK = "http://127.0.0.1:8885"
ORIGIN = "http://127.0.0.1:8885"            # profile writes go through the same guarded API as the dashboard
COLORS = ["#30a46c", "#0091ff", "#e5484d", "#f76b15", "#8e4ec6", "#12a594", "#ffb224"]

INSTRUCTIONS = """The user has a Stream Deck Neo on their desk (8 keys + a small 248x58 display), reachable via these tools.
Use it sparingly and only when it helps the user who is away from the screen or busy:
- deck_ask: for a real decision you need from the user mid-task (approve a risky step, pick between 2-7 options).
- deck_progress: for long tasks (>1 min) - update at meaningful steps, not every second; finish with done == total.
- deck_show: a short final result or heads-up (one line, ~40 chars fits).
- deck_controls_start + deck_check + deck_controls_end: for long multi-step tasks (several minutes, many files) -
  check between steps and obey Pause/Stop.
- deck_light / deck_add_key: only when the user asks for it.
Never spam the display. Keep text short; Hebrew is fine."""


def call(method, path, body=None, timeout=10, write=False):
    headers = {"Content-Type": "application/json"}
    if write:
        token = call("GET", "/api/token")["token"]
        headers.update({"Origin": ORIGIN, "X-Deck-Token": token})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(DECK + path, data, headers, method=method)
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except urllib.error.URLError:
        raise RuntimeError("The Stream Deck service isn't running on this Mac (127.0.0.1:8885).")


def layout():
    d = call("GET", "/api/profile")
    return d["profile"], d.get("connected", False), d.get("page", 0)


def find_key(profile, ref):
    """ref: a key's label or id (case-insensitive), or "page:key" like "1:3"."""
    ref = str(ref).strip()
    if ":" in ref and ref.replace(":", "").isdigit():
        p, k = (int(x) for x in ref.split(":"))
        return p, k
    for pi, pg in enumerate(profile["pages"]):
        for k, spec in pg["keys"].items():
            if ref.lower() in (str(spec.get("label", "")).lower(), str(spec.get("id", "")).lower()):
                return pi, int(k)
    raise ValueError(f"No key called '{ref}'. Use deck_layout to see the keys.")


# ---------- tools ----------

def t_layout(_):
    prof, connected, cur = layout()
    lines = [f"Device {'connected' if connected else 'NOT connected'}; showing page {cur}."]
    for pi, pg in enumerate(prof["pages"]):
        lines.append(f"Page {pi} '{pg['name']}':")
        for k in range(8):
            spec = pg["keys"].get(str(k))
            if spec:
                extra = f" (id {spec['id']})" if spec.get("id") else ""
                desc = f" - {spec['desc']}" if spec.get("desc") else ""
                lines.append(f"  key {k}: {spec.get('label', '')}{extra}{desc}")
            else:
                lines.append(f"  key {k}: (empty)")
    lines.append("Keys 0-3 are the top row, 4-7 the bottom row.")
    return "\n".join(lines)


def t_show(a):
    call("POST", "/screen", {"text": a["text"], "seconds": a.get("seconds", 10)})
    return "Shown on the display."


def t_progress(a):
    done, total = float(a["done"]), a.get("total")
    frac = done / float(total) if total else done / 100.0
    label = a.get("label", "")
    label = f"{label} · {int(done)}/{int(total)}" if total else f"{label} · {int(done)}%"
    finished = frac >= 1
    call("POST", "/screen", {"text": ("✓ " if finished else "") + label, "progress": frac,
                             "seconds": 20 if finished else 900})
    return "Progress shown." + (" Marked as finished." if finished else "")


def t_ask(a):
    opts = [str(o) for o in a["options"]][:7]
    if len(opts) < 2:
        raise ValueError("Give 2-7 options.")
    timeout = int(a.get("timeout_seconds", 120))
    r = call("POST", "/ask", {"prompt": a["question"], "info": a.get("context", "Claude")[:10],
                              "options": [{"label": o[:12], "color": COLORS[i % len(COLORS)]} for i, o in enumerate(opts)],
                              "timeout": timeout}, timeout=timeout + 15)
    if r.get("offline"):
        return "The Stream Deck isn't connected - ask the user in the chat instead."
    if r.get("busy"):
        return "The Stream Deck is already showing another question - ask the user in the chat instead."
    if r.get("choice") is None:
        return f"No answer within {timeout}s - the user may be away. Ask in the chat or wait."
    return f"The user pressed: {opts[r['choice']]}"


def t_light(a):
    prof, _, _ = layout()
    p, k = find_key(prof, a["key"])
    if a.get("clear"):
        call("POST", "/set", {"page": p, "key": k, "clear": True})
        return f"Key {p}:{k} back to normal."
    spec = prof["pages"][p]["keys"].get(str(k), {})
    body = {"page": p, "key": k, "label": a.get("label") or spec.get("label", "")}
    if spec.get("icon"):
        body["icon"] = spec["icon"]
    if "badge" in a:
        body["badge"] = a["badge"]
    if "color" in a:                      # shows on text keys; icon keys keep their icon
        body["color"] = a["color"]
    call("POST", "/set", body)
    return f"Key {p}:{k} ('{body['label']}') updated. Use clear=true to reset it."


# Keys added from a chat may only open apps/links or run an existing action script. Anything else (any shell)
# must be entered by Ilan in the dashboard - so a prompt-injected agent can't plant a dangerous key.
SAFE_COMMANDS = [
    r"open -a (['\"])[^'\";|&`$<>]+\1",
    r"open -a [\w.\- ]+",
    r"open -b [\w.\-]+",
    r"open (['\"])https?://[^'\"\s$`\\]+\1",          # no $ ` \ even quoted: "..." still expands them
    r"open https?://[^\s'\";|&`$()<>]+",
    r"\{actions\}/[\w.\-]+\.sh( [\w.\-{}]+)*",
]


def safe_command(cmd):
    cmd = cmd.strip()
    if not any(re.fullmatch(p, cmd) for p in SAFE_COMMANDS):
        return False
    if cmd.startswith("{actions}/"):
        script = cmd.split()[0].replace("{actions}/", "")
        return os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "actions", script))
    return True


def t_add_key(a):
    if a.get("app") and not a.get("command"):
        a["command"] = f"open -a '{a['app']}'"
    if not a.get("command"):
        raise ValueError("Give a command, or app= to open an app.")
    if not safe_command(a["command"]):
        raise ValueError("From chat, keys may only open an app (open -a 'Name'), a link (open https://...), or run "
                         "an existing actions/ script. For any other command, Ilan adds it in the dashboard "
                         "(http://127.0.0.1:8885).")
    prof, _, _ = layout()
    pages = prof["pages"]
    if "page" in a and "key" in a:
        p, k = int(a["page"]), int(a["key"])
    else:
        want = [int(a["page"])] if "page" in a else range(len(pages))
        free = [(pi, kk) for pi in want for kk in range(8) if str(kk) not in pages[pi]["keys"]]
        if not free:
            raise ValueError("No free key slot - give page and key (and replace=true) to overwrite one.")
        p, k = free[0]
    if p == len(pages):
        pages.append({"name": a.get("page_name", f"Page {p + 1}"), "keys": {}})
        prof.setdefault("touch_colors", [[110, 110, 120], [0, 110, 255], [170, 60, 255], [255, 140, 0], [0, 180, 120]])
    if not (0 <= p < len(pages) and 0 <= k <= 7):
        raise ValueError("page/key out of range")
    old = pages[p]["keys"].get(str(k))
    if old and not a.get("replace"):
        raise ValueError(f"Page {p} key {k} is '{old.get('label')}' - pass replace=true to overwrite it.")
    spec = {"label": a["label"], "cmd": a["command"]}
    if a.get("description"):
        spec["desc"] = a["description"]
    if a.get("confirm"):
        spec["confirm"] = True
    m = re.fullmatch(r"open -a (['\"]?)(.+?)\1", a["command"].strip())
    app = a.get("app") or (m.group(2) if m and not a.get("sf_symbol") and not a.get("icon_file") else None)
    if app:
        r = call("POST", "/api/icon_app", {"app": app, "name": a["label"]}, write=True, timeout=40)
        if not r.get("ok"):
            raise ValueError(r.get("error", "app icon failed"))
        spec["icon"] = r["icon"]
    elif a.get("sf_symbol"):
        r = call("POST", "/api/icon_symbol", {"symbol": a["sf_symbol"], "color": a.get("color", "#3d8bff"),
                                              "name": a["label"]}, write=True, timeout=40)
        if not r.get("ok"):
            raise ValueError(r.get("error", "icon failed"))
        spec["icon"] = r["icon"]
    elif a.get("icon_file"):
        path = os.path.expanduser(a["icon_file"])
        data = "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()
        r = call("POST", "/api/icon", {"name": os.path.basename(path), "data": data}, write=True, timeout=40)
        if not r.get("ok"):
            raise ValueError(r.get("error", "icon upload failed"))
        spec["icon"] = r["icon"]
    elif old and old.get("icon") and a.get("replace"):
        spec["icon"] = old["icon"]
    pages[p]["keys"][str(k)] = spec
    r = call("POST", "/api/profile", {"profile": prof}, write=True, timeout=20)
    if not r.get("ok"):
        raise ValueError(r.get("error", "save failed"))
    return (f"Added '{a['label']}' on page {p} ('{pages[p]['name']}'), key {k} "
            f"({'top' if k < 4 else 'bottom'} row, #{k % 4 + 1} from the left). The deck updated; "
            f"the previous layout is backed up in state/profile-backups.")


# ---------- control keys for a running task ----------

CONTROL_KEYS = {"4": {"label": "Pause", "color": "#a86b00"}, "5": {"label": "Continue", "color": "#2f7d4f"},
                "7": {"label": "Stop", "color": "#c0392b"}}
_cursor = {"last": 0}


def t_controls_start(a):
    r = call("POST", "/overlay", {"title": a["task"], "keys": CONTROL_KEYS, "seconds": 6})
    _cursor["last"] = r.get("last", 0)
    return ("Control keys are on the deck (bottom row: Pause, Continue, Stop). Call deck_check between steps "
            "(every step or ~30 s), and deck_controls_end when you finish or stop.")


def t_check(a):
    import time
    r = call("GET", f"/events?after={_cursor['last']}")
    _cursor["last"] = r.get("last", _cursor["last"])
    labels = [e["label"] for e in r.get("events", [])]
    if "Stop" in labels:
        return "STOP: the user pressed Stop on the deck. Stop the task now, leave things in a safe state, " \
               "call deck_controls_end, and summarize what was done and what wasn't."
    if not r.get("paused"):
        return "No input from the deck - continue." if not labels else f"User pressed {labels[-1]} - continue."
    if not a.get("wait_if_paused", True):
        return "PAUSED: the user pressed Pause. Don't continue until they press Continue."
    limit, t0 = float(a.get("max_wait_seconds", 600)), time.time()
    while time.time() - t0 < limit:
        time.sleep(1)
        r = call("GET", f"/events?after={_cursor['last']}")
        _cursor["last"] = r.get("last", _cursor["last"])
        labels = [e["label"] for e in r.get("events", [])]
        if "Stop" in labels:
            return "STOP: while paused, the user pressed Stop. Stop now, call deck_controls_end, and summarize."
        if not r.get("paused"):
            return f"Resumed after {int(time.time() - t0)} s - continue."
    return f"Still paused after {int(limit)} s - the user may be away. Wait, or ask in the chat."


def t_controls_end(a):
    call("POST", "/overlay", {"clear": True, "text": a.get("summary", ""), "seconds": 15})
    return "Control keys removed."


def t_move_key(a):
    prof, _, _ = layout()
    p, k = find_key(prof, a["key"])
    tp, tk = int(a["to_page"]), int(a["to_key"])
    if not (0 <= tp < len(prof["pages"]) and 0 <= tk <= 7):
        raise ValueError("to_page/to_key out of range (keys 0-7; see deck_layout)")
    src, dst = prof["pages"][p]["keys"], prof["pages"][tp]["keys"]
    moving, other = src.pop(str(k)), dst.pop(str(tk), None)
    dst[str(tk)] = moving
    if other:
        src[str(k)] = other
    r = call("POST", "/api/profile", {"profile": prof}, write=True, timeout=20)
    if not r.get("ok"):
        raise ValueError(r.get("error", "save failed"))
    swap = f", swapped with '{other.get('label')}'" if other else ""
    return f"Moved '{moving.get('label')}' to page {tp} key {tk}{swap}."


def t_remove_key(a):
    prof, _, _ = layout()
    p, k = find_key(prof, a["key"])
    spec = prof["pages"][p]["keys"].pop(str(k))
    prof.setdefault("tray", []).append(spec)
    r = call("POST", "/api/profile", {"profile": prof}, write=True, timeout=20)
    if not r.get("ok"):
        raise ValueError(r.get("error", "save failed"))
    return f"Removed '{spec.get('label')}' from page {p} key {k} - it's in the dashboard's drawer, not deleted."


# ---------- temporary context buttons ----------

def t_buttons_start(a):
    labels = [str(b)[:12] for b in a["buttons"]][:8]
    if not labels:
        raise ValueError("give 1-8 buttons")
    slots = [4, 5, 6, 7, 0, 1, 2, 3][:len(labels)]          # bottom row first, keep the top row live if possible
    keys = {str(slot): {"label": lab, "color": COLORS[i % len(COLORS)]} for i, (slot, lab) in enumerate(zip(slots, labels))}
    r = call("POST", "/overlay", {"title": a["title"], "keys": keys, "seconds": 6})
    _cursor["last"] = r.get("last", 0)
    return f"Buttons on the deck: {', '.join(labels)}. Call deck_wait_press to get each press; deck_controls_end to remove them."


def t_wait_press(a):
    import time
    limit, t0 = float(a.get("timeout_seconds", 300)), time.time()
    while time.time() - t0 < limit:
        r = call("GET", f"/events?after={_cursor['last']}")
        if r.get("events"):
            e = r["events"][0]
            _cursor["last"] = e["id"]
            return f"The user pressed: {e['label']}"
        if not r.get("overlay"):
            return "The buttons are no longer on the deck."
        time.sleep(0.5)
    return f"No press within {int(limit)} s."


TOOLS = [
    {"name": "deck_controls_start", "handler": t_controls_start,
     "description": "Before a long multi-step task, put Pause / Continue / Stop keys on the deck so the user can "
                    "steer you from their desk. Then call deck_check between steps and deck_controls_end at the end.",
     "inputSchema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "short name shown on the display, e.g. 'Refactor auth'"}},
         "required": ["task"]}},
    {"name": "deck_check", "handler": t_check,
     "description": "Between steps of a controlled task: see whether the user pressed Pause/Stop on the deck. "
                    "If paused, this waits (up to max_wait_seconds) until they press Continue or Stop. "
                    "Obey the result: on STOP, stop immediately.",
     "inputSchema": {"type": "object", "properties": {
         "wait_if_paused": {"type": "boolean", "default": True},
         "max_wait_seconds": {"type": "number", "default": 600}}}},
    {"name": "deck_controls_end", "handler": t_controls_end,
     "description": "Remove the temporary keys (control keys or deck_buttons_start buttons); optionally show a one-line summary.",
     "inputSchema": {"type": "object", "properties": {"summary": {"type": "string"}}}},
    {"name": "deck_buttons_start", "handler": t_buttons_start,
     "description": "Show temporary task-specific buttons on the deck (1-8, e.g. a review: Approve / Changes / Next / "
                    "Skip). They sit on the bottom row first so the user's top row keeps working. Then call "
                    "deck_wait_press for each press, and deck_controls_end to remove them. The user's layout is untouched.",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string"}, "buttons": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8}},
         "required": ["title", "buttons"]}},
    {"name": "deck_wait_press", "handler": t_wait_press,
     "description": "Wait for the next press on the temporary buttons (deck_buttons_start). Returns its label.",
     "inputSchema": {"type": "object", "properties": {"timeout_seconds": {"type": "number", "default": 300}}}},
    {"name": "deck_move_key", "handler": t_move_key,
     "description": "Move a key to another position (swaps with whatever is there). key = label, id or 'page:key'.",
     "inputSchema": {"type": "object", "properties": {
         "key": {"type": "string"}, "to_page": {"type": "integer"}, "to_key": {"type": "integer"}},
         "required": ["key", "to_page", "to_key"]}},
    {"name": "deck_remove_key", "handler": t_remove_key,
     "description": "Take a key off the deck (kept in the dashboard's drawer, so it can be put back).",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "deck_layout", "handler": t_layout,
     "description": "See what's on the Stream Deck: every page and key (label, id, what it does) and free slots.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "deck_show", "handler": t_show,
     "description": "Show a short line on the Stream Deck display (~40 chars), then it returns to the clock.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}, "seconds": {"type": "number", "default": 10}}, "required": ["text"]}},
    {"name": "deck_progress", "handler": t_progress,
     "description": "Show progress of a long task on the display as a bar. Pass done/total (e.g. tests 34/120) or "
                    "done as a percent without total. When done reaches total it shows as finished.",
     "inputSchema": {"type": "object", "properties": {
         "label": {"type": "string", "description": "short, e.g. 'Tests' or 'Build'"},
         "done": {"type": "number"}, "total": {"type": "number"}}, "required": ["label", "done"]}},
    {"name": "deck_ask", "handler": t_ask,
     "description": "Ask the user a question on the Stream Deck and wait until they press an answer key. "
                    "The question appears on the display, each option (2-7, short - max 12 chars) on its own key. "
                    "Returns the chosen option, or says nobody answered.",
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string", "description": "short, fits the small display"},
         "options": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 7},
         "timeout_seconds": {"type": "integer", "default": 120},
         "context": {"type": "string", "description": "who asks, shown on a key (max 10 chars)", "default": "Claude"}},
         "required": ["question", "options"]}},
    {"name": "deck_light", "handler": t_light,
     "description": "Put a status on an existing key: a red badge text (e.g. '!', '3', 'OK') and/or a color for text "
                    "keys. clear=true resets the key. key = its label, id, or 'page:key'.",
     "inputSchema": {"type": "object", "properties": {
         "key": {"type": "string"}, "badge": {"type": "string"}, "color": {"type": "string", "description": "#rrggbb"},
         "label": {"type": "string"}, "clear": {"type": "boolean"}}, "required": ["key"]}},
    {"name": "deck_add_key", "handler": t_add_key,
     "description": "Add (or with replace=true overwrite) a key on the Stream Deck. Allowed commands from chat: open an "
                    "app (app='Figma' - its real icon is used automatically), open a link (command=\"open https://...\"), "
                    "or an existing {actions}/ script; anything else must be added in the dashboard. Other icons: an SF "
                    "Symbol in the house style (sf_symbol='paintbrush.fill', color='#a259ff') or a PNG path. "
                    "Without page/key it takes the first free slot. A page index one past the last creates a new page. "
                    "The description is used by voice control to know when to press it.",
     "inputSchema": {"type": "object", "properties": {
         "label": {"type": "string"}, "command": {"type": "string"}, "app": {"type": "string"},
         "description": {"type": "string"}, "page": {"type": "integer"}, "key": {"type": "integer"},
         "sf_symbol": {"type": "string"}, "color": {"type": "string"}, "icon_file": {"type": "string"},
         "confirm": {"type": "boolean", "description": "voice control asks Yes/No on the deck before running it"},
         "replace": {"type": "boolean"}, "page_name": {"type": "string"}},
         "required": ["label"]}},
]
BY_NAME = {t["name"]: t for t in TOOLS}


# ---------- JSON-RPC over stdio ----------

def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(msg):
    method, id_, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if id_ is None:                       # notifications (initialized, cancelled, ...)
        return
    if method == "initialize":
        return reply(id_, {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "streamdeck", "version": "1.0.0"},
                           "instructions": INSTRUCTIONS})
    if method == "ping":
        return reply(id_, {})
    if method == "tools/list":
        return reply(id_, {"tools": [{k: v for k, v in t.items() if k != "handler"} for t in TOOLS]})
    if method == "tools/call":
        tool = BY_NAME.get(params.get("name"))
        if not tool:
            return reply(id_, error={"code": -32602, "message": f"unknown tool {params.get('name')}"})
        try:
            text, is_err = tool["handler"](params.get("arguments") or {}), False
        except Exception as e:
            text, is_err = str(e), True
        return reply(id_, {"content": [{"type": "text", "text": text}], "isError": is_err})
    reply(id_, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except Exception as e:
            sys.stderr.write(f"streamdeck mcp: {e!r}\n")


if __name__ == "__main__":
    main()
