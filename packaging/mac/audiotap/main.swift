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

import AVFoundation
import CoreAudio
import Darwin
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
var requestMicrophone = false
var args = Array(CommandLine.arguments.dropFirst())
while !args.isEmpty {
    let a = args.removeFirst()
    switch a {
    case "--out":
        guard !args.isEmpty else { fail("args", "--out requires a path") }
        outPath = args.removeFirst()
    case "--request-microphone":
        requestMicrophone = true
    default:
        fail("args", "unknown argument: \(a)")
    }
}

if requestMicrophone {
    let status = AVCaptureDevice.authorizationStatus(for: .audio)
    switch status {
    case .authorized:
        emit(["event": "permission", "kind": "microphone", "granted": true])
        exit(0)
    case .denied, .restricted:
        emit(["event": "permission", "kind": "microphone", "granted": false])
        exit(2)
    case .notDetermined:
        let finished = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: .audio) { allowed in
            granted = allowed
            finished.signal()
        }
        if finished.wait(timeout: .now() + .seconds(120)) == .timedOut {
            emit(["event": "permission", "kind": "microphone", "granted": false,
                  "message": "microphone permission request timed out"])
            exit(3)
        }
        emit(["event": "permission", "kind": "microphone", "granted": granted])
        exit(granted ? 0 : 2)
    @unknown default:
        emit(["event": "permission", "kind": "microphone", "granted": false])
        exit(2)
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

// MARK: - Fixed output format

guard let outputFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: OUT_RATE,
                                       channels: OUT_CHANNELS, interleaved: true) else {
    fail("tap_failed", "could not build the output format")
}

// MARK: - Shared state

final class CaptureState {
    let lock = NSLock()
    var peakRMS: Double = 0
    var framesWritten: UInt64 = 0
    var ioError: String? = nil
    var ioFailures: UInt64 = 0
    var nonzeroSeen = false
    var silenceHintLogged = false
}
let state = CaptureState()

/// Every capture hiccup goes through here: counted (so level events can show
/// the parent when failures STOP happening and it can clear its warning),
/// latched for the next error event, and logged to stderr so audiotap.log
/// tells the whole story after the fact.
func reportIO(_ message: String) {
    state.lock.lock()
    state.ioFailures += 1
    let count = state.ioFailures
    if state.ioError == nil { state.ioError = message }
    state.lock.unlock()
    log("io failure #\(count): \(message)")
}

// MARK: - Real-time-safe input queue

private let QUEUE_SLOTS = 64
private let SLOT_BYTES = 512 * 1024
private let MAX_AUDIO_BUFFERS = 16

final class RawSlot {
    let storage = UnsafeMutableRawPointer.allocate(
        byteCount: SLOT_BYTES, alignment: MemoryLayout<UInt64>.alignment)
    var used = 0
    var frameCount: UInt32 = 0
    var format = AudioStreamBasicDescription()
    var bufferCount = 0
    var offsets = [Int](repeating: 0, count: MAX_AUDIO_BUFFERS)
    var sizes = [Int](repeating: 0, count: MAX_AUDIO_BUFFERS)

    deinit { storage.deallocate() }
}

struct CapturedBlock {
    let frameCount: UInt32
    let format: AudioStreamBasicDescription
    let buffers: [Data]
}

final class RawRing {
    private let lock = NSLock()
    private let available = DispatchSemaphore(value: 0)
    private let slots = (0..<QUEUE_SLOTS).map { _ in RawSlot() }
    private var writeIndex = 0
    private var readIndex = 0
    private var count = 0
    private var currentFormat: AudioStreamBasicDescription
    var dropped: Int32 = 0

    init(format: AudioStreamBasicDescription) { currentFormat = format }

    func updateFormat(_ format: AudioStreamBasicDescription) {
        lock.lock()
        currentFormat = format
        lock.unlock()
    }

