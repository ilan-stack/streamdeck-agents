#!/bin/zsh
# Build the small native helpers into bin/ (needs Xcode Command Line Tools for swiftc).
#   bin/sfsymbol                 SF Symbol -> PNG (icons in the house style)
#   bin/appicon                  an app's real icon -> PNG (deck_add_key app=...)
#   bin/quitapp                  ask a running app to quit like Cmd+Q (double press on an app key, voice "close X")
#   bin/StreamDeck Tabs.app      close a site's browser tab (double press on a link key); own Automation grant
#   bin/StreamDeck Calendar.app  EventKit reader, launched via `open` so the Calendars permission is its own
#   bin/StreamDeck Mic.app       push-to-talk recorder, launched via `open` so the Microphone permission is its own
set -e
cd ${0:A:h:h}; mkdir -p bin
T=arm64-apple-macos12
swiftc -O -target $T native/icons/sfsymbol.swift -o bin/sfsymbol
swiftc -O -target $T native/icons/appicon.swift -o bin/appicon
swiftc -O -target $T native/apps/quitapp.swift -o bin/quitapp

app() {   # app <name> <exe> <swift> <bundle-id> <usage-key> <usage-text> [extra plist]
  local A="bin/$1.app"; rm -rf "$A"; mkdir -p "$A/Contents/MacOS"
  swiftc -O -target $T "$3" -o "$A/Contents/MacOS/$2"
  cat > "$A/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>$4</string>
  <key>CFBundleName</key><string>$1</string>
  <key>CFBundleExecutable</key><string>$2</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>$5</key><string>$6</string>$7
</dict></plist>
PL
  codesign -s - -f --deep "$A" >/dev/null 2>&1
}
app "StreamDeck Calendar" calnext native/calendar/calnext.swift io.github.streamdeck-agents.calendar \
    NSCalendarsFullAccessUsageDescription "Show your next meeting on the Stream Deck." \
    "<key>NSCalendarsUsageDescription</key><string>Show your next meeting on the Stream Deck.</string>"
app "StreamDeck Mic" recmic native/mic/recmic.swift io.github.streamdeck-agents.mic \
    NSMicrophoneUsageDescription "Record your voice while you hold the Talk key on the Stream Deck."
app "StreamDeck Tabs" closetab native/tabs/closetab.swift io.github.streamdeck-agents.tabs \
    NSAppleEventsUsageDescription "Close the matching browser tab when you double-press a link key on the Stream Deck."
ls bin
