#!/bin/zsh
# Toggle macOS output mute and repaint key 0 right away (notify.py also polls it).
m=$(osascript -e 'set m to not (output muted of (get volume settings))' -e 'set volume output muted m' -e 'm')
icon=volume.png; [[ $m == true ]] && icon=muted.png
curl -s -m 2 -XPOST 127.0.0.1:8885/set -d "{\"key\":0,\"label\":\"Mute\",\"icon\":\"$icon\"}" >/dev/null
