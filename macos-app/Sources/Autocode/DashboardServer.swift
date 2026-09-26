import Foundation
import AppKit

enum ServerPhase: Equatable {
    case idle
    case starting
    case ready
    case failed(String)
}

struct ServerFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Writes to the shared log file from the stdout reader thread; closing is
/// synchronized so a late pipe chunk after shutdown cannot touch a closed handle.
final class LogWriter: @unchecked Sendable {
    private let lock = NSLock()
    private var handle: FileHandle?

    init(_ handle: FileHandle) {
        self.handle = handle
    }

    func write(_ data: Data) {
        lock.lock()
        defer { lock.unlock() }
        if let handle { handle.write(data) }
    }

    func close() {
        lock.lock()
        defer { lock.unlock() }
        try? handle?.close()
        handle = nil
    }
}

/// Owns the local dashboard server process. Every chat and run operation the
/// dashboard performs is executed by the runner script we pass via --runner,
/// which is always the checkout's autopilot controller: talking to a task in
/// the app means talking to `python tools/autopilot.py ...`.
@MainActor
final class DashboardServer: ObservableObject {
    static let checkoutKey = "autocode.checkoutPath"
    static let portKey = "autocode.port"

    @Published private(set) var phase: ServerPhase = .idle {
        didSet { NSLog("[autocode] phase: \(oldValue) -> \(phase)") }
    }
    @Published private(set) var url: URL?
    @Published private(set) var interpreterNote = ""

    private var process: Process?
    private var logWriter: LogWriter?
    private var launchGeneration = 0
    private var startAttempts = 0

