#!/bin/zsh
# Jump to a Claude Code session in the Claude app.
#   claude_jump.sh <cli-session-id>   that session (from the Sessions page)
#   claude_jump.sh                    the most urgent one: waiting for you first, then the latest finished
# A finished session you jump to is marked seen (its state file is removed; it comes back on the next prompt).
source ${0:A:h}/_lib.sh
STATE=${0:A:h:h}/state/claude
sid=$1
if [[ -z $sid ]]; then
  sid=$($PYAPP - "$STATE" <<'EOF'
import json, os, sys
d = sys.argv[1]
items = []
for f in os.listdir(d) if os.path.isdir(d) else []:
    try:
        items.append(json.load(open(os.path.join(d, f))))
    except Exception:
        pass
rank = {"needs": 0, "done": 1}
items = sorted((e for e in items if e.get("status") in rank), key=lambda e: (rank[e["status"]], -e.get("ts", 0)))
print(items[0]["session"] if items else "")
EOF
)
fi
[[ -z $sid ]] && { open -a Claude; exit; }
# Claude app keeps local_<id>.json per session with the CLI id inside -> its deep link
link=$(grep -l "\"cliSessionId\":\"$sid\"\|\"cliSessionId\": \"$sid\"" \
  ~/Library/Application\ Support/Claude/claude-code-sessions/*/*/local_*.json 2>/dev/null | head -1)
if [[ -n $link ]]; then open "claude://claude.ai/epitaxy/${${link:t}%.json}"; else open -a Claude; fi
f=$STATE/$sid.json
[[ -f $f ]] && $PYAPP -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("status") == "done" else 1)' $f && rm -f $f
