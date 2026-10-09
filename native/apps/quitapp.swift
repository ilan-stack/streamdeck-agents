import AppKit
// quitapp <App name | bundle id>  - ask a running app to quit normally (like Cmd+Q: it may ask to save).
//                                   Prints the app's name; exit 1 if it isn't running.
// quitapp --list                  - names of running apps (one per line), for the voice router.
// NSRunningApplication.terminate() needs no Accessibility/Automation grant.
let args = Array(CommandLine.arguments.dropFirst())
let clean = { (s: String?) in (s ?? "").replacingOccurrences(of: "\u{200e}", with: "") }
let running = NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular }
if args.first == "--list" {
    for a in running { print(clean(a.localizedName)) }
    exit(0)
}
let want = args.joined(separator: " ").lowercased()
guard !want.isEmpty else { exit(64) }
let apps = running.filter { clean($0.bundleIdentifier).lowercased() == want || clean($0.localizedName).lowercased() == want }
guard !apps.isEmpty else { exit(1) }
for a in apps { _ = a.terminate() }
print(clean(apps[0].localizedName))
