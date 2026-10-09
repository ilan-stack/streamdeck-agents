#!/bin/zsh
# Toggle a 25-minute focus timer on the info bar.
source ${0:A:h}/_lib.sh
if curl -s -m 2 $DECK/state | $PYAPP -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["timer"] else 1)'; then
  curl -s -m 2 -XPOST $DECK/timer -d '{"cancel":true}' >/dev/null; bar "Focus cancelled" 3
else
  curl -s -m 2 -XPOST $DECK/timer -d '{"seconds":1500,"label":"Focus"}' >/dev/null
fi