    let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        if defaults.string(forKey: Self.checkoutKey) == nil {
            defaults.set(Self.resolveDefaultCheckout(), forKey: Self.checkoutKey)
        }
        if defaults.object(forKey: Self.portKey) == nil {
            defaults.set(0, forKey: Self.portKey)
        }
    }

    var checkout: String {
        get { defaults.string(forKey: Self.checkoutKey) ?? "" }
        set { defaults.set(newValue, forKey: Self.checkoutKey) }
    }

    /// 0 lets the server pick a free ephemeral port (it prints the real URL).
    var port: Int {
        get { defaults.object(forKey: Self.portKey) as? Int ?? 0 }
        set { defaults.set(max(0, min(65535, newValue)), forKey: Self.portKey) }
    }

    var resolvedCheckout: String { (checkout as NSString).expandingTildeInPath }

    // MARK: - Lifecycle

    func start() {
        launchGeneration += 1
        startAttempts += 1
        let generation = launchGeneration
        phase = .starting
        url = nil
        let rawCheckout = resolvedCheckout
        Task { [weak self] in
            guard let self else { return }
            do {
                guard Self.isCheckout(rawCheckout) else {
                    throw ServerFailure(message: "\(rawCheckout) is not an Autocode checkout (tools/dashboard/agent_console.py and tools/autopilot.py are missing). Set the checkout path in Settings.")
                }
                let interpreter = await Task.detached(priority: .userInitiated) {
                    Self.selectInterpreter(forCheckout: rawCheckout)
                }.value
                guard let interpreter else {
                    throw ServerFailure(message: "No Python interpreter with psutil was found. Use the checkout's virtual environment (python3 -m venv .venv && .venv/bin/pip install .) or install autocode with pipx, then restart the server.")
                }
                guard generation == self.launchGeneration else { return }
                self.interpreterNote = interpreter.note
                // Reap servers left behind by a crashed app instance. Only
                // processes running OUR runner whose parent is launchd are
                // orphans; terminal-launched dashboards keep their shell parent
                // and other live instances keep their app parent.
                await Task.detached(priority: .utility) {
                    Self.killOrphanedServers(runnerPath: rawCheckout + "/tools/autopilot.py")
                }.value
                guard generation == self.launchGeneration else { return }
                let log = try Self.openLog()
                try self.launch(checkout: rawCheckout, python: interpreter.path, log: log, generation: generation)
            } catch {
                if generation == self.launchGeneration {
                    self.phase = .failed(error.localizedDescription)
                }
            }
        }
    }

    func restart() {
        stop()
        start()
    }

    /// Stops only the dashboard server. Runner subprocesses live in their own
    /// process groups and keep working; their state is saved in each run.
    func stop() {
        launchGeneration += 1
        if let proc = process, proc.isRunning {
            proc.terminate()
            let pid = proc.processIdentifier
            DispatchQueue.global().asyncAfter(deadline: .now() + 3) {
                kill(pid, SIGKILL)
            }
        }
        process = nil
        logWriter?.close()
        logWriter = nil
        phase = .idle
        url = nil
    }

    private func launch(checkout: String, python: String, log: FileHandle, generation: Int) throws {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.currentDirectoryURL = URL(fileURLWithPath: checkout)
        proc.arguments = [
            checkout + "/tools/dashboard/agent_console.py",
            "--runner", checkout + "/tools/autopilot.py",
            "--port", String(port),
        ]
        proc.environment = Self.environment()
        proc.qualityOfService = .userInitiated

        let pipe = Pipe()
        proc.standardInput = FileHandle.nullDevice
        proc.standardOutput = pipe
        proc.standardError = pipe

        let writer = LogWriter(log)
        logWriter = writer
        let scanner = LineScanner { [weak self] line in
            guard let found = Self.firstURL(in: line) else { return }
            DispatchQueue.main.async {
                self?.serverPrinted(url: found, generation: generation)
            }
        }
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let chunk = handle.availableData
            if chunk.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            writer.write(chunk)
            scanner.feed(chunk)
        }

        try proc.run()
        process = proc
        NSLog("[autocode] server launched pid \(proc.processIdentifier) port-arg \(self.port)")
        Task.detached(priority: .utility) { [weak self] in
            proc.waitUntilExit()
            let code = proc.terminationStatus
            await self?.processExited(code: code, generation: generation)
        }
        startupWatchdog(generation: generation)
    }

    /// If the server never reports its URL, retry once automatically (the
    /// occasional Python startup stall is transient), then fail visibly.
    private func startupWatchdog(generation: Int) {
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 25_000_000_000)
            guard let self, generation == self.launchGeneration, self.url == nil else { return }
            self.stop()
            if self.startAttempts < 3 {
                NSLog("[autocode] no URL after 25s; automatic retry \(self.startAttempts)/3")
                self.start()
            } else {
                self.phase = .failed("The dashboard server did not report its address after three attempts. Use Retry — if it keeps failing, check \(Self.logFileURL().path).")
            }
        }
    }

    private func serverPrinted(url found: URL, generation: Int) {
        guard generation == launchGeneration, url == nil else { return }
        url = found
        startAttempts = 0
        // The server prints its URL only after binding and entering
        // serve_forever, so this is the authoritative readiness signal.
        phase = .ready
        verify(target: found, generation: generation)
    }

    /// Non-gating HTTP verification: failures are logged for diagnosis but do
    /// not flip the phase, because the loopback URL was already printed by a
    /// live server.
    private func verify(target: URL, generation: Int) {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 3
        let session = URLSession(configuration: config)
        Task { [weak self] in
            for attempt in 0..<10 {
                try? await Task.sleep(nanoseconds: 300_000_000)
                guard let self, generation == self.launchGeneration else { return }
                do {
                    let (_, response) = try await session.data(from: target)
                    let code = (response as? HTTPURLResponse)?.statusCode ?? -1
                    NSLog("[autocode] verify attempt \(attempt): HTTP \(code)")
                    return
                } catch {
                    NSLog("[autocode] verify attempt \(attempt) failed: \(error.localizedDescription)")
                }
            }
        }
    }

    private func processExited(code: Int32, generation: Int) {
        guard generation == launchGeneration else { return }
        process = nil
        switch phase {
        case .ready:
            phase = .failed("The dashboard server exited unexpectedly (status \(code)). Use Server ▸ Restart Dashboard Server. Runner tasks are independent processes; their work is saved in each run.")
        case .starting:
            phase = .failed("The dashboard server exited during startup (status \(code)). The combined server log is at \(Self.logFileURL().path).")
        default:
            break
        }
    }

    // MARK: - Discovery helpers (blocking; keep off the main thread)

    nonisolated static func isCheckout(_ raw: String) -> Bool {
        let base = URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
        let manager = FileManager.default
        return manager.fileExists(atPath: base.appendingPathComponent("tools/dashboard/agent_console.py").path)
            && manager.fileExists(atPath: base.appendingPathComponent("tools/autopilot.py").path)
    }

    nonisolated static func resolveDefaultCheckout() -> String {
        var candidates = [BuildConfig.defaultCheckout]
        if BuildConfig.defaultCheckout.isEmpty {
            candidates.append(NSHomeDirectory() + "/Documents/workspace/autocode")
        }
        return candidates.first { !$0.isEmpty && isCheckout($0) } ?? candidates.first { !$0.isEmpty } ?? ""
    }

    /// Prefer the checkout's own virtual environment, then the pipx
    /// autocode-supervisor environment, then any python3 that can import psutil.
    /// Each probe is watchdogged: a stalled spawn fails over to the next
    /// candidate instead of hanging the app.
    nonisolated static func selectInterpreter(forCheckout checkout: String) -> (path: String, note: String)? {
        let venv = URL(fileURLWithPath: checkout).appendingPathComponent(".venv/bin/python").path
        let pipx = NSHomeDirectory() + "/.local/pipx/venvs/autocode-supervisor/bin/python"
        if probe(venv) { return (venv, "checkout .venv") }
        if probe(pipx) { return (pipx, "pipx autocode-supervisor venv") }
        for path in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"] {
            if probe(path) { return (path, path) }
        }
        return nil
    }

    nonisolated private static func probe(_ python: String) -> Bool {
        guard FileManager.default.isExecutableFile(atPath: python) else { return false }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = ["-c", "import psutil"]
        proc.standardInput = FileHandle.nullDevice
        proc.standardOutput = Pipe()
        proc.standardError = Pipe()
        do {
            try proc.run()
        } catch {
            return false
        }
        let deadline = Date().addingTimeInterval(6)
        while proc.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
            return false
        }
        return proc.terminationStatus == 0
    }

    /// The app can be launched from Finder/Dock, where the environment is
    /// launchd's minimal one. The dashboard and its autopilot runner need git,
    /// opencode and friends on PATH, so make sure the usual local bin
    /// directories are present alongside the inherited PATH.
    nonisolated static func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        var path = env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"
        let parts = path.split(separator: ":").map(String.init)
        for extra in [NSHomeDirectory() + "/.local/bin", "/opt/homebrew/bin", "/usr/local/bin"]
        where !parts.contains(extra) {
            path += ":" + extra
        }
        env["PATH"] = path
        if env["HOME"] == nil { env["HOME"] = NSHomeDirectory() }
        return env
    }

    nonisolated static func killOrphanedServers(runnerPath: String) {
        let pgrep = Process()
        pgrep.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        pgrep.arguments = ["-f", "agent_console\\.py --runner " + runnerPath]
        let out = Pipe()
        pgrep.standardOutput = out
        pgrep.standardError = FileHandle.nullDevice
        pgrep.standardInput = FileHandle.nullDevice
        do {
            try pgrep.run()
        } catch {
            return
        }
        pgrep.waitUntilExit()
        guard pgrep.terminationStatus == 0,
              let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) else { return }
        for candidate in text.split(whereSeparator: \.isWhitespace) {
            guard let pid = Int32(candidate), pid > 1, parentPID(of: pid) == 1 else { continue }
            NSLog("[autocode] reaping orphaned dashboard server pid \(pid)")
            kill(pid, SIGTERM)
        }
    }

    nonisolated private static func parentPID(of pid: Int32) -> Int32 {
        let ps = Process()
        ps.executableURL = URL(fileURLWithPath: "/bin/ps")
        ps.arguments = ["-o", "ppid=", "-p", String(pid)]
        let out = Pipe()
        ps.standardOutput = out
        ps.standardError = FileHandle.nullDevice
        ps.standardInput = FileHandle.nullDevice
        do {
            try ps.run()
        } catch {
            return -1
        }
        ps.waitUntilExit()
        guard ps.terminationStatus == 0,
              let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) else { return -1 }
        return Int32(text.trimmingCharacters(in: .whitespacesAndNewlines)) ?? -1
    }

    nonisolated static func logFileURL() -> URL {
        URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent(".autocode/macos-app/dashboard.log")
    }

    nonisolated private static func openLog() throws -> FileHandle {
        let url = logFileURL()
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        let handle = try FileHandle(forWritingTo: url)
        handle.seekToEndOfFile()
        return handle
    }

    nonisolated static func firstURL(in line: String) -> URL? {
        let prefix = "http://127.0.0.1:"
        guard let range = line.range(of: prefix) else { return nil }
        var digits = ""
        for character in line[range.upperBound...] {
            guard character.isNumber else { break }
            digits.append(character)
        }
        guard !digits.isEmpty else { return nil }
        return URL(string: prefix + digits)
    }
}

/// Splits an arbitrary stdout chunk stream into complete lines.
final class LineScanner {
    private var buffer = Data()
    private let onLine: (String) -> Void

    init(onLine: @escaping (String) -> Void) {
        self.onLine = onLine
    }

    func feed(_ chunk: Data) {
        buffer.append(chunk)
        while let newline = buffer.firstIndex(of: UInt8(ascii: "\n")) {
            let lineData = buffer.subdata(in: buffer.startIndex..<newline)
            buffer.removeSubrange(buffer.startIndex...newline)
            if let line = String(data: lineData, encoding: .utf8) {
                onLine(line.trimmingCharacters(in: .whitespacesAndNewlines))
            }
        }
    }
}
