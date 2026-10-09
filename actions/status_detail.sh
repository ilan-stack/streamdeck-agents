#!/bin/zsh
# Show the last service check (written by notify.py) on the info bar.
source ${0:A:h}/_lib.sh
bar "$(cat ~/streamdeck/state/status.txt 2>/dev/null || echo 'No status yet')" 12
