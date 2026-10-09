#!/usr/bin/env python3
"""Claude Code PermissionRequest hook: ask on the Stream Deck Neo.

Prints a decision only when a key was pressed. On any failure (daemon down, busy,
timeout) it prints nothing and exits 0, so Claude Code shows its normal prompt.
"""
import json
import subprocess
import sys
import urllib.request

ASK_TIMEOUT = 25


def main():
    try:
        ev = json.load(sys.stdin)
        tool = ev.get("tool_name", "?")
        ti = ev.get("tool_input", {}) or {}
        detail = ti.get("command") or ti.get("file_path") or ti.get("url") or json.dumps(ti)[:120]
        detail = " ".join(str(detail).split())[:140]

        subprocess.Popen(["osascript", "-e",
                          f'display notification {json.dumps(detail)} with title {json.dumps("Claude: " + tool)}'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        body = {"info": tool.replace("mcp__", "")[:10], "prompt": detail, "timeout": ASK_TIMEOUT,
                "options": [{"label": "Approve", "color": "#30a46c"},
                            {"label": "Deny", "color": "#e5484d"}]}
        req = urllib.request.Request("http://127.0.0.1:8885/ask", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        res = json.load(urllib.request.urlopen(req, timeout=ASK_TIMEOUT + 5))
    except Exception:
        return
    if res.get("choice") == 0:
        decision = {"behavior": "allow"}
    elif res.get("choice") == 1:
        decision = {"behavior": "deny", "message": "Denied by user from Stream Deck", "interrupt": False}
    else:
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}))


main()
