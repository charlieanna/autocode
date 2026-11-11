// Forward bounded native progress that `opencode run --format json` omits.
// Hashes describe liveness only: never emit report text, tool proof or completion.
// A reasoning part also carries a short preview of its newest words, for the live log to show
// what the model is thinking (display only); a text part, which may be the report, never does.
// When a reasoning part ends, one last row marks it final and previews its opening words instead,
// so a finished thought reads from its start rather than as a tail cut mid-sentence.
// A compaction part (the provider summarized the conversation) is forwarded as its own
// row, so the runner can refuse the response that follows it (autocode_context_window).
import { createHash } from "node:crypto";

const WINDOW = 8192;
const PREVIEW = 600; // characters of a reasoning part's newest text; the runner bounds and redacts again
const MAX_PARTS = 4096;
const MAX_EVENTS = 65536;
const digest = (text) => createHash("sha256").update(text.trim()).digest("hex");

export default async () => {
  let sessionID;
  const parts = new Map();
  const seen = new Set();
  const compactions = new Set();
  function flush(id, part, ended = false) {
    const final = ended && part.kind === "reasoning" && !part.final && part.head.trim() !== "";
    if (!part.pending.trim() && !final) return;
    const progress = {id, kind: part.kind, position: part.position, nonwhite: true,
      content_hash: digest(part.window), delta_hash: digest(part.pending)};
    if (part.kind === "reasoning") progress.preview = final ? part.head : part.window.slice(-PREVIEW);
    if (final) {
      progress.final = true;
      if (part.position > part.head.length) progress.cut = true; // the preview is not the whole thought
      part.final = true;
    }
    process.stdout.write(JSON.stringify({
      type: "autocode_progress", version: 1, timestamp: Date.now(), sessionID, progress
    }) + "\n");
    part.pending = "";
    part.emitted = performance.now();
  }
  return {
    "chat.message": async (input) => {
      if (sessionID !== input.sessionID) {
        sessionID = input.sessionID;
        parts.clear();
        seen.clear();
        compactions.clear();
      }
    },
    event: async ({event}) => {
      const data = event.properties || {};
      if (event.type === "message.part.updated") {
        const part = data.part;
        if (!part || part.sessionID !== sessionID) return;
        if (part.type === "compaction") {
          if (typeof part.id !== "string" || compactions.has(part.id) || compactions.size >= MAX_PARTS) return;
          compactions.add(part.id);
          process.stdout.write(JSON.stringify({
            type: "autocode_compaction", version: 1, timestamp: Date.now(), sessionID,
            compaction: {auto: part.auto === true, overflow: part.overflow === true}
          }) + "\n");
          return;
        }
        if (!["text", "reasoning"].includes(part.type)) return;
        if (!parts.has(part.id) && parts.size < MAX_PARTS) {
          parts.set(part.id, {kind: part.type, position: 0, window: "", head: "", pending: "", emitted: -Infinity});
        }
        if (part.time?.end && parts.has(part.id)) flush(part.id, parts.get(part.id), true);
        return;
      }
      if (event.type !== "message.part.delta" || !sessionID || data.sessionID !== sessionID ||
          data.field !== "text" || typeof data.delta !== "string" || !data.delta ||
          typeof event.id !== "string" || seen.has(event.id) || seen.size >= MAX_EVENTS) return;
      const part = parts.get(data.partID);
      if (!part) return;
      seen.add(event.id);
      part.position += data.delta.length;
      part.window = (part.window + data.delta).slice(-WINDOW);
      if (part.head.length < PREVIEW) part.head = (part.head + data.delta).slice(0, PREVIEW);
      part.pending = (part.pending + data.delta).slice(-WINDOW);
      // Emit only on actual traffic, at most four snapshots/second per part.
      // Completion flushes any final remainder; no timer generates heartbeats.
      if (performance.now() - part.emitted >= 250) flush(data.partID, part);
    }
  };
};
