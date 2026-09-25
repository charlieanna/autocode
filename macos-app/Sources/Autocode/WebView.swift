import SwiftUI
import WebKit

@MainActor
final class WebViewStore: ObservableObject {
    weak var webView: WKWebView?
    @Published var navigationError: String?

    func reload() {
        webView?.reload()
    }
}

final class NavigationDelegate: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
    let store: WebViewStore

    init(store: WebViewStore) {
        self.store = store
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        NSLog("[autocode] page console.\(message.name): \(message.body)")
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        NSLog("[autocode] navigation started -> \(webView.url?.absoluteString ?? "?")")
    }

    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        NSLog("[autocode] navigation committed: \(webView.url?.absoluteString ?? "?")")
        DispatchQueue.main.async { self.store.navigationError = nil }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        NSLog("[autocode] navigation finished: \(webView.url?.absoluteString ?? "?")")
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        NSLog("[autocode] navigation failed: \(error.localizedDescription)")
        DispatchQueue.main.async { self.store.navigationError = error.localizedDescription }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        NSLog("[autocode] provisional navigation failed: \(error.localizedDescription)")
        DispatchQueue.main.async { self.store.navigationError = error.localizedDescription }
    }
}

struct WebView: NSViewRepresentable {
    @EnvironmentObject var model: AppModel

    func makeCoordinator() -> NavigationDelegate {
        NavigationDelegate(store: model.webStore)
    }

    func makeNSView(context: Context) -> WKWebView {
        NSLog("[autocode] makeNSView")
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        let console = WKUserContentController()
        console.add(context.coordinator, name: "log")
        console.add(context.coordinator, name: "error")
        console.addUserScript(WKUserScript(
            source: """
            (function () {
                var forward = function (name) { return function () {
                    try { window.webkit.messageHandlers[name].postMessage(Array.from(arguments).join(' ').slice(0, 2000)); } catch (e) {}
                }; };
                console.log = forward('log');
                console.error = forward('error');
                console.warn = forward('error');
                window.addEventListener('error', function (e) {
                    try { window.webkit.messageHandlers.error.postMessage('window error: ' + e.message + ' at ' + (e.filename || '') + ':' + e.lineno); } catch (err) {}
                });
            })();
            """,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        ))
        configuration.userContentController = console
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.allowsBackForwardNavigationGestures = true
        view.navigationDelegate = context.coordinator
        model.webStore.webView = view
        if let target = model.server.url {
            NSLog("[autocode] loading \(target.absoluteString)")
            view.load(URLRequest(url: target))
        }
        return view
    }

    /// The dashboard rewrites its own URL fragment (#inbox, #run=…). Reload
    /// only when the server origin actually changed (e.g. after a restart),
    /// never because the page navigated within itself.
    private func sameOrigin(_ a: URL?, _ b: URL?) -> Bool {
        guard let a, let b else { return false }
        return a.scheme == b.scheme && a.host == b.host && a.port == b.port
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        guard let target = model.server.url else { return }
        if view.url == nil || !sameOrigin(view.url, target) {
            NSLog("[autocode] updateNSView loading \(target.absoluteString)")
            view.load(URLRequest(url: target))
        }
    }
}
