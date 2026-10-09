// calnext - print the current/next timed event (next 12h) from the macOS calendar store as JSON.
// Works with any account added to macOS Calendar (Exchange/Outlook 365, Google, iCloud).
// usage: calnext [calendar-name-substring]   -> {"summary","start","end","link","calendar"} | {} | {"error"}
import EventKit
import Foundation

let store = EKEventStore()
let sema = DispatchSemaphore(value: 0)
var granted = false
if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents { ok, _ in granted = ok; sema.signal() }
} else {
    store.requestAccess(to: .event) { ok, _ in granted = ok; sema.signal() }
}
_ = sema.wait(timeout: .now() + 60)
func emit(_ o: [String: Any]) {
    let d = try! JSONSerialization.data(withJSONObject: o)
    FileHandle.standardOutput.write(d); FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}
guard granted else { emit(["error": "calendar access denied"]); exit(2) }

let filter = CommandLine.arguments.count > 1 ? CommandLine.arguments[1].lowercased() : nil
var cals = store.calendars(for: .event)
if let f = filter { cals = cals.filter { $0.title.lowercased().contains(f) || $0.source.title.lowercased().contains(f) } }
let now = Date()
let pred = store.predicateForEvents(withStart: now, end: now.addingTimeInterval(12 * 3600), calendars: cals)
let pattern = "https://(?:[\\w.-]*zoom\\.us/j/|meet\\.google\\.com/|teams\\.microsoft\\.com/l/meetup-join/|teams\\.live\\.com/meet/)[^\\s\"<>]+"
let rx = try! NSRegularExpression(pattern: pattern)
func link(_ e: EKEvent) -> String? {
    if let u = e.url?.absoluteString, rx.firstMatch(in: u, range: NSRange(u.startIndex..., in: u)) != nil { return u }
    let text = "\(e.location ?? "") \(e.notes ?? "")"
    if let m = rx.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)), let r = Range(m.range, in: text) {
        return String(text[r])
    }
    return nil
}
let events = store.events(matching: pred)
    .filter { !$0.isAllDay && $0.endDate > now && $0.status != .canceled }
    .sorted { $0.startDate < $1.startDate }
guard let e = events.first else { emit([:]); exit(0) }
var o: [String: Any] = ["summary": e.title ?? "(no title)", "start": e.startDate.timeIntervalSince1970,
                        "end": e.endDate.timeIntervalSince1970, "calendar": e.calendar.title]
if let l = link(e) { o["link"] = l }
emit(o)
