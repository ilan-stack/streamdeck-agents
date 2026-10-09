#!/bin/zsh
# layout | translate | improve | summarize. layout = wrong-keyboard fix (layout.py, no AI); the rest use Claude Haiku (ask.py).
# layout/translate/improve: copy the current selection (Cmd+C), replace it in place (Cmd+V), then restore the clipboard.
# summarize: works on the selection (or the clipboard if nothing is selected); result goes to the clipboard + bar.
# Needs Accessibility for Python.app (keys.py sends the keystrokes via CoreGraphics). Repeat presses while running are ignored.
source ${0:A:h}/_lib.sh
LOCK=/tmp/sd_clip.lock
mkdir $LOCK 2>/dev/null || exit 0
trap 'rmdir $LOCK' EXIT

changes() { osascript -l JavaScript -e 'ObjC.import("AppKit"); $.NSPasteboard.generalPasteboard.changeCount' 2>/dev/null; }
HERE=${0:A:h}
keys() { combo $1; }

saved=$(pbpaste)
before=$(changes)
keys c || { bar "⚠ Allow Python.app in Accessibility" 10; exit; }
for i in {1..10}; do sleep 0.05; [[ $(changes) != $before ]] && break; done
if [[ $(changes) != $before ]]; then text=$(pbpaste); selected=1; else text=$saved; selected=0; fi

if [[ $1 != summarize && $selected == 0 ]]; then bar "Select some text first" 4; exit; fi
[[ -z ${text// } ]] && { bar "Nothing to work on" 4; exit; }
case $1 in
  layout)    prompt='';;
  translate) prompt='Translate the text. If it is Hebrew, translate to English; otherwise translate to Hebrew. Keep line breaks and formatting. Output ONLY the translation.';;
  improve)   prompt='Rewrite the text to be clearer and more natural, in the SAME language, keeping the meaning and tone. Use plain hyphens, never em dashes. Output ONLY the rewritten text.';;
  summarize) prompt='Summarize the text in 1-3 short sentences, in the SAME language as the text. Output ONLY the summary.';;
  *) exit 1;;
esac
if [[ $1 == layout ]]; then
  out=$(print -rn -- "$text" | $PYAPP $HERE/layout.py)
else
  bar "✨ ${1}…" 60
  out=$(print -r -- "$text" | $PYAPP $HERE/ask.py "$prompt" 2>>/tmp/sd_ask.log)
fi
[[ -z $out ]] && { print -rn -- "$saved" | pbcopy; bar "Claude failed - nothing changed" 8; exit; }

print -rn -- "$out" | pbcopy
if [[ $1 == summarize ]]; then
  bar "📋 ${out//$'\n'/ }" 20
else
  keys v; sleep 0.4
  print -rn -- "$saved" | pbcopy          # give the user their clipboard back
  bar "✓ ${out//$'\n'/ }" 6
fi
afplay /System/Library/Sounds/Pop.aiff &
