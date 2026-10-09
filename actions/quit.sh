#!/bin/zsh
# quit.sh <App name | bundle id> - ask the app to quit like Cmd+Q (it may ask to save). Used by double presses + voice.
source ${0:A:h}/_lib.sh
if app=$(${0:A:h:h}/bin/quitapp "$*"); then bar "✕ Closed $app" 3; else bar "${*} isn't open" 3; fi
