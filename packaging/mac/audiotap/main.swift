// earshot-audiotap: capture system audio ("them") on macOS via a Core Audio
// process tap (macOS 14.4+) and spool it to disk as raw interleaved int16.
//
// Usage:
//   earshot-audiotap --out /path/to/spool_them.raw
//
// Output protocol (line-delimited JSON on stdout, one event per line):
//   {"event":"start","rate":48000,"channels":2,"format":"int16"}   once, after capture begins
//   {"event":"level","rms":0.031}                                  ~8 Hz, max RMS since last emit
//   {"event":"error","code":"args|io|permission|tap_failed","message":"..."}  then exit(1)
//   {"event":"stop","frames":123456}                               on clean shutdown, then exit(0)
//
// The helper converts whatever format the tap delivers to a FIXED on-disk
// format (int16, 48 kHz, 2ch interleaved) so the spool can never change rate
// mid-file, even if the output device or its sample rate changes while
// recording. It exits cleanly on SIGTERM/SIGINT or when stdin reaches EOF
// (the parent app crashed), leaving a valid, salvageable spool behind.
//
// Diagnostics go to stderr; the parent captures them to audiotap.log.

import AVFAudio
import CoreAudio
import Foundation

// MARK: - Fixed output format

let OUT_RATE: Double = 48000
let OUT_CHANNELS: AVAudioChannelCount = 2

// MARK: - stdout / stderr plumbing

let stdoutHandle = FileHandle.standardOutput

func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj) else { return }
    stdoutHandle.write(data)
    stdoutHandle.write(Data([0x0A]))
}

func log(_ msg: String) {
    FileHandle.standardError.write(("audiotap: " + msg + "\n").data(using: .utf8)!)
}

func fail(_ code: String, _ message: String) -> Never {
    emit(["event": "error", "code": code, "message": message])
    log("FATAL [\(code)] \(message)")
    exit(1)
}

// MARK: - Arguments

var outPath: String? = nil
var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let a = args.removeFirst()
    switch a {
    case "--out":
        guard !args.isEmpty else { fail("args", "--out requires a path") }
        outPath = args.removeFirst()
    default:
        fail("args", "unknown argument: \(a)")
    }
}
guard let spoolPath = outPath else { fail("args", "--out <path> is required") }

// MARK: - Spool file

FileManager.default.createFile(atPath: spoolPath, contents: nil)
guard let spoolFile = FileHandle(forWritingAtPath: spoolPath) else {
    fail("io", "cannot open spool file for writing: \(spoolPath)")
}

// MARK: - Core Audio helpers

func fourCC(_ status: OSStatus) -> String {
    let n = UInt32(bitPattern: status)
    let bytes = [UInt8((n >> 24) & 255), UInt8((n >> 16) & 255), UInt8((n >> 8) & 255), UInt8(n & 255)]
    if bytes.allSatisfy({ $0 >= 32 && $0 < 127 }) {
        return "'\(String(bytes: bytes, encoding: .ascii) ?? "")' (\(status))"
    }
    return "\(status)"
}

func tapFormat(of tapID: AudioObjectID) -> AudioStreamBasicDescription? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioTapPropertyFormat,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var asbd = AudioStreamBasicDescription()
    var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
    let status = AudioObjectGetPropertyData(tapID, &address, 0, nil, &size, &asbd)
    return status == noErr ? asbd : nil
}

// MARK: - Create the tap (this is what triggers the system-audio TCC prompt)

let tapDescription = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
tapDescription.name = "Earshot system audio"
tapDescription.isPrivate = true
tapDescription.muteBehavior = .unmuted

var tapID = AudioObjectID(kAudioObjectUnknown)
let tapStatus = AudioHardwareCreateProcessTap(tapDescription, &tapID)
if tapStatus != noErr || tapID == kAudioObjectUnknown {
    // 560947818 = '!hog' style TCC denials come back as errors here; treat any
    // failure to create the system-wide tap as a permission problem first.
    fail("permission",
         "could not create the system audio tap (status \(fourCC(tapStatus))). " +
         "System audio recording permission is probably missing or denied.")
}
log("tap created: \(tapID)")

guard let inputASBD = tapFormat(of: tapID) else {
    AudioHardwareDestroyProcessTap(tapID)
    fail("tap_failed", "could not read the tap's stream format")
}
log("tap format: \(inputASBD.mSampleRate) Hz, \(inputASBD.mChannelsPerFrame) ch, flags \(inputASBD.mFormatFlags)")

// MARK: - Aggregate device wrapping the tap

