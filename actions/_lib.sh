# Shared helpers for action scripts.
DECK=http://127.0.0.1:8885
LIBDIR=${${(%):-%x}:A:h}
export PATH=/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin
# PYAPP = the Python binary that holds the Accessibility grant; install.sh writes it to _env.sh.
PYAPP=python3
[[ -f $LIBDIR/_env.sh ]] && source $LIBDIR/_env.sh
json() { $PYAPP -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }
bar() { curl -s -m 2 -XPOST $DECK/screen -d "{\"text\":$(json "$1"),\"seconds\":${2:-8}}" >/dev/null; }
combo() { $PYAPP $LIBDIR/keys.py $1 2>>/tmp/sd_clip.log; }
