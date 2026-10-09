import AppKit
// closetab <host>  - close the browser tab showing <host> (e.g. mail.google.com) in the default browser:
// the active tab if it matches, else the first matching tab in the front window. Never launches the browser.
// Prints closed | none | not-running | denied. Lives in "StreamDeck Tabs.app" (launched via `open`) so the
// "control Google Chrome / Safari" Automation permission belongs to it.
let host = CommandLine.arguments.dropFirst().first ?? ""
func out(_ s: String) -> Never { print(s); exit(0) }
guard !host.isEmpty else { out("none") }
let probe = URL(string: "https://example.com")!
let browser = NSWorkspace.shared.urlForApplication(toOpen: probe).flatMap { Bundle(url: $0)?.bundleIdentifier } ?? "com.google.Chrome"
guard NSWorkspace.shared.runningApplications.contains(where: { $0.bundleIdentifier == browser }) else { out("not-running") }
let h = host.replacingOccurrences(of: "\"", with: "")
let script: String
if browser == "com.apple.Safari" {
    script = """
    tell application id "com.apple.Safari"
      if (count of windows) = 0 then return "none"
      set w to front window
      if URL of current tab of w contains "\(h)" then
        close current tab of w
        return "closed"
      end if
      repeat with t in tabs of w
        if URL of t contains "\(h)" then
          close t
          return "closed"
        end if
      end repeat
      return "none"
    end tell
    """
} else {   // Chrome and its relatives (Brave, Edge, Arc...) share the same scripting dictionary
    script = """
    tell application id "\(browser)"
      if (count of windows) = 0 then return "none"
      set w to front window
      if URL of active tab of w contains "\(h)" then
        close active tab of w
        return "closed"
      end if
      repeat with t in tabs of w
        if URL of t contains "\(h)" then
          close t
          return "closed"
        end if
      end repeat
      return "none"
    end tell
    """
}
var err: NSDictionary?
let res = NSAppleScript(source: script)?.executeAndReturnError(&err)
if let e = err { FileHandle.standardError.write("\(e)\n".data(using: .utf8)!); out((e["NSAppleScriptErrorNumber"] as? Int) == -1743 ? "denied" : "none") }
out(res?.stringValue ?? "none")