let aggregateUID = UUID().uuidString
let aggregateDescription: [String: Any] = [
    kAudioAggregateDeviceNameKey as String: "Earshot tap aggregate",
    kAudioAggregateDeviceUIDKey as String: aggregateUID,
    kAudioAggregateDeviceIsPrivateKey as String: true,
    kAudioAggregateDeviceIsStackedKey as String: false,
    kAudioAggregateDeviceTapAutoStartKey as String: true,
    kAudioAggregateDeviceSubDeviceListKey as String: [] as [[String: Any]],
    kAudioAggregateDeviceTapListKey as String: [
        [kAudioSubTapUIDKey as String: tapDescription.uuid.uuidString]
    ],
]

var aggregateID = AudioObjectID(kAudioObjectUnknown)
let aggStatus = AudioHardwareCreateAggregateDevice(aggregateDescription as CFDictionary, &aggregateID)
if aggStatus != noErr || aggregateID == kAudioObjectUnknown {
    AudioHardwareDestroyProcessTap(tapID)
    fail("tap_failed", "could not create the aggregate device (status \(fourCC(aggStatus)))")
}
log("aggregate device created: \(aggregateID)")

// MARK: - Converter to the fixed on-disk format

guard let outputFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: OUT_RATE,
                                       channels: OUT_CHANNELS, interleaved: true) else {
    fail("tap_failed", "could not build the output format")
}

final class ConverterBox {
    var inputFormat: AVAudioFormat
    var converter: AVAudioConverter
    let lock = NSLock()

    init?(asbd: AudioStreamBasicDescription, output: AVAudioFormat) {
        var asbd = asbd
        guard let inFmt = AVAudioFormat(streamDescription: &asbd),
              let conv = AVAudioConverter(from: inFmt, to: output) else { return nil }
        self.inputFormat = inFmt
        self.converter = conv
    }

    func rebuild(asbd: AudioStreamBasicDescription, output: AVAudioFormat) {
        var asbd = asbd
        guard let inFmt = AVAudioFormat(streamDescription: &asbd),
              let conv = AVAudioConverter(from: inFmt, to: output) else { return }
        lock.lock()
        inputFormat = inFmt
        converter = conv
        lock.unlock()
    }
}

guard let box = ConverterBox(asbd: inputASBD, output: outputFormat) else {
    AudioHardwareDestroyAggregateDevice(aggregateID)
    AudioHardwareDestroyProcessTap(tapID)
    fail("tap_failed", "could not build a converter for the tap format")
}

// MARK: - Shared state

final class CaptureState {
    let lock = NSLock()
    var peakRMS: Double = 0
    var framesWritten: UInt64 = 0
    var ioError: String? = nil
    var nonzeroSeen = false
    var silenceHintLogged = false
}
let state = CaptureState()

// MARK: - IOProc: convert + spool every tap buffer

var ioProcID: AudioDeviceIOProcID? = nil

let ioBlock: AudioDeviceIOBlock = { _, inInputData, _, _, _ in
    box.lock.lock()
    let converter = box.converter
    let inputFormat = box.inputFormat
    box.lock.unlock()

    guard let inputBuffer = AVAudioPCMBuffer(pcmFormat: inputFormat,
                                             bufferListNoCopy: inInputData,
                                             deallocator: nil),
          inputBuffer.frameLength > 0 else { return }

    let ratio = OUT_RATE / inputFormat.sampleRate
    let capacity = AVAudioFrameCount(Double(inputBuffer.frameLength) * ratio) + 64
    guard let outputBuffer = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return }

    var fed = false
    var convError: NSError? = nil
    let status = converter.convert(to: outputBuffer, error: &convError) { _, outStatus in
        if fed {
            outStatus.pointee = .noDataNow
            return nil
        }
        fed = true
        outStatus.pointee = .haveData
        return inputBuffer
    }
    if status == .error {
        log("convert error: \(convError?.localizedDescription ?? "?")")
        return
    }
    let frames = Int(outputBuffer.frameLength)
    guard frames > 0, let samples = outputBuffer.int16ChannelData?[0] else { return }
    let sampleCount = frames * Int(OUT_CHANNELS)

    // RMS on the converted int16 (0..1)
    var acc: Double = 0
    for i in 0..<sampleCount {
        let v = Double(samples[i]) / 32768.0
        acc += v * v
    }
    let rms = (sampleCount > 0) ? (acc / Double(sampleCount)).squareRoot() : 0

    let data = Data(bytes: samples, count: sampleCount * MemoryLayout<Int16>.size)
    state.lock.lock()
    if rms > state.peakRMS { state.peakRMS = rms }
    if rms > 0 { state.nonzeroSeen = true }
    state.lock.unlock()
    do {
        try spoolFile.write(contentsOf: data)
        state.lock.lock()
        state.framesWritten += UInt64(frames)
        state.lock.unlock()
    } catch {
        state.lock.lock()
        let already = state.ioError != nil
        if !already { state.ioError = "spool write failed: \(error.localizedDescription)" }
        state.lock.unlock()
    }
}

