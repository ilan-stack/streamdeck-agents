#!/bin/zsh
# Toggle a screen recording: 1st press opens the macOS capture toolbar (Cmd+Shift+5 - pick
# "Record Selected Portion", then Record); 2nd press stops it (Cmd+Ctrl+Esc).
# usage: record.sh <page> <key>   (where the Record key sits, so it can show REC)
source ${0:A:h}/_lib.sh
PAGE=${1:-0}; KEY=${2:-0}
FLAG=/tmp/sd_recording
if [[ -f $FLAG ]]; then
  combo cmd+ctrl+esc; rm -f $FLAG
  curl -s -m 2 -XPOST $DECK/set -d "{\"page\":$PAGE,\"key\":$KEY,\"label\":\"Record\",\"icon\":\"record.png\",\"badge\":0}" >/dev/null
  bar "⏹ Recording saved" 5
else
  combo cmd+shift+5 || { bar "⚠ Allow Python.app in Accessibility" 8; exit; }
  touch $FLAG
  curl -s -m 2 -XPOST $DECK/set -d "{\"page\":$PAGE,\"key\":$KEY,\"label\":\"Record\",\"icon\":\"record.png\",\"badge\":\"REC\"}" >/dev/null
  bar "Select an area → Record · press again to stop" 8
fi
