#!/bin/zsh
# closetab.sh <host> - double press on a link key: close that site's tab (only if one is open). See native/tabs.
source ${0:A:h}/_lib.sh
out=/tmp/sd_closetab.out; rm -f $out
open -W -g --stdout $out -a "${0:A:h:h}/bin/StreamDeck Tabs.app" --args "$1"
for i in {1..30}; do [[ -s $out ]] && break; sleep 0.1; done      # `open -W` can return before the line lands
case $(cat $out 2>/dev/null) in
  closed)      bar "✕ Closed ${1#www.}" 3 ;;
  denied)      bar "⚠ Allow StreamDeck Tabs to control the browser" 8 ;;
  not-running) bar "The browser isn't open" 3 ;;
  *)           bar "No ${1#www.} tab to close" 3 ;;
esac