let procStatus = AudioDeviceCreateIOProcIDWithBlock(&ioProcID, aggregateID, nil, ioBlock)
if procStatus != noErr || ioProcID == nil {
    AudioHardwareDestroyAggregateDevice(aggregateID)
    AudioHardwareDestroyProcessTap(tapID)
    fail("tap_failed", "could not install the IO proc (status \(fourCC(procStatus)))")
}

let startStatus = AudioDeviceStart(aggregateID, ioProcID)
if startStatus != noErr {
    AudioDeviceDestroyIOProcID(aggregateID, ioProcID!)
    AudioHardwareDestroyAggregateDevice(aggregateID)
    AudioHardwareDestroyProcessTap(tapID)
    fail("tap_failed", "could not start the aggregate device (status \(fourCC(startStatus)))")
}
log("capture started")
emit(["event": "start", "rate": Int(OUT_RATE), "channels": Int(OUT_CHANNELS), "format": "int16"])

// MARK: - Tap format changes (output device switched): rebuild the converter

var formatAddress = AudioObjectPropertyAddress(
    mSelector: kAudioTapPropertyFormat,
    mScope: kAudioObjectPropertyScopeGlobal,
    mElement: kAudioObjectPropertyElementMain)
let listenerQueue = DispatchQueue(label: "audiotap.listener")
AudioObjectAddPropertyListenerBlock(tapID, &formatAddress, listenerQueue) { _, _ in
    if let asbd = tapFormat(of: tapID) {
        log("tap format changed: \(asbd.mSampleRate) Hz, \(asbd.mChannelsPerFrame) ch; rebuilding converter")
        box.rebuild(asbd: asbd, output: outputFormat)
    }
}

// MARK: - Shutdown paths

var cleanedUp = false
func cleanupAndExit(_ code: Int32) {
    if cleanedUp { exit(code) }
    cleanedUp = true
    AudioDeviceStop(aggregateID, ioProcID)
    if let p = ioProcID { AudioDeviceDestroyIOProcID(aggregateID, p) }
    AudioHardwareDestroyAggregateDevice(aggregateID)
    AudioHardwareDestroyProcessTap(tapID)
    try? spoolFile.synchronize()
    try? spoolFile.close()
    state.lock.lock()
    let frames = state.framesWritten
    state.lock.unlock()
    emit(["event": "stop", "frames": frames])
    log("stopped cleanly after \(frames) frames")
    exit(code)
}

let signalQueue = DispatchQueue(label: "audiotap.signals")
signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)
let termSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: signalQueue)
termSource.setEventHandler { cleanupAndExit(0) }
termSource.resume()
let intSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: signalQueue)
intSource.setEventHandler { cleanupAndExit(0) }
intSource.resume()

// stdin EOF watchdog: if the parent app dies, the pipe closes and we stop on
// our own instead of recording forever.
let stdinSource = DispatchSource.makeReadSource(fileDescriptor: 0, queue: signalQueue)
stdinSource.setEventHandler {
    var buf = [UInt8](repeating: 0, count: 4096)
    let n = read(0, &buf, buf.count)
    if n <= 0 {
        log("stdin EOF; parent is gone, shutting down")
        cleanupAndExit(0)
    }
}
stdinSource.resume()

// Level events + IO error surfacing, ~8 Hz.
let levelTimer = DispatchSource.makeTimerSource(queue: signalQueue)
levelTimer.schedule(deadline: .now() + .milliseconds(125), repeating: .milliseconds(125))
levelTimer.setEventHandler {
    state.lock.lock()
    let rms = state.peakRMS
    state.peakRMS = 0
    let ioError = state.ioError
    state.ioError = nil
    state.lock.unlock()
    if let msg = ioError {
        emit(["event": "error", "code": "io", "message": msg])
    }
    emit(["event": "level", "rms": (rms * 1000).rounded() / 1000])
    // Unauthorized taps deliver buffers full of exact zeros instead of failing;
    // leave a diagnostic breadcrumb once if the first seconds are dead silent.
    state.lock.lock()
    let framesSoFar = state.framesWritten
    let anySignal = state.nonzeroSeen
    let hinted = state.silenceHintLogged
    if !hinted && !anySignal && framesSoFar > UInt64(OUT_RATE) * 3 {
        state.silenceHintLogged = true
        state.lock.unlock()
        log("3s of pure zeros so far: either nothing is playing, or System Audio " +
            "Recording permission is missing (unauthorized taps read as silence). " +
            "Check System Settings > Privacy & Security > Screen & System Audio Recording.")
    } else {
        state.lock.unlock()
    }
}
levelTimer.resume()

RunLoop.main.run()
