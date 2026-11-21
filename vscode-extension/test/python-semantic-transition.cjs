// The click can race the first status poll; only the exact reviewed plan is a benign old view.
const { isDeepStrictEqual } = require('node:util');

function sameApproval(view, before) {
  return Boolean(view && typeof view === 'object' && !view.done && view.status === before.status && view.stop_reason === before.stop_reason &&
      view.needs?.kind === 'approve_plan' && view.needs.token === before.needs?.token &&
      isDeepStrictEqual(view.displayed_plan, before.displayed_plan));
}

function settledAfterApproval(view, before, transitioned) {
  if (!view || typeof view !== 'object') return false;
  if (!transitioned && sameApproval(view, before)) return false;
  return Boolean(view.done || view.status === before.status || String(view.status).startsWith('PAUSED_') || view.status === 'WAITING_FOR_USER' ||
    (view.needs && view.needs.kind !== 'continue') || (!view.next_command && view.stop_reason));
}

module.exports = { sameApproval, settledAfterApproval };
