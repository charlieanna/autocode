// Display-only Requirements text. These rows never carry report or tool proof.
export async function requirementsStream() {
  let sessionID;
  const parts = new Map();
  const emitted = new Map();
  const assistants = new Set();
  function publish(id, text) {
    const match = /"assistant_reply"\s*:\s*"/.exec(text);
    if (!match) return;
    let fragment = text.slice(match.index + match[0].length);
    let escaped = false;
    for (let i = 0; i < fragment.length; i++) {
      if (fragment[i] === '"' && !escaped) { fragment = fragment.slice(0, i); break; }
      escaped = fragment[i] === '\\' && !escaped;
    }
    for (let trim = 0; trim <= Math.min(6, fragment.length); trim++) {
      try {
        const reply = JSON.parse('"' + (trim ? fragment.slice(0, -trim) : fragment) + '"').slice(0, 12000);
        if (reply && reply !== emitted.get(id)) {
          emitted.set(id, reply);
          process.stdout.write(JSON.stringify({type: 'autocode_chat_reply', sessionID, reply}) + '\n');
        }
        break;
      } catch { /* A delta may end inside an escape. */ }
    }
  }
  return {
    'chat.message': async (input) => { sessionID = input.sessionID; parts.clear(); emitted.clear(); assistants.clear(); },
    event: async ({event}) => {
      const data = event.properties || {};
      if (event.type === 'message.updated') {
        const info = data.info;
        if (info?.sessionID === sessionID && info.role === 'assistant' && assistants.size < 32) assistants.add(info.id);
      } else if (event.type === 'message.part.updated') {
        const part = data.part;
        if (!part || part.sessionID !== sessionID || part.type !== 'text' || !assistants.has(part.messageID)) return;
        if (!parts.has(part.id) && parts.size >= 32) return;
        const text = typeof part.text === 'string' ? part.text.slice(0, 262144) : '';
        parts.set(part.id, text); publish(part.id, text);
      } else if (event.type === 'message.part.delta' && data.sessionID === sessionID
          && data.field === 'text' && typeof data.delta === 'string' && parts.has(data.partID)) {
        const text = (parts.get(data.partID) + data.delta).slice(0, 262144);
        parts.set(data.partID, text); publish(data.partID, text);
      }
    },
  };
}
export default {id: 'autocode-requirements-stream', server: requirementsStream};
