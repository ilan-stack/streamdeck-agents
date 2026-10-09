// recmic <out.wav> <stopfile> - record the default mic (16 kHz mono WAV) until <stopfile> appears (max 60 s).
// Lives in "StreamDeck Mic.app" and is started with `open`, so the Microphone permission belongs to this app.
import AVFoundation
import Foundation

let args = CommandLine.arguments
guard args.count >= 3 else { exit(64) }
let out = URL(fileURLWithPath: args[1]), stop = args[2]
let sema = DispatchSemaphore(value: 0)
var ok = false
AVCaptureDevice.requestAccess(for: .audio) { g in ok = g; sema.signal() }
_ = sema.wait(timeout: .now() + 60)
guard ok else { FileHandle.standardError.write("microphone access denied\n".data(using: .utf8)!); exit(2) }

let settings: [String: Any] = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000.0,
                               AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16,
                               AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false]
guard let rec = try? AVAudioRecorder(url: out, settings: settings), rec.record() else { exit(3) }
let t0 = Date()
while !FileManager.default.fileExists(atPath: stop) && Date().timeIntervalSince(t0) < 60 {
    RunLoop.current.run(until: Date().addingTimeInterval(0.05))
}
rec.stop()
try? FileManager.default.removeItem(atPath: stop)
