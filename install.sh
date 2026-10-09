#!/bin/zsh
# streamdeck-agents - installer. Run from the cloned folder.
# Needs: an Apple Silicon Mac, Homebrew `hidapi` + `fribidi` (copied into lib/), and Xcode Command Line Tools
# (swiftc) to build the small native helpers in bin/. Optional for push-to-talk: `brew install whisper-cpp` + a model.
#   ./install.sh               full install
#   ./install.sh --no-hooks    skip Claude Code hooks
#   ./install.sh --no-launchd  set everything up but don't start background services (testing)
#   ./install.sh --python /path/to/python3   force a specific Python
# Safe to re-run. No admin rights needed. See README.md.
set -e
DIR=${0:A:h}
cd $DIR
HOOKS=1; LAUNCHD=1; FORCE_PY=""
while (( $# )); do
  case $1 in --no-hooks) HOOKS=0;; --no-launchd) LAUNCHD=0;; --python) FORCE_PY=$2; shift;; esac; shift
done
say() { print -P "%F{cyan}==>%f $*"; }
warn() { print -P "%F{yellow}!!%f $*"; }
die() { print -P "%F{red}xx%f $*"; exit 1; }

[[ $(uname -m) == arm64 ]] || die "Built and tested on Apple Silicon (arm64) only."

[[ -f profile.json ]] || { cp profile.example.json profile.json; say "Created profile.json from profile.example.json"; }