    func enqueue(_ input: UnsafePointer<AudioBufferList>) {
        // Never wait from Core Audio's IOProc. Contention/full queue means an
        // explicit dropped-buffer error rather than risking the audio thread.
        guard lock.try() else {
            OSAtomicIncrement32Barrier(&dropped)
            return
        }
        defer { lock.unlock() }
        if count == slots.count {
            OSAtomicIncrement32Barrier(&dropped)
            return
        }
        let source = UnsafeMutableAudioBufferListPointer(
            UnsafeMutablePointer(mutating: input))
        if source.count > MAX_AUDIO_BUFFERS {
            OSAtomicIncrement32Barrier(&dropped)
            return
        }
        let slot = slots[writeIndex]
        var offset = 0
        for (index, buffer) in source.enumerated() {
            let size = Int(buffer.mDataByteSize)
            guard let data = buffer.mData, offset + size <= SLOT_BYTES else {
                OSAtomicIncrement32Barrier(&dropped)
                return
            }
            slot.storage.advanced(by: offset).copyMemory(from: data, byteCount: size)
            slot.offsets[index] = offset
            slot.sizes[index] = size
            offset += size
        }
        slot.used = offset
        slot.frameCount = source.first.map { currentFormat.mBytesPerFrame > 0
            ? $0.mDataByteSize / currentFormat.mBytesPerFrame : 0 } ?? 0
        slot.format = currentFormat
        slot.bufferCount = source.count
        writeIndex = (writeIndex + 1) % slots.count
        count += 1
        available.signal()
    }

    func dequeue(timeout: DispatchTime) -> CapturedBlock? {
        guard available.wait(timeout: timeout) == .success else { return nil }
        lock.lock()
        defer { lock.unlock() }
        guard count > 0 else { return nil }
        let slot = slots[readIndex]
        var copied: [Data] = []
        copied.reserveCapacity(slot.bufferCount)
        for index in 0..<slot.bufferCount {
            copied.append(Data(
                bytes: slot.storage.advanced(by: slot.offsets[index]),
                count: slot.sizes[index]))
        }
        let block = CapturedBlock(
            frameCount: slot.frameCount, format: slot.format, buffers: copied)
        readIndex = (readIndex + 1) % slots.count
        count -= 1
        return block
    }

    var hasPending: Bool {
        lock.lock()
        defer { lock.unlock() }
        return count > 0
    }

    func wake() { available.signal() }
}

let ring = RawRing(format: inputASBD)
var workerShouldStop: Int32 = 0
let workerGroup = DispatchGroup()
let workerQueue = DispatchQueue(label: "audiotap.writer", qos: .userInteractive)

func sameFormat(_ a: AudioStreamBasicDescription,
                _ b: AudioStreamBasicDescription) -> Bool {
    return a.mSampleRate == b.mSampleRate &&
        a.mFormatID == b.mFormatID &&
        a.mFormatFlags == b.mFormatFlags &&
        a.mBytesPerPacket == b.mBytesPerPacket &&
        a.mFramesPerPacket == b.mFramesPerPacket &&
        a.mBytesPerFrame == b.mBytesPerFrame &&
        a.mChannelsPerFrame == b.mChannelsPerFrame &&
        a.mBitsPerChannel == b.mBitsPerChannel
}

workerGroup.enter()
workerQueue.async {
    var activeASBD = AudioStreamBasicDescription()
    var inputFormat: AVAudioFormat? = nil
    var converter: AVAudioConverter? = nil
    while OSAtomicAdd32Barrier(0, &workerShouldStop) == 0 || ring.hasPending {
        guard let block = ring.dequeue(timeout: .now() + .milliseconds(100)) else { continue }
        if inputFormat == nil || !sameFormat(activeASBD, block.format) {
            var format = block.format
            inputFormat = AVAudioFormat(streamDescription: &format)
            converter = inputFormat.flatMap { AVAudioConverter(from: $0, to: outputFormat) }
            activeASBD = block.format
        }
        guard let inFormat = inputFormat, let activeConverter = converter,
              let inputBuffer = AVAudioPCMBuffer(
                pcmFormat: inFormat, frameCapacity: AVAudioFrameCount(block.frameCount)) else {
            reportIO("could not build an input audio buffer")
            continue
        }
        inputBuffer.frameLength = AVAudioFrameCount(block.frameCount)
        let destination = UnsafeMutableAudioBufferListPointer(inputBuffer.mutableAudioBufferList)
        guard destination.count == block.buffers.count else {
            reportIO("tap buffer layout changed unexpectedly")
            continue
        }
        var layoutOK = true
        for index in 0..<destination.count {
            let data = block.buffers[index]
            guard let target = destination[index].mData,
                  data.count <= Int(destination[index].mDataByteSize) else {
                layoutOK = false
                break
            }
            data.copyBytes(to: target.assumingMemoryBound(to: UInt8.self), count: data.count)
            destination[index].mDataByteSize = UInt32(data.count)
        }
        if !layoutOK {
            reportIO("tap input exceeded the conversion buffer")
            continue
        }

        let ratio = OUT_RATE / inFormat.sampleRate
        let capacity = AVAudioFrameCount(Double(inputBuffer.frameLength) * ratio) + 64
        guard let outputBuffer = AVAudioPCMBuffer(
            pcmFormat: outputFormat, frameCapacity: capacity) else { continue }
        var fed = false
        var convError: NSError? = nil
        let conversionStatus = activeConverter.convert(
            to: outputBuffer, error: &convError) { _, outStatus in
            if fed {
                outStatus.pointee = .noDataNow
                return nil
            }
            fed = true
            outStatus.pointee = .haveData
            return inputBuffer
        }
        if conversionStatus == .error {
            reportIO("audio conversion failed: \(convError?.localizedDescription ?? "unknown error")")
            continue
        }
        let frames = Int(outputBuffer.frameLength)
        guard frames > 0, let samples = outputBuffer.int16ChannelData?[0] else { continue }
        let sampleCount = frames * Int(OUT_CHANNELS)
        var acc: Double = 0
        for index in 0..<sampleCount {
            let value = Double(samples[index]) / 32768.0
            acc += value * value
        }
        let rms = (acc / Double(sampleCount)).squareRoot()
        let data = Data(bytes: samples, count: sampleCount * MemoryLayout<Int16>.size)
        do {
            try spoolFile.write(contentsOf: data)
            state.lock.lock()
            if rms > state.peakRMS { state.peakRMS = rms }
            if rms > 0 { state.nonzeroSeen = true }
            state.framesWritten += UInt64(frames)
            state.lock.unlock()
        } catch {
            reportIO("spool write failed: \(error.localizedDescription)")
        }
    }
    workerGroup.leave()
}

