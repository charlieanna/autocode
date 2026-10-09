import SwiftUI
import AppKit

struct ContentView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        Group {
            switch model.server.phase {
            case .ready:
                readyView
            case .failed(let message):
                failureView(message)
            case .starting, .idle:
                ProgressView()
                    .scaleEffect(1.2)
                    .padding()
                Text(startingText)
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(minWidth: 1024, minHeight: 660)
    }

    private var startingText: String {
        "Starting the Autocode dashboard — every chat and task action runs through autopilot…"
    }

    private var readyView: some View {
        VStack(spacing: 0) {
            WebView()
            Divider()
            HStack(spacing: 8) {
                Image(systemName: "circle.fill")
                    .font(.system(size: 8))
                    .foregroundStyle(.green)
                Text(statusLine)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                if let error = model.webStore.navigationError {
                    Text(error)
                        .font(.callout)
                        .foregroundStyle(.red)
                        .lineLimit(1)
                        .help(error)
                    Button("Reload") { model.webStore.reload() }
                }
                Button("Open in Browser") {
                    if let url = model.server.url {
                        NSWorkspace.shared.open(url)
                    }
                }
                Button("Restart Server") { model.server.restart() }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
        }
    }

    private var statusLine: String {
        var parts = ["Autopilot runner"]
        if let url = model.server.url {
            parts.append(url.absoluteString)
        }
        if !model.server.interpreterNote.isEmpty {
            parts.append("· python: \(model.server.interpreterNote)")
        }
        return parts.joined(separator: " ")
    }

    private func failureView(_ message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 40))
                .foregroundStyle(.orange)
            Text("The dashboard server could not start")
                .font(.title2)
            Text(message)
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 520)
            HStack {
                Button("Retry") { model.server.start() }
                    .keyboardShortcut(.defaultAction)
                Button("Open Settings") {
                    NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
                }
                Button("Show Log") {
                    NSWorkspace.shared.activateFileViewerSelecting([DashboardServer.logFileURL()])
                }
            }
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
