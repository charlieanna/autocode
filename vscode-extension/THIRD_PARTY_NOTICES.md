# Kilo Code chat adaptation

The composer layout, auto-resize, keyboard undo handling and transcript scroll
behavior in `src/upstream/kilo-chat.ts` are adapted from Kilo Code revision
`1552877e82ceaecc79a9fd4be7859518745e6f56`:

- [PromptInput.tsx](https://github.com/Kilo-Org/kilocode/blob/1552877e82ceaecc79a9fd4be7859518745e6f56/packages/kilo-vscode/webview-ui/src/components/chat/PromptInput.tsx)
- [prompt-input-utils.ts](https://github.com/Kilo-Org/kilocode/blob/1552877e82ceaecc79a9fd4be7859518745e6f56/packages/kilo-vscode/webview-ui/src/components/chat/prompt-input-utils.ts)
- [prompt-input.css](https://github.com/Kilo-Org/kilocode/blob/1552877e82ceaecc79a9fd4be7859518745e6f56/packages/kilo-vscode/webview-ui/src/styles/prompt-input.css)
- [chat-layout.css](https://github.com/Kilo-Org/kilocode/blob/1552877e82ceaecc79a9fd4be7859518745e6f56/packages/kilo-vscode/webview-ui/src/styles/chat-layout.css)
- [create-auto-scroll.tsx](https://github.com/Kilo-Org/kilocode/blob/1552877e82ceaecc79a9fd4be7859518745e6f56/packages/kilo-ui/src/hooks/create-auto-scroll.tsx)

Changes: converted the selected behavior from Solid effects to DOM events and
observers; replaced Kilo session/provider calls with AutoCode's public status and
named actions; omitted unsupported attachments, tool execution, automatic
approvals and session editing. Theme variables use VS Code fallbacks.
This is a component adaptation, not the full Kilo extension.

MIT License

Copyright (c) 2026 Kilo Code
Copyright (c) 2025 opencode

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