// MARK: - IOProc: copy each tap buffer into the bounded queue

var ioProcID: AudioDeviceIOProcID? = nil

let ioBlock: AudioDeviceIOBlock = { _, inInputData, _, _, _ in
    ring.enqueue(inInputData)
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
emit(["event": "start", "rate": Int(OUT_RATE), "channels": Int(OUT_CHANNELS),
      "format": "int16", "started_uptime": ProcessInfo.processInfo.systemUptime])

// MARK: - Tap format changes (output device switched): rebuild the converter

var formatAddress = AudioObjectPropertyAddress(
    mSelector: kAudioTapPropertyFormat,
    mScope: kAudioObjectPropertyScopeGlobal,
    mElement: kAudioObjectPropertyElementMain)
let listenerQueue = DispatchQueue(label: "audiotap.listener")
AudioObjectAddPropertyListenerBlock(tapID, &formatAddress, listenerQueue) { _, _ in
    if let asbd = tapFormat(of: tapID) {
        log("tap format changed: \(asbd.mSampleRate) Hz, \(asbd.mChannelsPerFrame) ch")
        ring.updateFormat(asbd)
    }
}

// MARK: - Shutdown paths

var cleanedUp = false
func cleanupAndExit(_ code: Int32) {
    if cleanedUp { exit(code) }
    cleanedUp = true
    AudioDeviceStop(aggregateID, ioProcID)
    OSAtomicIncrement32Barrier(&workerShouldStop)
    ring.wake()
    _ = workerGroup.wait(timeout: .now() + .seconds(5))
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
    let dropped = OSAtomicAdd32Barrier(0, &ring.dropped)
    if dropped > 0 {
        _ = OSAtomicAdd32Barrier(-dropped, &ring.dropped)
        // Counted + stderr-logged like any other hiccup so the parent can tell
        // a transient stall (failures stop growing) from an ongoing problem.
        reportIO("system audio callback dropped \(dropped) buffers")
        emit(["event": "error", "code": "overflow",
              "message": "system audio callback dropped \(dropped) buffers"])
    }
    if let msg = ioError {
        emit(["event": "error", "code": "io", "message": msg])
    }
    state.lock.lock()
    let framesNow = state.framesWritten
    let failuresNow = state.ioFailures
    state.lock.unlock()
    // frames/failures let the parent verify capture is healthy again after a
    // transient failure and clear its recording-problem warning.
    emit(["event": "level", "rms": (rms * 1000).rounded() / 1000,
          "frames": framesNow, "failures": failuresNow])
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
        emit(["event": "warning", "code": "permission_silence"])
    } else {
        state.lock.unlock()
    }
}
levelTimer.resume()

RunLoop.main.run()