if [[ ! -f lib/libhidapi.dylib || ! -f lib/libfribidi.0.dylib ]]; then
  say "Copying hidapi + fribidi from Homebrew into lib/"
  BREW=$(brew --prefix 2>/dev/null || echo /opt/homebrew)
  [[ -f $BREW/lib/libhidapi.0.dylib && -f $BREW/lib/libfribidi.0.dylib ]] || die "Missing libraries - run: brew install hidapi fribidi"
  mkdir -p lib
  cp -L $BREW/lib/libhidapi.0.dylib lib/libhidapi.dylib
  cp -L $BREW/lib/libfribidi.0.dylib lib/libfribidi.0.dylib
  cp lib/libfribidi.0.dylib lib/libfribidi.dylib          # Pillow tries both bare names
  chmod u+w lib/*
fi

say "Clearing the download quarantine flag (unsigned helper app + bundled libraries)"
chmod -R u+w $DIR                                  # read-only files keep the flag: xattr gets EACCES
xattr -dr com.apple.quarantine $DIR 2>/dev/null || true
if xattr -lr $DIR 2>/dev/null | grep -q com.apple.quarantine; then
  die "Some files are still quarantined - run: xattr -dr com.apple.quarantine ${(q)DIR}"
fi

say "Looking for Python 3.9+"
cands=(/opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3
       /Library/Frameworks/Python.framework/Versions/3.1{4,3,2,1,0}/bin/python3 /usr/local/bin/python3)
xcode-select -p >/dev/null 2>&1 && cands+=(/usr/bin/python3)   # without CLT, /usr/bin/python3 pops an install dialog
PY=""
[[ -n $FORCE_PY ]] && cands=($FORCE_PY)
for c in $cands; do
  [[ -x $c ]] || continue
  if $c -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY=$c; break; fi
done
[[ -n $PY ]] || die "No Python 3.9+ found. Install one of: Xcode Command Line Tools (xcode-select --install), python.org installer, or Homebrew python. Then re-run."
say "Using $PY ($($PY -c 'import sys; print(sys.version.split()[0])'))"

# The binary macOS sees (and that gets Accessibility): Python.app inside the framework when there is one.
PYAPP=$($PY - <<'EOF'
import os, sys
app = os.path.join(sys.base_prefix, "Resources", "Python.app", "Contents", "MacOS", "Python")
print(app if os.path.exists(app) else os.path.realpath(sys.executable))
EOF
)
say "Runtime binary: $PYAPP"

say "Creating the virtualenv and installing packages (offline wheels first)"
rm -rf .venv
$PY -m venv .venv
if [[ -d wheels ]] && .venv/bin/pip install -q --no-index --find-links wheels "streamdeck==0.9.6" pillow 2>/dev/null; then
  :
else
  .venv/bin/pip install -q "streamdeck==0.9.6" pillow || die "pip install failed (network/proxy?)"
fi
SITE=$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')

if [[ ! -x bin/sfsymbol || ! -d "bin/StreamDeck Calendar.app" || ! -d "bin/StreamDeck Mic.app" ]]; then
  say "Building the native helpers (bin/)"
  ./tools/build_native.sh || die "Native build failed - is Xcode Command Line Tools installed? (xcode-select --install)"
fi
if [[ -z $(ls icons/*.png 2>/dev/null) ]]; then
  say "Rendering the default icon set (icons/)"
  PYTHONPATH=$SITE $PYAPP tools/make_icons.py || warn "Icon rendering failed - keys fall back to text labels"
fi

say "Checking the bundled libraries load"
(cd $DIR/lib && PYTHONPATH=$SITE $PYAPP - <<'EOF'
import ctypes, os
ctypes.CDLL("./libhidapi.dylib")
from PIL import features, ImageFont
assert features.check_feature("fribidi"), "fribidi not active - Hebrew would render left-to-right"
from StreamDeck.Transport.LibUSBHIDAPI import LibUSBHIDAPI
print("   libraries OK, Hebrew layout OK")
EOF
) || die "Libraries failed to load (see above)."

say "Writing run scripts and actions/_env.sh"
print -r -- "PYAPP=${(q)PYAPP}" > actions/_env.sh
cat > run.sh <<EOF
#!/bin/zsh
# Runs as the Python binary that holds the macOS permissions; packages come from .venv via PYTHONPATH.
cd ${(q)DIR}
export PYTHONPATH=${(q)SITE}
exec ${(q)PYAPP} -u deck.py
EOF
cat > run_notify.sh <<EOF
#!/bin/zsh
cd ${(q)DIR}
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
exec ${(q)PYAPP} -u notify.py
EOF
chmod +x run.sh run_notify.sh actions/*.sh
mkdir -p state/claude

if [[ $HOOKS == 1 ]]; then
  if [[ -d $HOME/.claude ]]; then
    say "Adding Claude Code hooks to ~/.claude/settings.json (backup kept)"
    $PYAPP - "$DIR" "$PYAPP" <<'EOF'
import json, os, shutil, sys, time
d, py = sys.argv[1], sys.argv[2]
p = os.path.expanduser("~/.claude/settings.json")
s = json.load(open(p)) if os.path.exists(p) else {}
if os.path.exists(p):
    shutil.copy(p, f"{p}.bak-streamdeck-{time.strftime('%Y%m%d-%H%M%S')}")
want = {"PermissionRequest": ("hook_permission.py", 35)}
for ev in ("Stop", "Notification", "UserPromptSubmit", "SessionEnd"):
    want[ev] = ("hook_claude.py", 5)
hooks = s.setdefault("hooks", {})
for ev, (script, timeout) in want.items():
    lst = [e for e in hooks.get(ev, []) if not any(script in h.get("command", "") for h in e.get("hooks", []))]
    lst.append({"hooks": [{"type": "command", "command": f'"{py}" "{d}/{script}"', "timeout": timeout}]})
    hooks[ev] = lst
json.dump(s, open(p, "w"), indent=2, ensure_ascii=False)
print("   hooks:", ", ".join(want))
EOF
  else
    warn "~/.claude not found - skipping Claude Code hooks (re-run after installing Claude Code)."
  fi
fi

if [[ $HOOKS == 1 ]]; then
  CLAUDE_BIN=$(command -v claude || ls /opt/homebrew/bin/claude $HOME/.local/bin/claude $HOME/.claude/local/claude 2>/dev/null | head -1)
  if [[ -n $CLAUDE_BIN ]]; then
    say "Registering the streamdeck MCP server with Claude Code (user scope)"
    $CLAUDE_BIN mcp remove streamdeck -s user >/dev/null 2>&1 || true
    $CLAUDE_BIN mcp add --scope user streamdeck -- "$PYAPP" "$DIR/mcp_server.py" >/dev/null && echo "   added" \
      || warn "Couldn't register the MCP server - run: claude mcp add --scope user streamdeck -- \"$PYAPP\" \"$DIR/mcp_server.py\""
  else
    warn "claude CLI not found - skipping MCP registration (see README)."
  fi
fi

if [[ $LAUNCHD == 1 ]]; then
  say "Installing background services (launchd)"
  LA=$HOME/Library/LaunchAgents; mkdir -p $LA
  for pair in "streamdeck-agents.deck:run.sh:daemon.log" "streamdeck-agents.notify:run_notify.sh:notify.log"; do
    label=${pair%%:*}; rest=${pair#*:}; script=${rest%%:*}; logf=${rest#*:}
    cat > $LA/$label.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>$DIR/$script</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$DIR/$logf</string>
  <key>StandardErrorPath</key><string>$DIR/$logf</string>
</dict></plist>
EOF
    launchctl bootout gui/$(id -u)/$label 2>/dev/null || true
    launchctl bootstrap gui/$(id -u) $LA/$label.plist
  done
  sleep 5
  if curl -s -m 3 127.0.0.1:8885/state | grep -q '"connected": true'; then say "Stream Deck connected ✓"
  else warn "Service is up but no Stream Deck yet - plug it in (it connects by itself). Log: $DIR/daemon.log"; fi
fi

[[ -f $HOME/.config/streamdeck/anthropic_key ]] || warn "No API key yet - Translate/Improve stay off until you add one (see README: API key)."
print -rn -- "${PYAPP:h:h:h}" | pbcopy
cat <<EOF

Done. Two permissions are left for you (see README: macOS permissions):
  1. System Settings → Privacy & Security → Accessibility → + → Cmd+Shift+G → Cmd+V → Open → switch ON
     (the path is on your clipboard: ${PYAPP:h:h:h})
  2. The first time the Calendar key updates, macOS asks to let "StreamDeck Calendar" see calendars → Allow.
EOF
