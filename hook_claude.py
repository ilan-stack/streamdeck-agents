#!/usr/bin/env python3
"""Claude Code hook (UserPromptSubmit / Stop / Notification / SessionEnd) -> per-session state for the deck.

state/claude/<session>.json = {"session", "project", "status", "msg", "ts"} where status is
  working  - a prompt was submitted (UserPromptSubmit)
  done     - Claude finished its turn (Stop)
  needs    - Claude is waiting for the user (Notification, e.g. permission or idle input)
SessionEnd deletes the file. deck.py builds the "Sessions" page from these; notify.py badges the Claude key
for done/needs. Never prints anything and always exits 0, so it can't affect Claude Code.
"""
import json
import os
import sys
import time

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "claude")
STATUS = {"UserPromptSubmit": "working", "Stop": "done", "Notification": "needs"}


def main():
    if os.environ.get("STREAMDECK_NO_HOOK"):      # our own `claude -p` calls (clip.sh etc.)
        return
    ev = json.load(sys.stdin)
    sid = ev.get("session_id")
    if not sid:
        return
    path = os.path.join(DIR, f"{sid}.json")
    name = ev.get("hook_event_name")
    if name == "SessionEnd":
        if os.path.exists(path):
            os.remove(path)
        return
    if name not in STATUS:
        return
    if name == "Stop" and ev.get("stop_hook_active"):
        return
    msg = {"UserPromptSubmit": "working", "Stop": "finished",
           "Notification": " ".join(str(ev.get("message", "needs you")).split())[:80]}[name]
    os.makedirs(DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"session": sid, "project": os.path.basename(ev.get("cwd", "")) or "?",
                   "status": STATUS[name], "event": name, "msg": msg, "ts": time.time()}, f, ensure_ascii=False)
    os.replace(tmp, path)


try:
    main()
except Exception:
    pass
