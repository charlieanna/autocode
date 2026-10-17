// Only VS Code's host APIs are substituted. Tests load the shipped controller,
// registered commands and generated webview script without starting VS Code/models.
const Module = require('node:module');
const path = require('node:path');

function editor() {
  const config = new Map();
  const commands = new Map();
  const panels = [];
  const errors = [];
  const providers = new Map();
  const storage = new Map();
  const workspaceState = { get: (key) => storage.get(key), update: async (key, value) => { storage.set(key, value); } };
  const log = { show() {}, append() {}, appendLine() {}, dispose() {} };
  const vscode = {
    workspace: { getConfiguration: () => ({ get: (key, fallback) => config.get(key) ?? fallback }) },
    window: {
      createOutputChannel: () => log,
      createStatusBarItem: () => ({ hide() {}, show() {}, dispose() {} }),
      createTreeView: () => ({ dispose() {} }),
      registerWebviewViewProvider: (id, provider) => { providers.set(id, provider); return { dispose() {} }; },
      showInformationMessage() {},
      showErrorMessage: async (message) => { errors.push(message); },
      showWarningMessage: async (_message, _options, choice) => choice,
      createWebviewPanel: (_type, title) => {
        let receive;
        let onDispose;
        let onViewState;
        const panel = {
          visible: true, title,
          onDidDispose: (callback) => { onDispose = callback; },
          onDidChangeViewState: (callback) => { onViewState = callback; },
          setVisible(visible) { this.visible = visible; onViewState?.({ webviewPanel: this }); },
          reveal() { this.setVisible(true); },
          dispose: () => onDispose?.(),
          webview: {
            html: '', messages: [],
            onDidReceiveMessage: (callback) => { receive = callback; },
            postMessage(message) { this.messages.push(message); this.deliver?.(message); return Promise.resolve(true); },
            receive: (message) => receive?.(message),
          },
        };
        panels.push(panel);
        return panel;
      },
    },
    commands: {
      registerCommand: (name, handler) => { commands.set(name, handler); return { dispose() {} }; },
      executeCommand: async () => {},
    },
    StatusBarAlignment: { Left: 1 }, ViewColumn: { One: 1 },
    TreeItem: class {}, TreeItemCollapsibleState: { None: 0, Expanded: 2 },
    Uri: { file: (value) => value }, MarkdownString: class {}, ThemeIcon: class {}, ThemeColor: class {},
    EventEmitter: class {
      listeners = new Set();
      event = (listener) => { this.listeners.add(listener); return { dispose: () => this.listeners.delete(listener) }; };
      fire(value) { for (const listener of this.listeners) listener(value); }
      dispose() { this.listeners.clear(); }
    },
  };
  function load(name) {
    const original = Module._load;
    Module._load = function (id) { return id === 'vscode' ? vscode : original.apply(this, arguments); };
    try { return require(path.resolve(__dirname, '../../out-test/src', name)); }
    finally { Module._load = original; }
  }
  return { config, commands, panels, providers, workspaceState, errors, log, load, vscode };
}

module.exports = { editor, flush: () => new Promise((resolve) => setImmediate(resolve)) };
