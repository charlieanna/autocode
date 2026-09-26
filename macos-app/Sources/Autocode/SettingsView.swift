import SwiftUI
import AppKit

struct SettingsView: View {
    @EnvironmentObject var model: AppModel

    @State private var checkout = ""
    @State private var portText = ""
    @State private var message: String?

    var body: some View {
        Form {
            Section {
                HStack {
                    TextField("Autocode checkout", text: $checkout)
                    Button("Choose…") { chooseFolder() }
                }
                if !checkout.isEmpty {
                    Text(DashboardServer.isCheckout(checkout)
                         ? "Checkout verified: dashboard and autopilot found."
                         : "Not an Autocode checkout: tools/dashboard/agent_console.py or tools/autopilot.py is missing.")
                        .font(.caption)
                        .foregroundStyle(DashboardServer.isCheckout(checkout) ? Color.secondary : Color.orange)
                }
            } header: {
                Text("Autocode checkout")
            }

            Section {
                TextField("Port (0 = pick a free port automatically)", text: $portText)
            } header: {
                Text("Server")
            }

            Section {
                Text("Every task conversation message, answer, approval, feedback request and continue action is executed as `python tools/autopilot.py …` from this checkout. The dashboard server itself never launches agents for status reads.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                Text("Server log: \(DashboardServer.logFileURL().path)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            } header: {
                Text("Chat routing")
            }

            if let message {
                Text(message)
                    .foregroundStyle(.red)
                    .font(.callout)
            }

            Button("Save & Restart Server") { save() }
        }
        .formStyle(.grouped)
        .frame(width: 520, height: 420)
        .onAppear {
            checkout = model.server.checkout
            portText = String(model.server.port)
        }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = "Choose the Autocode checkout (the repository containing tools/autopilot.py)"
        if panel.runModal() == .OK, let url = panel.url {
            checkout = url.path
        }
    }

    private func save() {
        let trimmed = checkout.trimmingCharacters(in: .whitespaces)
        guard DashboardServer.isCheckout(trimmed) else {
            message = "That folder is not an Autocode checkout."
            return
        }
        guard let port = Int(portText.trimmingCharacters(in: .whitespaces)), port >= 0, port <= 65535 else {
            message = "Port must be a number between 0 and 65535 (0 picks a free port)."
            return
        }
        message = nil
        model.server.checkout = trimmed
        model.server.port = port
        model.server.restart()
    }
}
