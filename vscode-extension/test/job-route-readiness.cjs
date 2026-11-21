// A field left in an old chat is not enough: wait for this run's visible decision.
function routeDecisionReady({ visible, text }) {
  return visible === true && typeof text === 'string'
    && /Review the model and reasoning level for the Design Reviewer, then retry it/i.test(text)
    && /The Design Reviewer's saved model settings changed\. Review the model and reasoning level before retrying\./i.test(text);
}

module.exports = { routeDecisionReady };
