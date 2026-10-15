'use strict';

// Headless Chrome (seen on 155 through agent-browser 0.25.3) marks the page
// hidden when it receives an Escape keyDown the page does not
// preventDefault: document.visibilityState becomes "hidden" and stays hidden
// across later navigations in the session. A headed browser stays visible.
// The dashboard's drawer Escape handler closes the drawer without
// preventDefault, which is correct for a real browser, so every later step
// ran on a hidden page: Chrome throttled its timers to one wake-up a second,
// then one a minute, and presses and evals took seconds to minutes until one
// crossed the 45-second bridge timeout (#1003). Re-selecting the tab
// (Page.bringToFront) makes it visible again. The Escape itself still reaches
// the page unchanged, and the assertion keeps a page that stays hidden from
// degrading silently into slow timeouts.
function pressEscape(browser, evaluate) {
  browser('press', 'Escape');
  browser('tab', '0');
  const visibility = evaluate('JSON.stringify(document.visibilityState)');
  if (visibility !== 'visible') {
    throw new Error('the page stayed ' + visibility + ' after Escape; later keyboard steps would run on a throttled page');
  }
}

module.exports = {pressEscape};
