#!/bin/zsh
# Region screenshot to the clipboard (macOS Cmd+Ctrl+Shift+4).
source ${0:A:h}/_lib.sh
combo cmd+ctrl+shift+4 || bar "⚠ Allow Python.app in Accessibility" 8
