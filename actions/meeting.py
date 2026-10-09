"""meeting.py -> prints the join link of the meeting starting within 15 min or in progress (else nothing).
Reads state/next_meeting.json, written by notify.py. Second line: the meeting title."""
import json
import os
import time

try:
    m = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "state", "next_meeting.json")))
except Exception:
    m = {}
if m.get("link") and m.get("start", 0) - time.time() <= 15 * 60 and m.get("end", 0) > time.time():
    print(m["link"])
    print(m.get("summary", ""))
