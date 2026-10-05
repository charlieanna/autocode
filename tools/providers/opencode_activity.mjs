// Forward bounded native progress that `opencode run --format json` omits.
// Hashes describe liveness only: never emit report text, tool proof or completion.
import { createHash } from "node:crypto";

const WINDOW = 8192;
const MAX_PARTS = 4096;
const MAX_EVENTS = 65536;
const digest = (text) => createHash("sha256").update(text.trim()).digest("hex");

export default async () => {
  let sessionID;
  const parts = new Map();
  const seen = new Set();
  function flush(id, part) {
    if (!part.pending.trim()) return;
    process.stdout.write(JSON.stringify({
      type: "autocode_progress", version: 1, timestamp: Date.now(), sessionID,
      progress: {id, kind: part.kind, position: part.position, nonwhite: true,
        content_hash: digest(part.window), delta_hash: digest(part.pending)}
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
      }
    },
    event: async ({event}) => {
      const data = event.properties || {};
      if (event.type === "message.part.updated") {
        const part = data.part;
        if (!part || part.sessionID !== sessionID || !["text", "reasoning"].includes(part.type)) return;
        if (!parts.has(part.id) && parts.size < MAX_PARTS) {
          parts.set(part.id, {kind: part.type, position: 0, window: "", pending: "", emitted: -Infinity});
        }
        if (part.time?.end && parts.has(part.id)) flush(part.id, parts.get(part.id));
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
      part.pending = (part.pending + data.delta).slice(-WINDOW);
      // Emit only on actual traffic, at most four snapshots/second per part.
      // Completion flushes any final remainder; no timer generates heartbeats.
      if (performance.now() - part.emitted >= 250) flush(data.partID, part);
    }
  };
};
