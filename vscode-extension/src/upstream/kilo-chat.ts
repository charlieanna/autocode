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
  #telemetry { flex-shrink: 0; margin: 0 6px; border: 1px solid var(--vscode-panel-border); border-radius: 6px;
    font-size: .75rem; color: var(--vscode-descriptionForeground); background: var(--vscode-sideBar-background); }
  #telemetry-summary { cursor: pointer; padding: 5px 8px; line-height: 1.4; overflow-wrap: anywhere; font-size: .75rem; }
  .telemetry-heading { color: var(--vscode-foreground); font-weight: 600; }
  #model-summary, #usage-summary { display: inline; }
  #model-summary::before, #usage-summary::before { content: "  ·  "; color: var(--vscode-descriptionForeground); }
  #model-summary { color: var(--vscode-foreground); }
  #telemetry-body { max-height: 30vh; overflow: auto; padding: 0 8px 8px; border-top: 1px solid var(--vscode-panel-border); }
  #model-info { padding-top: 6px; }
  .model-caption { font-weight: 600; color: var(--vscode-foreground); margin-bottom: 4px; }
  .model-note { font-size: .7rem; margin-bottom: 6px; }
  .model-section { margin-bottom: 12px; }
  .model-card { padding: 6px 0; border-bottom: 1px solid var(--vscode-panel-border); }
  .model-heading { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px 12px; }
  .model-job { font-weight: 600; color: var(--vscode-foreground); }
  .model-meta { font-size: .7rem; margin-top: 2px; }
  .model-value { color: var(--vscode-foreground); overflow-wrap: anywhere; }
  #usage-breakdown { padding-top: 6px; }
  .telemetry-row { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px 12px; padding: 3px 0; }
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
  #app { display: contents; }
  #app.muted { display: block; border-left: 2px solid var(--vscode-textLink-foreground); padding: 0 0 0 10px; margin-bottom: 14px; }
  #app:empty { display: none; }
  .decision-card {
    background: var(--vscode-editorWidget-background, var(--vscode-sideBar-background));
    border: 1px solid var(--vscode-widget-border, var(--vscode-panel-border));
    border-radius: 8px; padding: 10px 12px; margin: 0 0 12px; line-height: 1.5; overflow-wrap: anywhere;
  }
  .decision-card .speaker { font-size: .75rem; font-weight: 600; color: var(--vscode-descriptionForeground); margin-bottom: 6px; }
  .decision-card h2 { font-size: .85rem; font-weight: 600; text-transform: none; letter-spacing: normal;
    opacity: 1; margin: 12px 0 6px; color: var(--vscode-foreground); }
  .decision-card h2:first-child { margin-top: 0; }
  .decision-card .row { padding: 4px 0; border-bottom: 0; font-size: .85rem; }
  .decision-card .row + .row { border-top: 1px solid var(--vscode-panel-border, rgba(128,128,128,.1)); }
  .decision-card .why { font-size: .75rem; color: var(--vscode-descriptionForeground); opacity: 1; margin-top: 2px; }
  .decision-card pre.understanding { white-space: pre-wrap; word-break: normal; font-family: inherit;
    font-size: .85rem; line-height: 1.45; background: var(--vscode-textCodeBlock-background);
    padding: 8px 10px; border-radius: 6px; margin: 6px 0; }
  .decision-card .decision { margin: 10px 0; }
  .decision-card .decision .q { font-weight: 600; font-size: .85rem; }
  .decision-card select, .decision-card input[type="text"] { box-sizing: border-box; width: 100%; margin-top: 4px;
    padding: 6px 8px; font: inherit; font-size: .85rem; background: var(--vscode-input-background);
    color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border, var(--vscode-panel-border));
    border-radius: 4px; }
  .decision-card select:focus, .decision-card input[type="text"]:focus { border-color: var(--vscode-focusBorder); outline: none; }
  .decision-card .notice { border-left: 3px solid var(--vscode-textLink-foreground, #3794ff);
    background: var(--vscode-textCodeBlock-background, rgba(128,128,128,.1)); padding: 8px 10px;
    margin: 10px 0; font-size: .84rem; border-radius: 0 4px 4px 0; }
  .decision-card .gap { border-left: 3px solid var(--vscode-inputValidation-warningBorder, #cc8800);
    background: var(--vscode-inputValidation-warningBackground, rgba(204,136,0,.12)); padding: 8px 10px;
    margin: 10px 0; font-size: .84rem; border-radius: 0 4px 4px 0; }
  .action-strip {
    position: sticky; bottom: -12px; z-index: 2; display: flex; align-items: center; flex-wrap: wrap;
    gap: 8px; padding: 8px 16px; margin: 8px -16px -12px; background: var(--vscode-sideBar-background);
    border-top: 1px solid var(--vscode-panel-border); box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.08);
  }
  .action-strip .action-prompt { font-size: .75rem; font-weight: 600; color: var(--vscode-descriptionForeground); margin-right: auto; }
  .action-strip button { display: inline-flex; align-items: center; justify-content: center;
    padding: 5px 12px; font-size: .8rem; font-weight: 500; border-radius: 4px;
    border: 1px solid var(--vscode-button-border, transparent); background: var(--vscode-button-background);
    color: var(--vscode-button-foreground); cursor: pointer; }
  .action-strip button:hover { background: var(--vscode-button-hoverBackground); }
  .action-strip button:focus-visible { outline: 1px solid var(--vscode-focusBorder); }
  .action-strip button.secondary { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
  .action-strip button.secondary:hover { background: var(--vscode-button-secondaryHoverBackground); }
  .action-strip button:disabled { opacity: 0.5; cursor: not-allowed; }
  #latest { align-self: center; margin-top: -34px; margin-bottom: 6px; z-index: 3;
    border-radius: 16px; font-size: .75rem; box-shadow: 0 2px 8px var(--vscode-widget-shadow); }
  #latest[hidden] { display: none; }
  .chat-toolbar { display: flex; flex-wrap: wrap; flex-shrink: 0; justify-content: flex-end; padding: 2px 8px; gap: 2px; }
  .chat-toolbar button.icon, #composer .chip { display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; padding: 0; border: 0; border-radius: 5px; background: transparent;
    color: var(--vscode-foreground); cursor: pointer; }
  .chat-toolbar button.icon:hover, #composer .chip:hover { background: var(--vscode-toolbar-hover); }
  .chat-toolbar button.icon:focus-visible, #composer .chip:focus-visible { outline: 1px solid var(--vscode-focusBorder); }
  .chat-toolbar svg { width: 16px; height: 16px; fill: none; stroke: currentColor;
    stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
  #composer .chip { width: auto; gap: 4px; padding: 0 8px; font-size: .7rem; color: var(--vscode-descriptionForeground); }
  #composer .chip svg { width: 13px; height: 13px; fill: none; stroke: currentColor;
    stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
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
