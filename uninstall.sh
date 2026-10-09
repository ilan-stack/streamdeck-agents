#!/bin/zsh
# Stream Deck Neo kit - uninstaller: stops the services, removes them and this kit's Claude Code hooks.
# The folder itself is left in place - delete it yourself afterwards if you want.
DIR=${0:A:h}
for label in streamdeck-agents.deck streamdeck-agents.notify; do
  launchctl bootout gui/$(id -u)/$label 2>/dev/null
  rm -f $HOME/Library/LaunchAgents/$label.plist
done
echo "services removed"
P=$HOME/.claude/settings.json
[[ -f $P && -f $DIR/actions/_env.sh ]] || exit 0
source $DIR/actions/_env.sh            # PYAPP, written by install.sh
cp $P "$P.bak-streamdeck-uninstall-$(date +%Y%m%d-%H%M%S)"
$PYAPP - "$DIR" <<'EOF'
import json, os, sys
d = sys.argv[1]
p = os.path.expanduser("~/.claude/settings.json")
s = json.load(open(p))
for ev, lst in list(s.get("hooks", {}).items()):
    keep = [e for e in lst if not any(d in h.get("command", "") for h in e.get("hooks", []))]
    if keep:
        s["hooks"][ev] = keep
    else:
        del s["hooks"][ev]
json.dump(s, open(p, "w"), indent=2, ensure_ascii=False)
print("Claude Code hooks removed")
EOF
