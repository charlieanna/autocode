import SwiftUI
import AppKit
import Combine

@MainActor
final class AppModel: ObservableObject {
    static var shared: AppModel?

    let server: DashboardServer
    let webStore = WebViewStore()
    private var cancellables: Set<AnyCancellable> = []

    init() {
        NSLog("[autocode] app model init")
        server = DashboardServer()
        // Views observe AppModel, so forward the server's and web store's
        // published changes through it — otherwise the UI never re-renders on
        // phase transitions.
        server.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        webStore.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        server.start()
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationWillTerminate(_ notification: Notification) {
        // Only the dashboard server dies with the app. Runner processes run in
        // their own process groups and keep their saved state.
        AppModel.shared?.server.stop()
    }
}

@main
struct AutocodeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model: AppModel

    init() {
        let created = AppModel()
        _model = StateObject(wrappedValue: created)
        AppModel.shared = created
    }

    var body: some Scene {
        WindowGroup("Autopilot") {
            ContentView()
                .environmentObject(model)
        }
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(after: .newItem) {
                Button("Reload Page") {
                    model.webStore.reload()
                }
                .keyboardShortcut("r", modifiers: .command)
                Button("Open in Browser…") {
                    if let url = model.server.url {
                        NSWorkspace.shared.open(url)
                    }
                }
                .keyboardShortcut("b", modifiers: [.command, .shift])
                Divider()
                Button("Restart Dashboard Server") {
                    model.server.restart()
                }
                .keyboardShortcut("r", modifiers: [.command, .shift])
            }
        }
        Settings {
            SettingsView()
                .environmentObject(model)
        }
    }
}
