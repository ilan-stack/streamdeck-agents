"""voice.py "<what was said>" - voice command router for the deck.

Claude Haiku gets every key in profile.json as an action it may run (plus built-ins: timer, bar message,
page switch) and some live context (time, next meeting, services). It either picks a tool - we run it -
or answers in text - shown on the bar and spoken. Only keys and built-ins can run, never free-form shell.
Keys with "confirm": true (physical effects) need a Yes on the Neo first.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ask import api_key, MODEL  # noqa: E402

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECK = "http://127.0.0.1:8885"
LOG = "/tmp/sd_voice.log"


def deck(path, body=None, timeout=5):
    req = urllib.request.Request(DECK + path, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception:
        return {}


def bar(text, seconds=8):
    deck("/screen", {"text": text, "seconds": seconds})


def speak(text):
    voice = ["-v", "Carmit"] if any("א" <= c <= "ת" for c in text) else []
    subprocess.Popen(["say", *voice, "--", text])


def log(*a):
    with open(LOG, "a") as f:
        print(time.strftime("%H:%M:%S"), *a, file=f)


def load_actions():
    """Every pressable key -> {id, page, key, label, desc, cmd, confirm}. Hold keys (Talk) are skipped."""
    p = json.load(open(os.path.join(HOME, "profile.json")))
    base = {"home": HOME, "actions": os.path.join(HOME, "actions")}
    acts = []
    for pi, pg in enumerate(p["pages"]):
        for k, spec in sorted(pg["keys"].items(), key=lambda kv: int(kv[0])):
            if "cmd" not in spec:
                continue
            acts.append({"id": f"p{pi}k{k}", "page": pg["name"], "label": spec.get("label", ""),
                         "desc": spec.get("desc", ""), "confirm": bool(spec.get("confirm")),
                         "cmd": spec["cmd"].format(page=pi, key=int(k), **base)})
    return acts


def context():
    lines = [f"Now: {time.strftime('%A %d %B %Y %H:%M')}."]
    try:
        m = json.load(open(os.path.join(HOME, "state", "next_meeting.json")))
        if m:
            mins = int((m["start"] - time.time()) // 60)
            when = f"in {mins} min" if mins > 0 else "now (in progress)"
            lines.append(f"Next meeting: '{m['summary']}' {when}{', has a join link' if m.get('link') else ''}.")
        else:
            lines.append("No timed meetings in the next 12 hours.")
    except Exception:
        pass
    try:
        lines.append("Services: " + open(os.path.join(HOME, "state", "status.txt")).read().strip())
    except Exception:
        pass
    st = deck("/state")
    if st.get("timer"):
        lines.append(f"A '{st['timer']['label']}' timer is running, {st['timer']['left'] // 60} min left.")
    return "\n".join(lines)


def tools(acts):
    listing = "\n".join(f"- {a['id']}: {a['label']}" + (f" - {a['desc']}" if a['desc'] else "")
                        + (" [asks for confirmation]" if a['confirm'] else "") for a in acts)
    return [
        {"name": "run_action",
         "description": "Press one of the user's Stream Deck keys. Available actions:\n" + listing,
         "input_schema": {"type": "object", "properties": {
             "action": {"type": "string", "enum": [a["id"] for a in acts]},
             "say": {"type": "string", "description": "very short confirmation for the display, user's language"}},
             "required": ["action"]}},
        {"name": "set_timer",
         "description": "Start a countdown on the deck's display (replaces any running timer). minutes=0 cancels it.",
         "input_schema": {"type": "object", "properties": {
             "minutes": {"type": "number"}, "label": {"type": "string", "description": "short, e.g. Tea, Focus"}},
             "required": ["minutes"]}},
        {"name": "show_on_display",
         "description": "Show a short note on the deck's small display for a while (a reminder, a number).",
         "input_schema": {"type": "object", "properties": {
             "text": {"type": "string"}, "seconds": {"type": "number"}}, "required": ["text"]}},
        {"name": "switch_page",
         "description": "Show a different page of keys on the deck.",
         "input_schema": {"type": "object", "properties": {"page": {"type": "integer", "description": "0-based"}},
                          "required": ["page"]}},
    ]


SYSTEM = """You are the voice control of the user's Stream Deck (a small key pad with a display).
The user spoke a request (transcribed, may contain small errors; Hebrew or English).
If the request matches a tool, call exactly ONE tool. If it's a question, answer in at most 20 words,
plain text, in the user's language, using the context below. Never invent actions that aren't listed.
If you can't help, say so briefly.

Context:
"""


def ask_claude(text, acts):
    body = {"model": MODEL, "max_tokens": 300, "system": SYSTEM + context(), "tools": tools(acts),
            "messages": [{"role": "user", "content": text}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", json.dumps(body).encode(), {
        "x-api-key": api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=40))


def run(tool, inp, acts):
    if tool == "run_action":
        a = next((x for x in acts if x["id"] == inp.get("action")), None)
        if not a:
            return bar("Unknown action", 5)
        if a["confirm"]:
            r = deck("/ask", {"prompt": f"{a['label']}?", "info": "Voice",
                              "options": [{"label": "Yes", "color": "#30a46c"}, {"label": "No", "color": "#e5484d"}],
                              "timeout": 20}, timeout=30)
            if r.get("choice") != 0:
                return bar("Cancelled", 3)
        subprocess.Popen(a["cmd"], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        bar(f"✓ {inp.get('say') or a['label']}", 4)
    elif tool == "set_timer":
        mins = float(inp.get("minutes", 0))
        if mins <= 0:
            deck("/timer", {"cancel": True})
            return bar("Timer cancelled", 3)
        deck("/timer", {"seconds": mins * 60, "label": inp.get("label") or "Timer"})
    elif tool == "show_on_display":
        bar(inp["text"], float(inp.get("seconds", 30)))
    elif tool == "switch_page":
        deck("/page", {"page": int(inp["page"])})


def main():
    dry = len(sys.argv) > 1 and sys.argv[1] == "--dry"      # print the decision, run nothing
    text = " ".join(sys.argv[2 if dry else 1:]).strip()
    if not text:
        return
    acts = load_actions()
    try:
        res = ask_claude(text, acts)
    except Exception as e:
        log("api error", repr(e))
        return bar("Claude failed", 6)
    used = [b for b in res.get("content", []) if b.get("type") == "tool_use"]
    if used:
        for b in used[:1]:
            log(text, "->", b["name"], json.dumps(b["input"], ensure_ascii=False))
            if dry:
                a = next((x for x in acts if x["id"] == b["input"].get("action")), {})
                print(f"TOOL {b['name']} {json.dumps(b['input'], ensure_ascii=False)} {a.get('label', '')}")
            else:
                run(b["name"], b["input"], acts)
        return
    answer = " ".join("".join(b.get("text", "") for b in res.get("content", [])).split())
    log(text, "-> answer:", answer)
    if dry:
        print(f"ANSWER {answer}")
    elif answer:
        bar(answer, 25)
        speak(answer)


if __name__ == "__main__":
    main()
