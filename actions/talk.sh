#!/bin/zsh
# Push-to-talk: `start` on key down, `stop` on key up.
# Records the mic, transcribes with a local whisper-server (Hebrew-capable large-v3-turbo, started on demand,
# stopped after 10 idle minutes), routes it through voice.py (run a key / timer / page, or answer on the bar + speech).
source ${0:A:h}/_lib.sh
WAV=/tmp/sd_talk.wav; STOP=/tmp/sd_talk.stop; LAST=/tmp/sd_talk.last
# Recording runs in bin/StreamDeck Mic.app (started via `open`) so macOS asks for - and remembers - the
# Microphone permission for that app. ffmpeg launched from the deck service gets silence: the service is
# a background Python that macOS never prompts, and the Microphone list has no "+" to add it by hand.
MICAPP="${0:A:h:h}/bin/StreamDeck Mic.app"
MODEL=${WHISPER_MODEL:-$HOME/models/ggml-large-v3-turbo.bin}; WPORT=8886   # whisper.cpp model (brew install whisper-cpp)

server_up() { curl -s -m 1 127.0.0.1:$WPORT >/dev/null; }

case $1 in
start)
  server_up || nohup whisper-server -m $MODEL -l auto --host 127.0.0.1 --port $WPORT >/tmp/sd_whisper.log 2>&1 &
  rm -f $WAV $STOP
  open -g -a "$MICAPP" --args $WAV $STOP
  bar "🎙️ Listening…" 60
  ;;
stop)
  touch $STOP
  for i in {1..30}; do pgrep -qf "StreamDeck Mic.app/Contents/MacOS/recmic" || break; sleep 0.1; done
  [[ -s $WAV ]] && (( $(stat -f %z $WAV) > 8000 )) || { bar "🎙️ No audio - allow \"StreamDeck Mic\" in Microphone settings" 10; exit; }
  bar "🎙️ Transcribing…" 30
  for i in {1..30}; do server_up && break; sleep 0.5; done
  q=$(curl -s -m 30 127.0.0.1:$WPORT/inference -F file=@$WAV -F response_format=text | tr '\n' ' ' | sed 's/^ *//; s/ *$//')
  date +%s > $LAST
  ( sleep 600; (( $(date +%s) - $(cat $LAST) >= 590 )) && pkill -f "whisper-server -m $MODEL" ) >/dev/null 2>&1 &
  [[ -z $q || $q == *"[BLANK_AUDIO]"* ]] && { bar "🎙️ Didn't catch that" 5; exit; }
  bar "🗣 $q" 30
  $PYAPP ${0:A:h}/voice.py "$q"           # picks a key/tool or answers (bar + speech)
  ;;
esac
