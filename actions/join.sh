#!/bin/zsh
# Join the current/next meeting (link saved by notify.py), else open the calendar.
# usage: join.sh [outlook]   (fallback: Google Calendar, or the Outlook app)
source ${0:A:h}/_lib.sh
info=("${(@f)$($PYAPP ${0:A:h}/meeting.py)}")
if [[ -n ${info[1]} ]]; then open "${info[1]}"; bar "📹 Joining: ${info[2]}" 6
elif [[ $1 == outlook ]]; then open -a "Microsoft Outlook"
else open https://calendar.google.com; fi
