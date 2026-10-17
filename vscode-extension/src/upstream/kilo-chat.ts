/** Selected Kilo Code chat components, adapted to a DOM webview.
 * MIT; Copyright (c) 2026 Kilo Code, (c) 2025 opencode.
 * Source revision, files and full license: ../../THIRD_PARTY_NOTICES.md.
 */
export const chatStyles = /* css */ `
  html, body { height: 100%; margin: 0; padding: 0; }
  body { display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
  #thread { flex: 1; min-height: 0; overflow-x: hidden; overflow-y: auto;
    padding: 12px 16px; scrollbar-gutter: stable; overflow-anchor: none; }
  #composer { flex-shrink: 0; margin: 6px; border: 1px solid var(--vscode-input-border, var(--vscode-panel-border));
    border-radius: 8px; background: var(--vscode-input-background); }
  #composer:focus-within { border-color: var(--vscode-focusBorder); }
  #message { box-sizing: border-box; width: 100%; min-height: 54px; max-height: 200px;
    padding: 8px; font: inherit; line-height: 1.4; resize: none; outline: none;
    scrollbar-gutter: stable; color: var(--vscode-input-foreground);
    caret-color: var(--vscode-input-foreground); background: transparent; border: 0; }
  #message::placeholder { color: var(--vscode-input-placeholderForeground); }
  #composer .actions { display: flex; align-items: center; gap: 8px; padding: 6px; margin: 0; }
  #send-message { margin-left: auto; flex-shrink: 0; }
  #send-hint { display: block; padding: 6px 8px 0; font-size: .75rem; color: var(--vscode-descriptionForeground); }
  #chat-error { padding: 0 8px; font-size: .8rem; color: var(--vscode-errorForeground); }
  .message { overflow-wrap: anywhere; margin: 0 0 20px; line-height: 1.5; }
  .message.user { white-space: pre-wrap; padding: 10px 12px; border-radius: 8px;
    background: var(--vscode-textCodeBlock-background); }
  .speaker { font-size: .75rem; font-weight: 600; color: var(--vscode-descriptionForeground); margin-bottom: 6px; }
  .message p { margin: 0 0 10px; }
  .message pre { white-space: pre; word-break: normal; overflow: auto; padding: 10px; }
  .message code { font-family: var(--vscode-editor-font-family); background: var(--vscode-textCodeBlock-background); }
  .message a { color: var(--vscode-textLink-foreground); }
  .message ul, .message ol { padding-left: 22px; }
  .message table { border-collapse: collapse; display: block; overflow: auto; }
  .message th, .message td { padding: 4px 8px; border: 1px solid var(--vscode-panel-border); }
  #app { border-left: 2px solid var(--vscode-textLink-foreground); padding: 0 0 0 10px; margin-bottom: 14px; }
  #app:empty { display: none; }
  #latest { align-self: center; margin-top: -34px; margin-bottom: 6px; z-index: 1;
    border-radius: 16px; font-size: .75rem; box-shadow: 0 2px 8px var(--vscode-widget-shadow); }
  #latest[hidden] { display: none; }
  .chat-toolbar { display: flex; justify-content: flex-end; padding: 4px 8px; gap: 8px; }
  .chat-toolbar button { font-size: .8rem; }
  .keyboard-hint { font-size: .7rem; color: var(--vscode-descriptionForeground); }
`;

/** Self-contained so its compiled source can be embedded in the webview. */
export function installChatBehavior() {
  const field = document.getElementById("message") as HTMLTextAreaElement;
  const thread = document.getElementById("thread")!;
  const content = document.getElementById("thread-content")!;
  const latest = document.getElementById("latest") as HTMLButtonElement;
  let userScrolled = false;
  let top = 0;
  let dragging = false;
  let lastInteraction = -Infinity;
  const distance = () => thread.scrollHeight - thread.clientHeight - thread.scrollTop;
  const canScroll = () => thread.scrollHeight - thread.clientHeight > 1;
  const recent = () => performance.now() - lastInteraction < 300;
  const indicate = () => { latest.hidden = !userScrolled || !canScroll(); };
  const bottom = () => { thread.scrollTop = thread.scrollHeight; top = thread.scrollTop; indicate(); };
  const pause = () => { if (canScroll()) userScrolled = true; indicate(); };
  const resume = () => { userScrolled = false; lastInteraction = -Infinity; bottom(); };
  thread.addEventListener("wheel", (event) => {
    lastInteraction = performance.now();
    if (event.deltaY < 0) pause();
  }, { capture: true, passive: true });
  thread.addEventListener("pointerdown", () => { dragging = true; lastInteraction = performance.now(); });
  window.addEventListener("pointerup", () => {
    if (!dragging) return;
    dragging = false; lastInteraction = performance.now();
    if (window.getSelection()?.toString()) pause();
  });
  thread.addEventListener("keydown", (event) => {
    if (["ArrowUp", "PageUp", "Home"].includes(event.key)) {
      lastInteraction = performance.now(); pause();
    }
  });
  thread.addEventListener("scroll", () => {
    const down = thread.scrollTop > top;
    top = thread.scrollTop;
    if (distance() < 2 && down) userScrolled = false;
    else if (recent() && distance() > 10) pause();
    indicate();
  });
  const follow = () => {
    if (dragging || userScrolled) return;
    if (recent() && distance() > 10) { pause(); return; }
    bottom();
  };
  new MutationObserver(follow).observe(content, { childList: true, subtree: true, characterData: true });
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(follow).observe(content);
    new ResizeObserver(() => { if (!dragging && !userScrolled) bottom(); }).observe(thread);
  }
  latest.addEventListener("click", resume);
  const resize = () => {
    field.style.height = "auto";
    if (field.value) field.style.height = `${Math.min(field.scrollHeight, 200)}px`;
  };
  field.addEventListener("input", resize);
  // VS Code intercepts these keys in the webview preload. Keep native prompt
  // undo local so it cannot undo an unrelated source editor (Kilo #13724).
  field.addEventListener("keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const z = event.keyCode === 90 || event.key.toLowerCase() === "z";
    const y = event.keyCode === 89 || event.key.toLowerCase() === "y";
    const action = z ? (event.shiftKey ? "redo" : "undo") : y && !event.shiftKey ? "redo" : null;
    if (!action) return;
    event.stopPropagation(); event.preventDefault();
    document.execCommand(action);
    resize();
  });
  return { resume, resize };
}
