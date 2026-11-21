// The click can race the first status poll; only the exact reviewed plan is a benign old view.
const { isDeepStrictEqual } = require('node:util');

function sameApproval(view, before) {
  return Boolean(view && typeof view === 'object' && !view.done && view.status === before.status && view.stop_reason === before.stop_reason &&
      view.needs?.kind === 'approve_plan' && view.needs.token === before.needs?.token &&
      isDeepStrictEqual(view.displayed_plan, before.displayed_plan));
}

// A live controller suppresses actions until it has saved its next state.
function observationContinuing(view) {
  return Boolean(view && typeof view === 'object' && !view.done && view.progress?.continuing === true &&
    !view.needs && !view.next_command);
}

function settledAfterApproval(view, before, transitioned) {
  if (!view || typeof view !== 'object') return false;
  if (observationContinuing(view) || (!transitioned && sameApproval(view, before))) return false;
  return Boolean(view.done || view.status === before.status || String(view.status).startsWith('PAUSED_') || view.status === 'WAITING_FOR_USER' ||
    (view.needs && view.needs.kind !== 'continue') || (!view.next_command && view.stop_reason));
}

function afterApproval(before) {
  let transitioned = false;
  return (view) => {
    if (view && !sameApproval(view, before)) transitioned = true;
    return settledAfterApproval(view, before, transitioned);
  };
}

module.exports = { sameApproval, observationContinuing, settledAfterApproval, afterApproval };
