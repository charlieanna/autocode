// Browser-level M1/M2 regression: shell geometry, operational task flows, focus
// contrast, and the complete deterministic Figma scenario matrix. The Python
// fixture is disposable only.
const assert = require('node:assert/strict');
const {spawn, execFileSync} = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {dashboardReadinessExpression, waitForReadiness} = require('./browser_readiness');

const root = path.resolve(__dirname, '../../..');
const fixture = path.join(__dirname, 'unified_browser_fixture.py');
const evidenceRoot = process.env.DASHBOARD_MATRIX_EVIDENCE_DIR
  ? path.resolve(root, process.env.DASHBOARD_MATRIX_EVIDENCE_DIR)
  : path.join(root, '.autocode', 'evidence', 'm2-scenario-matrix', 'run-' + process.pid);
const shellEvidenceRoot = path.join(evidenceRoot, 'shell');
const matrixEvidenceRoot = path.join(evidenceRoot, 'matrix');
const fixtureRoot = path.join(evidenceRoot, 'fixture-state');
fs.mkdirSync(shellEvidenceRoot, {recursive: true});
fs.mkdirSync(matrixEvidenceRoot, {recursive: true});
fs.mkdirSync(fixtureRoot, {recursive: true});
let session = 'dashboard-shell-a11y-' + process.pid;
let server;
let scenarioReload = 0;
let sessionHasPage = false;
const browserSessionAudits = [];
const dashboardCss = fs.readFileSync(path.join(root, 'tools/dashboard/dashboard.css'), 'utf8');

assert.match(dashboardCss, /@media \(forced-colors:active\)\{[^}]*:focus-visible/, 'forced-colors mode keeps an explicit focus outline');
assert.match(dashboardCss, /@media \(prefers-reduced-motion:reduce\)\{/, 'reduced-motion mode remains explicitly supported');

const scenarios = ['workspace', 'running', 'waiting', 'paused', 'completed', 'plan', 'pending-answer'];
const viewports = [
  {name: 'desktop', width: 1440, height: 1024, nodes: {
    workspace: '13:106', running: '13:107', waiting: '13:108', paused: '13:109',
    completed: '13:110', plan: '13:111', 'pending-answer': '13:112',
  }},
  {name: 'tablet', width: 1024, height: 768, nodes: {
    workspace: '13:113', running: '13:114', waiting: '13:115', paused: '13:116',
    completed: '13:117', plan: '13:118', 'pending-answer': '13:119',
  }},
  {name: 'mobile', width: 390, height: 844, nodes: {
    workspace: '13:120', running: '13:121', waiting: '13:122', paused: '13:123',
    completed: '13:124', plan: '13:125', 'pending-answer': '13:126',
  }},
];

function browser(...args) {
  // The local bridge itself occasionally reports a transient EAGAIN while an
  // immediately preceding screenshot settles. Retry only that documented
  // bridge condition; dashboard errors and all other browser failures still
  // fail this test unchanged.
  let lastError;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return execFileSync('agent-browser', ['--session', session, ...args], {
        cwd: root,
        encoding: 'utf8',
        timeout: 45000,
        env: {...process.env, AGENT_BROWSER_SCREENSHOT_DIR: evidenceRoot},
      });
    } catch (error) {
      lastError = error;
      const output = String(error.stdout || '') + String(error.stderr || '') + String(error.message || '');
      if (!/Resource temporarily unavailable.*daemon may be busy|EAGAIN/i.test(output) || attempt === 2) throw error;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 450 * (attempt + 1));
    }
  }
  throw lastError;
}

function auditBrowserSession(label) {
  if (!sessionHasPage) return;
  const pageErrors = browser('errors').trim();
  assert.match(pageErrors, /^(?:|\[\]|No page errors\.?|No errors\.?)$/i, label + ' browser page errors: ' + pageErrors);
  const consoleMessages = browser('console').trim();
  assert.ok(!/\b(?:warn(?:ing)?|error)\b/i.test(consoleMessages), label + ' browser console warnings or errors: ' + consoleMessages);
  browserSessionAudits.push({
    label,
    session,
    page_errors: pageErrors || 'No page errors.',
    console: consoleMessages || 'No browser console messages.',
  });
  sessionHasPage = false;
}

function freshBrowserSession(label) {
  // The local bridge can retain a screenshot/read operation after a very long
  // 21-screen capture. Each lifecycle fixture is already an independent saved
  // state, so give it a fresh browser session rather than allowing bridge
  // contention to masquerade as a dashboard-routing failure.
  auditBrowserSession('before-' + label);
  try { browser('close', '--all'); } catch {}
  session = 'dashboard-shell-a11y-' + process.pid + '-' + label;
}

function sourceSnapshot() {
  const files = [
    'tools/dashboard/dashboard.html',
    'tools/dashboard/dashboard.css',
    'tools/dashboard/dashboard_app.js',
    'tools/dashboard/agent_console.py',
    'tools/dashboard/tests/test_console.py',
    'tools/dashboard/tests/browser_readiness.js',
    'tools/dashboard/tests/test_browser_readiness.js',
    'tools/dashboard/tests/test_shell_a11y_ui.js',
    'tools/dashboard/tests/test_workspace_ui.js',
    'tools/dashboard/tests/unified_browser_fixture.py',
  ];
  return files.map(file => ({
    file,
    sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex'),
  }));
}

function evaluate(script) {
  return JSON.parse(JSON.parse(browser('eval', script).trim()));
}

function data(body) {
  return evaluate('JSON.stringify((' + body + ')())');
}

function dashboardReadiness() {
  return data(dashboardReadinessExpression());
}

function browserFailureDiagnostics() {
  try {
    return {
      page_errors: browser('errors').trim() || 'No page errors.',
      console: browser('console').trim() || 'No browser console messages.',
    };
  } catch (error) {
    return {diagnostic_error: String(error?.stack || error)};
  }
}

function waitForDashboardReadiness(label, attempts = 48) {
  return waitForReadiness({
    label,
    attempts,
    probe: dashboardReadiness,
    wait: () => browser('wait', '250'),
    onTimeout: browserFailureDiagnostics,
  });
}

function waitForCondition(expression, label, attempts = 48) {
  // agent-browser's long-running --fn command can itself become contended
  // after a screenshot. Poll the same rendered browser state with a bounded
  // interval instead; the exact expected condition remains mandatory. The
  // fixture's authoritative action round trip includes a read-after-write,
  // so retain a bounded 12-second allowance rather than treating a slow local
  // browser bridge as a completed lifecycle transition.
  let last;
  for (let attempt = 0; attempt < attempts; attempt++) {
    // Do not evaluate a scenario predicate until the dashboard script has
    // declared its lexical globals. The guard keeps diagnostics useful when a
    // fixture never initializes instead of throwing ReferenceError first.
    last = data('()=>{' +
      'const applicationGlobalDeclared=typeof latestRun!=="undefined";' +
      'const run=applicationGlobalDeclared?latestRun:null;' +
      'return {matched:applicationGlobalDeclared&&Boolean(' + expression + '),document_ready_state:document.readyState,application_global_declared:applicationGlobalDeclared,application_root_present:!!document.querySelector(".app-shell"),status:run?.status||null,stage:run?.active_stage?.stage||null,notice:document.querySelector("#dashboard-notice")?.textContent||""};' +
    '}');
    if (last.matched) return;
    if (attempt < attempts - 1) browser('wait', '250');
  }
  assert.fail(label + ' did not become true within ' + attempts * 250 + ' ms: ' + expression + '; final rendered state=' + JSON.stringify(last));
}

function fixtureInfo() {
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(Error('Timed out waiting for the disposable dashboard fixture.')), 15000);
    let output = '', errors = '';
    server.stdout.on('data', chunk => {
      output += String(chunk);
      const match = output.match(/^FIXTURE=(.+)$/m);
      if (!match) return;
      clearTimeout(deadline);
      try { resolve(JSON.parse(match[1])); } catch (error) { reject(error); }
    });
    server.stderr.on('data', chunk => { errors += String(chunk); });
    server.once('error', reject);
    server.once('exit', code => {
      if (code) reject(Error('Disposable dashboard fixture exited with ' + code + ': ' + (errors || output)));
    });
  });
}

function shellMetrics() {
  return data('()=>{' +
    'const box=s=>{const e=document.querySelector(s);if(!e)return null;const r=e.getBoundingClientRect();return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]};' +
    'const elementBox=e=>{const r=e.getBoundingClientRect();return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)]};' +
    'const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"&&!e.hidden&&r.width>0&&r.height>0};' +
    'const undersized=[...document.querySelectorAll("button,input,select,textarea,summary,a.brand,[role=button],[role=tab]")].filter(visible).map(e=>({name:e.id||e.getAttribute("aria-label")||e.textContent.trim(),box:elementBox(e)})).filter(x=>x.box[2]<44||x.box[3]<44);' +
    'return {topbar:box(".topbar"),shellBody:box(".shell-body"),sidebar:box(".sidebar"),main:box(".main-area"),drawerClose:box("#drawer-close"),continue:box("#continue-run"),pause:box("#pause-run"),undersized,open:document.querySelector(".app-shell").classList.contains("nav-open"),inert:document.querySelector(".main-area").inert,topbarInert:document.querySelector(".topbar").inert,horizontalOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth,active:document.activeElement.id};' +
  '}');
}

function channel(value) {
  const match = String(value).match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (match) return match.slice(1).map(Number);
  const hex = String(value).trim().replace('#', '');
  if (/^[\da-f]{3}$/i.test(hex)) return [...hex].map(part => Number.parseInt(part + part, 16));
  if (/^[\da-f]{6}$/i.test(hex)) return hex.match(/[\da-f]{2}/gi).map(part => Number.parseInt(part, 16));
  assert.fail('expected a resolved RGB or hexadecimal color, received ' + value);
}

function luminance(rgb) {
  const normalized = rgb.map(value => value / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return .2126 * normalized[0] + .7152 * normalized[1] + .0722 * normalized[2];
}

function contrast(foreground, background) {
  const pair = [luminance(channel(foreground)), luminance(channel(background))].sort((left, right) => right - left);
  return (pair[0] + .05) / (pair[1] + .05);
}

function meaningfulBoundaryRatios() {
  return data('()=>{' +
    'const surface=getComputedStyle(document.documentElement).getPropertyValue("--surface-default").trim();' +
    'const target=s=>{const e=document.querySelector(s);return {selector:s,border:getComputedStyle(e).borderTopColor,surface};};' +
    'return ["#continue-run","#project-filter","#task-search",".summary-filter",".task-row",".composer","#project-removal-dialog"].map(target);' +
  '}');
}

function setTheme(theme) {
  data('()=>{document.documentElement.dataset.theme=' + JSON.stringify(theme) + ';return true;}');
}

function scenarioMetrics() {
  return data('()=>{' +
    'const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return !e.hidden&&s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};' +
    'const box=e=>{const r=e.getBoundingClientRect();return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)};};' +
    'const clipped=e=>!!e&&(e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1);const title=document.querySelector("#task-title"),status=document.querySelector("#task-status .badge");' +
    'const activePage=[...document.querySelectorAll(".page")].find(visible),topbar=document.querySelector(".topbar"),topLayer=document.elementFromPoint(8,8);' +
    'const targets=[...document.querySelectorAll("button,input,select,textarea,summary,a.brand,[role=button],[role=tab]")].filter(visible).map(e=>Object.assign({name:e.id||e.getAttribute("aria-label")||e.textContent.trim()},box(e)));' +
    'const contained=[...document.querySelectorAll(".main-area *")].filter(visible).map(e=>{const own=e.getBoundingClientRect(),parent=e.parentElement&&e.parentElement.getBoundingClientRect();return parent?{name:e.id||e.className||e.tagName,width:Math.round(own.width),parentWidth:Math.round(parent.width),overflow:Math.round(Math.max(0,own.right-parent.right,parent.left-own.left))}:null;}).filter(Boolean).sort((a,b)=>b.width-a.width);' +
    'const tabs=[...document.querySelectorAll(".detail-tabs [role=tab]")].filter(visible).map(t=>({label:t.textContent.trim(),selected:t.getAttribute("aria-selected")}));' +
    'const primary=[...new Set([...document.querySelectorAll("#task-detail button.primary,#task-detail [data-primary-action=true]")])].filter(e=>visible(e));' +
    'const styles=getComputedStyle(document.documentElement);const visualTokens={};["--surface-canvas","--surface-default","--text-primary","--text-secondary","--border-control","--accent-primary","--focus-control-layer","--focus-surface-layer"].forEach(k=>visualTokens[k]=styles.getPropertyValue(k).trim());' +
    'return {viewport:{width:innerWidth,height:innerHeight},document:{scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,horizontalOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth},shell:{topbar:box(topbar),main:box(document.querySelector(".main-area")),activePage:activePage?box(activePage):null,activePageScrollTop:activePage?Math.round(activePage.scrollTop):null,topbarOwnsViewportOrigin:!!topbar&&topbar.contains(topLayer)},header:{title:title?box(title):null,status:status?box(status):null,titleClipped:clipped(title),statusClipped:clipped(status)},widestChild:contained[0]||null,overflowingChildren:contained.filter(x=>x.overflow>0).slice(0,12),targets:{minimum:targets.length?Math.min(...targets.map(x=>Math.min(x.width,x.height))):0,undersized:targets.filter(x=>x.width<44||x.height<44),visible:targets.length},tabs,primaryActionCount:primary.length,primaryActions:primary.map(e=>Object.assign({name:e.id,label:e.textContent.trim()},box(e))),visualTokens};' +
  '}');
}

function taskFoldMetrics() {
  return data('()=>{' +
    // Container bounds plus textContent were a false positive: a closed
    // disclosure can have visible dimensions while its complete value is not
    // actually rendered. Inspect the dedicated visible value node instead.
    'const required=["state","step","objective","blocker","role","freshness"],clean=value=>String(value||"").replace(/\\s+/g," ").trim();' +
    'const box=e=>{const r=e.getBoundingClientRect();return {left:Math.round(r.left),top:Math.round(r.top),right:Math.round(r.right),bottom:Math.round(r.bottom),width:Math.round(r.width),height:Math.round(r.height)}};' +
    'const displayed=e=>{if(!e||e.hidden)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};' +
    'const clippedBy=(target,clip)=>target.left<clip.left-1||target.right>clip.right+1||target.top<clip.top-1||target.bottom>clip.bottom+1;' +
    'const inspect=(key)=>{const element=document.querySelector("[data-task-fact=\\\""+key+"\\\"]"),value=document.querySelector("[data-task-fact-visible=\\\""+key+"\\\"]"),label=document.querySelector("[data-task-fact-label=\\\""+key+"\\\"]");if(!element||!value)return {key,present:false};const rect=box(value),clippers=[],viewport={left:0,top:0,right:innerWidth,bottom:innerHeight};let fully=displayed(value)&&!clippedBy(rect,viewport);for(let parent=value.parentElement;parent;parent=parent.parentElement){const style=getComputedStyle(parent),clipX=/hidden|clip|scroll|auto/.test(style.overflowX),clipY=/hidden|clip|scroll|auto/.test(style.overflowY);if(!clipX&&!clipY)continue;const parentBox=box(parent),blocked=(clipX&&(rect.left<parentBox.left-1||rect.right>parentBox.right+1))||(clipY&&(rect.top<parentBox.top-1||rect.bottom>parentBox.bottom+1));clippers.push({name:parent.id||parent.className||parent.tagName,overflowX:style.overflowX,overflowY:style.overflowY,box:parentBox,blocked});if(blocked)fully=false;}return {key,present:true,visible:displayed(value),fullyVisible:fully,box:rect,visibleText:clean(value.innerText),textOverflow:value.scrollWidth>value.clientWidth+1||value.scrollHeight>value.clientHeight+1,labelFontSize:label?parseFloat(getComputedStyle(label).fontSize):null,valueFontSize:parseFloat(getComputedStyle(value).fontSize),clippingAncestors:clippers};};' +
    'const primary=[...new Set([...document.querySelectorAll("#task-detail button.primary,#task-detail [data-primary-action=true]")])].filter(displayed).map(element=>{const rect=box(element),parent=box(element.parentElement);return {label:clean(element.textContent),id:element.id,primary:element.classList.contains("primary")||element.dataset.primaryAction==="true",fullyVisible:rect.left>=0&&rect.right<=innerWidth&&rect.top>=0&&rect.bottom<=innerHeight,box:rect,parent};});' +
    'return {facts:required.map(inspect),primaryActions:primary,viewport:{width:innerWidth,height:innerHeight}};' +
  '}');
}

function completionRecordMetrics() {
  const record = data('()=>{' +
    'const record=document.querySelector(".completion-record"),value=record?.querySelector("[data-completion-timestamp]"),freshness=document.querySelector("[data-task-fact-visible=\\\"freshness\\\"]"),box=e=>{const r=e.getBoundingClientRect();return {left:Math.round(r.left),top:Math.round(r.top),right:Math.round(r.right),bottom:Math.round(r.bottom),width:Math.round(r.width),height:Math.round(r.height)}};' +
    'const visible=e=>{if(!e||e.hidden)return false;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};' +
    'const rect=value?box(value):null;return {present:!!record,state:record?.dataset.completionRecordState||null,timestamp:value?.dataset.completionTimestamp||null,dateTime:value?.getAttribute("datetime")||null,text:value?.innerText||"",recordText:record?.innerText||"",visible:visible(value),fullyVisible:!!rect&&rect.left>=0&&rect.right<=innerWidth&&rect.top>=0&&rect.bottom<=innerHeight,box:rect,textOverflow:!!value&&(value.scrollWidth>value.clientWidth+1||value.scrollHeight>value.clientHeight+1),freshness:freshness?.innerText||"",now:document.querySelector("#now")?.innerText||"",status:latestRun?.status||null,primary:document.querySelector("#continue-run")?.textContent.trim()||"",tabs:[...document.querySelectorAll(".detail-tabs [role=tab]")].map(tab=>tab.textContent.trim())};' +
  '}');
  const header=data('()=>{const element=document.querySelector("#task-status .badge");if(!element)return null;const rect=element.getBoundingClientRect(),style=getComputedStyle(element);return {timestamp:element.dataset.completionTimestamp||null,text:element.innerText,visible:style.display!=="none"&&style.visibility!=="hidden"&&rect.width>0&&rect.height>0,fullyVisible:rect.left>=0&&rect.right<=innerWidth&&rect.top>=0&&rect.bottom<=innerHeight};}');
  return {...record,header};
}

function renderedTextProbe(selector) {
  return data('()=>{' +
    'const target=document.querySelector(' + JSON.stringify(selector) + ');if(!target)throw Error("Missing text target: "+' + JSON.stringify(selector) + ');const transparent=value=>value==="transparent"||value==="rgba(0, 0, 0, 0)";let background="";for(let parent=target;parent;parent=parent.parentElement){const candidate=getComputedStyle(parent).backgroundColor;if(!transparent(candidate)){background=candidate;break;}}return {selector:' + JSON.stringify(selector) + ',text:target.textContent.trim(),color:getComputedStyle(target).color,background:background||getComputedStyle(document.documentElement).getPropertyValue("--surface-default").trim()};' +
  '}');
}

function assertNormalTextContrast(selector, theme, label) {
  const probe=renderedTextProbe(selector),ratio=contrast(probe.color,probe.background);
  assert.ok(ratio>=4.5, theme+' '+label+' normal text is at least 4.5:1 (recorded '+ratio.toFixed(3)+')');
  return {...probe,theme,label,ratio};
}

function focusProbe(selector, surfaceSelector, kind) {
  // CSS reports transparent controls as rgba(0, 0, 0, 0). Resolve the painted
  // ancestor rather than treating transparent black as the visible surface.
  const code = '()=>{' +
    'const e=document.querySelector(' + JSON.stringify(selector) + ');const surface=document.querySelector(' + JSON.stringify(surfaceSelector) + ')||document.querySelector(".main-area");' +
    'if(!e||!surface)throw Error("Missing focus probe");try{e.focus({focusVisible:true});}catch{e.focus();}e.setAttribute("data-focus-visible-test","");' +
    'const s=getComputedStyle(e),transparent=v=>v==="rgba(0, 0, 0, 0)"||v==="transparent",painted=node=>{for(let current=node;current;current=current.parentElement){const color=getComputedStyle(current).backgroundColor;if(!transparent(color))return color;}return getComputedStyle(document.documentElement).getPropertyValue("--surface-default").trim();},end=s.boxShadow.indexOf(")");' +
    'return {kind:' + JSON.stringify(kind) + ',selector:' + JSON.stringify(selector) + ',actualFocus:e.matches(":focus-visible"),outlineColor:s.outlineColor,outlineWidth:s.outlineWidth,outlineOffset:s.outlineOffset,innerColor:end>=0?s.boxShadow.slice(0,end+1):"",boxShadow:s.boxShadow,control:transparent(s.backgroundColor)?painted(e.parentElement):s.backgroundColor,surface:painted(surface)};' +
  '}';
  return data(code);
}

function assessFocus(probe, theme) {
  assert.ok(parseFloat(probe.outlineWidth) >= 2, theme + ' ' + probe.kind + ' has a 2 px outer focus layer');
  assert.match(probe.boxShadow, /2px/, theme + ' ' + probe.kind + ' has a 2 px inner focus layer');
  const ratios = {
    outerToControl: contrast(probe.outlineColor, probe.control),
    outerToSurface: contrast(probe.outlineColor, probe.surface),
    innerToControl: contrast(probe.innerColor, probe.control),
    innerToSurface: contrast(probe.innerColor, probe.surface),
  };
  for (const [pair, ratio] of Object.entries(ratios)) {
    assert.ok(ratio >= 3,
      theme + ' ' + probe.kind + ' ' + pair + ' is at least 3:1 (recorded ' + ratio.toFixed(3) + ')');
  }
  return {...probe, theme, ratios};
}

function captureRelevantScroll(name, viewport) {
  const before = data('()=>{' +
    'const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return !e.hidden&&s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};' +
    'const candidates=[...document.querySelectorAll(".page,.inspector-content,#interview")].filter(visible).filter(e=>e.scrollHeight>e.clientHeight+12).sort((a,b)=>(b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight));const target=candidates[0];if(!target)return null;target.dataset.matrixScroller="true";return {name:target.id||target.className,scrollHeight:target.scrollHeight,clientHeight:target.clientHeight,scrollTop:target.scrollTop};' +
  '}');
  if (!before) return null;
  data('()=>{const target=document.querySelector("[data-matrix-scroller=true]");target.scrollTop=Math.min(260,target.scrollHeight-target.clientHeight);return target.scrollTop;}');
  const file = path.join(matrixEvidenceRoot, name + '-' + viewport.name + '-scrolled.png');
  browser('screenshot', file);
  browser('wait', '100');
  data('()=>{const target=document.querySelector("[data-matrix-scroller=true]");target.scrollTop=0;target.removeAttribute("data-matrix-scroller");return true;}');
  return {...before, screenshot: path.relative(root, file)};
}

function openScenario(info, name, viewport) {
  browser('set', 'viewport', String(viewport.width), String(viewport.height));
  const url = new URL(info.scenarios[name]);
  url.searchParams.set('fixture_reload', String(++scenarioReload));
  browser('open', url.toString());
  sessionHasPage = true;
  waitForDashboardReadiness(name + ' scenario application initialization');
  if (name === 'plan') {
    browser('click', '.detail-tabs [data-tab="plan"]');
    browser('wait', '120');
  } else if (name === 'pending-answer') {
    browser('click', '.detail-tabs [data-tab="interview"]');
    browser('wait', '120');
  }
  // Wait for the scenario-specific saved state rather than relying on an
  // arbitrary render delay before capturing the deterministic matrix.
  const expected = {
    workspace: ['#workspace-heading', 'All work'],
    running: ['#task-title', 'Repair runtime monitoring'],
    waiting: ['#task-title', 'routing choice'],
    paused: ['#task-title', 'interrupted build'],
    recovery: ['#task-title', 'preserved feedback checkpoint'],
    completed: ['#task-title', 'completed runtime-monitoring'],
    'completed-freshness': ['#task-title', 'completed runtime-monitoring'],
    'completed-missing': ['#task-title', 'completed runtime-monitoring'],
    'completed-invalid': ['#task-title', 'completed runtime-monitoring'],
    plan: ['#brief-current', 'Plan revision'],
    'pending-answer': ['#conversation', 'saved planning step'],
  }[name];
  waitForCondition('document.querySelector(' + JSON.stringify(expected[0]) + ')?.textContent.includes(' + JSON.stringify(expected[1]) + ')', name + ' scenario render');
  // Browser hash navigation can retain a nested page scroll position. Every
  // matrix image must begin from the unscrolled Figma viewport; separate
  // captures below record intentionally scrolled content.
  data('()=>{[document.scrollingElement,...document.querySelectorAll(".page,.thread-scroll,.inspector-content,#execution")].filter(Boolean).forEach(e=>{e.scrollTop=0;e.scrollLeft=0;});return true;}');
  browser('wait', '60');
}

function openFlowScenario(info, name, viewport, titleFragment) {
  browser('set', 'viewport', String(viewport.width), String(viewport.height));
  const url = new URL(info.scenarios[name]);
  url.searchParams.set('fixture_reload', String(++scenarioReload));
  browser('open', url.toString());
  sessionHasPage = true;
  waitForDashboardReadiness(name + ' flow application initialization');
  waitForCondition('document.querySelector("#task-title")?.textContent.includes(' + JSON.stringify(titleFragment) + ')', name + ' flow render');
  data('()=>{[document.scrollingElement,...document.querySelectorAll(".page,.thread-scroll,.inspector-content,#execution")].filter(Boolean).forEach(e=>{e.scrollTop=0;e.scrollLeft=0;});return true;}');
  browser('wait', '60');
}

function captureRepresentativeFlow(name, viewport) {
  const file=path.join(matrixEvidenceRoot, 'representative-flow-'+name+'-'+viewport.name+'.png');
  browser('screenshot', file);
  // Let the encoder and local browser bridge become idle before the next
  // flow opens a different disposable route. This avoids treating a busy
  // screenshot bridge as a dashboard state failure.
  browser('wait', '250');
  return path.relative(root, file);
}

function assertScenarioRendered(name) {
  const expected = {
    workspace: ['#workspace-heading', /All work/],
    running: ['#task-title', /Repair runtime monitoring/],
    waiting: ['#task-title', /routing choice/],
    paused: ['#task-title', /interrupted build/],
    completed: ['#task-title', /completed runtime-monitoring/],
    'completed-freshness': ['#task-title', /completed runtime-monitoring/],
    'completed-missing': ['#task-title', /completed runtime-monitoring/],
    'completed-invalid': ['#task-title', /completed runtime-monitoring/],
    plan: ['#brief-current', /Plan revision/],
    'pending-answer': ['#conversation', /saved planning step/i],
  };
  const entry = expected[name];
  const text = data('()=>document.querySelector(' + JSON.stringify(entry[0]) + ')?.textContent||""');
  assert.match(text, entry[1], name + ' fixture renders its own deterministic scenario');
}

const exactPlanCriteria = [
  'Confirm exact responsive viewport dimensions.',
  'Verify no unintended clipping or overlap.',
  'Verify every touch target is at least 44×44 px.',
  'Verify semantic variables and AA contrast.',
  'Verify canonical component-instance reuse.',
  'Verify approval and Start building remain separate.',
];

function visible(selector) {
  return data('()=>{const element=document.querySelector(' + JSON.stringify(selector) + ');if(!element)return false;const rect=element.getBoundingClientRect(),style=getComputedStyle(element);return !element.hidden&&style.display!=="none"&&style.visibility!=="hidden"&&rect.width>0&&rect.height>0;}');
}

function assertM2Scenario(name, viewport) {
  if (name === 'workspace') {
    const workspace = data('()=>{' +
      'const clean=value=>String(value||"").replace(/\\s+/g," ").trim();' +
      'const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return !e.hidden&&s.display!=="none"&&s.visibility!=="hidden"&&r.width>0&&r.height>0};' +
      'return {filters:[...document.querySelectorAll("[data-workspace-filter]")].map(e=>[clean(e.querySelector("span")?.textContent),clean(e.querySelector("strong")?.textContent)].join(" ")),filterNames:[...document.querySelectorAll("[data-workspace-filter]")].map(e=>e.getAttribute("aria-label")),controls:["#project-filter","#task-status-filter","#task-search"].map(s=>({selector:s,visible:visible(document.querySelector(s))})),rows:[...document.querySelectorAll(".task-row")].filter(visible).map(e=>({priority:Number(e.dataset.workspacePriority),title:clean(e.querySelector(".task-name")?.textContent),metadata:clean(e.querySelector(".task-meta")?.textContent),state:clean(e.querySelector(".badge")?.textContent),facts:[...e.querySelectorAll(".task-row-step,.task-row-freshness")].map(f=>clean(f.textContent)),action:clean(e.querySelector(".next-action")?.textContent)}))};' +
    '}');
    assert.deepEqual(workspace.filters, ['Waiting on you 3', 'In progress 2', 'Paused / issues 2', 'Completed 1'], viewport.name + ' Workspace preserves the four exact aggregate filters');
    assert.deepEqual(workspace.filterNames, ['Waiting on you 3', 'In progress 2', 'Paused / issues 2', 'Completed 1']);
    assert.ok(workspace.controls.every(control => control.visible), viewport.name + ' Workspace keeps project, state, and search controls visible');
    assert.ok(workspace.rows.length >= 8, viewport.name + ' Workspace shows deterministic task rows');
    assert.deepEqual(workspace.rows.map(row => row.priority), [...workspace.rows.map(row => row.priority)].sort((a, b) => a - b), viewport.name + ' Workspace rows follow attention, paused, live, activity, and completion priority');
    for (const row of workspace.rows) {
      assert.ok(row.title && row.metadata && row.state && row.facts.length === 2 && row.action, viewport.name + ' Workspace row includes title, project/timestamp, runtime state, step/freshness, and action');
    }
    assert.ok(workspace.rows.some(row => row.state === 'Running · worker verified alive'));
    assert.ok(workspace.rows.some(row => row.state === 'Activity reported · live process not confirmed'));
    return;
  }

  const navigation = data('()=>[...document.querySelectorAll(".detail-tabs [role=tab]")].map(tab=>tab.textContent.trim())');
  assert.deepEqual(navigation, ['Now', 'Conversation', 'Plan', 'Changes', 'Preview', 'Checks', 'History'], name + ' retains every labeled task destination');

  if (['running', 'waiting', 'paused', 'completed'].includes(name)) {
    const task = taskFoldMetrics(),facts=Object.fromEntries(task.facts.map(fact=>[fact.key,fact]));
    for (const key of ['state', 'step', 'objective', 'blocker', 'role', 'freshness']) {
      const fact=facts[key];
      assert.equal(fact?.present, true, name + ' renders its '+key+' fact in the operational hero');
      assert.equal(fact?.visible, true, name + ' '+key+' fact is rendered');
      assert.equal(fact?.fullyVisible, true, name + ' '+key+' fact fits inside the unscrolled '+viewport.name+' viewport and all clipping ancestors: '+JSON.stringify(fact));
      assert.equal(fact?.textOverflow, false, name + ' '+key+' fact has no clipped text box');
      assert.ok(fact?.visibleText, name + ' '+key+' fact exposes actual rendered text instead of hidden detail text');
      assert.doesNotMatch(fact.visibleText, /(?:objective|blocker) detail available/i, name + ' '+key+' never substitutes a generic detail label for the operational fact');
      if (['objective', 'blocker', 'role', 'freshness'].includes(key)) {
        assert.ok(fact.labelFontSize >= 12, name + ' '+key+' label keeps the accepted 12 px essential-metadata minimum');
        assert.ok(fact.valueFontSize >= 14, name + ' '+key+' value keeps the accepted 14 px body-text minimum');
      }
    }
    assert.equal(task.primaryActions.length, 1, name + ' has exactly one visually primary task-context action above the fold');
    assert.equal(task.primaryActions[0].primary, true, name + ' visible task-context action is visually primary');
    assert.equal(task.primaryActions[0].fullyVisible, true, name + ' primary action fully fits above the fold');
    if (viewport.name === 'mobile') assert.equal(task.primaryActions[0].box.width, task.primaryActions[0].parent.width, name + ' mobile primary action fills its task-action parent');
    const expected=data('()=>({objective:String(latestRun.monitor?.objective||""),question:String(latestRun.questions?.[0]?.question||"")})');
    const normalizedObjective=expected.objective.replace(/\s+/g,' ').trim(),visibleObjective=facts.objective.visibleText.replace(/…$/,'');
    assert.ok(visibleObjective.length>=48&&normalizedObjective.startsWith(visibleObjective), name + ' exposes a meaningful task-specific objective instead of a generic availability label');
    const objectiveDetail=data('()=>{const detail=document.querySelector(".monitor-objective-detail");if(!detail)return null;detail.open=true;const value=detail.querySelector("p"),parent=detail.getBoundingClientRect(),rect=value.getBoundingClientRect(),result={text:value.innerText.replace(/\\s+/g," ").trim(),horizontalOverflow:value.scrollWidth>value.clientWidth+1||rect.left<parent.left-1||rect.right>parent.right+1};detail.open=false;return result;}');
    if (objectiveDetail) {
      assert.equal(objectiveDetail.text, normalizedObjective, name + ' preserves the complete long objective in an accessible detail');
      assert.equal(objectiveDetail.horizontalOverflow, false, name + ' complete objective detail wraps without horizontal overflow');
    } else assert.equal(facts.objective.visibleText, normalizedObjective, name + ' keeps short objectives complete in the visible hero value');
    if (name === 'running') {
      assert.equal(facts.state.visibleText, 'Running · worker verified alive');
      assert.match(facts.step.visibleText, /^Current step · /);
      assert.match(facts.freshness.visibleText, /PID 2418/);
      assert.match(facts.blocker.visibleText, /No blocker/);
      assert.equal(task.primaryActions[0].label, 'Pause after current step');
    }
    if (name === 'waiting') {
      assert.equal(facts.state.visibleText, 'Waiting on you · Answer needed');
      assert.match(facts.step.visibleText, /^Current saved step · /);
      assert.equal(task.primaryActions[0].label, 'Answer 3 questions');
      // The fold shows a concise, specific preview; the complete exact question
      // remains rendered in the decision card instead of being hidden inside a
      // clipped operational-fact node.
      assert.match(facts.blocker.visibleText, /^Answer 3 questions · first unresolved: The worker state says the planning step was/);
      assert.match(data('()=>document.querySelector(".decision-card")?.innerText||""'), new RegExp(expected.question.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\n+/g, '\\s+')));
      assert.match(data('()=>document.querySelector("#now")?.textContent||""'), /After the final answer is saved, planning continues automatically\./);
      assert.match(data('()=>document.querySelector("#now")?.textContent||""'), /This does not approve a plan or start implementation\./);
    }
    if (name === 'paused') {
      assert.equal(facts.state.visibleText, 'Interrupted · saved work preserved');
      assert.match(facts.step.visibleText, /^Last confirmed step · /);
      assert.doesNotMatch(facts.step.visibleText, /^(?:Next step|Authoritative completion recorded)/);
      assert.match(data('()=>document.querySelector("#now")?.textContent||""'), /Review recovery → Inspect interrupted attempt → Recover saved work → Resume separately/);
      assert.equal(task.primaryActions[0].label, 'Review recovery');
    }
    if (name === 'completed') {
      assert.equal(facts.state.visibleText, 'Completed · recorded evidence');
      assert.match(facts.step.visibleText, /^Last confirmed step · /);
      assert.doesNotMatch(facts.step.visibleText, /^(?:Next step|Authoritative completion recorded)/);
      assert.match(data('()=>document.querySelector("#now")?.textContent||""'), /No reply needed\. 8 checks were recorded as passing for source abc123\. Current freshness is unavailable\./);
      assert.equal(task.primaryActions[0].label, 'Review checks');
      // Regression F1: the prior passing assertion covered only the evidence
      // sentence and Review checks, omitting C08's authoritative completion
      // timestamp requirement. The four source timestamps below are distinct.
      const completion=completionRecordMetrics();
      const expected=data('()=>({completion:latestRun?.completed_at||null,polling:latestRun?.monitor?.checked_at||null,stage:(latestRun?.stages||[]).at(-1)?.finished_at||null,evidence:latestRun?.validation?.recorded_at||null})');
      assert.equal(completion.present, true, viewport.name + ' completed task exposes a dedicated completion record');
      assert.equal(completion.state, 'recorded', viewport.name + ' completion record is saved and authoritative');
      assert.equal(completion.timestamp, expected.completion, viewport.name + ' completion record preserves the saved completed_at value');
      assert.equal(completion.dateTime, expected.completion, viewport.name + ' completion record uses the exact saved ISO value in its time element');
      assert.equal(completion.header?.timestamp, expected.completion, viewport.name + ' task header exposes the same saved completion value');
      assert.match(completion.header?.text||'', /Completed · recorded/);
      assert.equal(completion.header?.visible, true, viewport.name + ' task-header completion time is rendered');
      assert.equal(completion.header?.fullyVisible, true, viewport.name + ' task-header completion time remains above the operational fold');
      assert.match(completion.recordText, /COMPLETION RECORDED/);
      assert.equal(completion.visible, true, viewport.name + ' completion timestamp is rendered');
      // The fixed mobile viewport prioritizes its required six operational
      // facts and primary action; the matrix separately captures scrolled
      // Completed content. Require a real rendered, non-clipped value rather
      // than pretending that C08 requires it above that operational fold.
      assert.equal(completion.textOverflow, false, viewport.name + ' completion timestamp wraps without clipping');
      assert.notEqual(completion.timestamp, expected.polling, 'completed_at is not monitor polling time');
      assert.notEqual(completion.timestamp, expected.stage, 'completed_at is not stage finish time');
      assert.notEqual(completion.timestamp, expected.evidence, 'completed_at is not evidence-record time');
      assert.match(completion.freshness, /Source abc123 · unavailable/);
      return {fold:task,visible_fact_text:Object.fromEntries(task.facts.map(fact=>[fact.key,fact.visibleText])),completion_record:completion,completion_sources:expected};
    }
    return {fold:task,visible_fact_text:Object.fromEntries(task.facts.map(fact=>[fact.key,fact.visibleText]))};
  }

  if (name === 'plan') {
    const plan = data('()=>{' +
      'const root=document.querySelector("#brief-current");const clean=value=>String(value||"").replace(/\\s+/g," ").trim();return {text:clean(root?.textContent),buttons:[...root.querySelectorAll("button")].filter(e=>!e.hidden).map(e=>clean(e.textContent)),criteria:[...root.querySelectorAll(".plan-document-section")].map(e=>clean(e.textContent))};' +
    '}');
    for (const text of ['Plan revision 7', 'Origin:', 'Reviewer:', 'State:', 'Intended outcome', 'Requirements', 'Constraints', 'Implementation sequence', 'Verification criteria', 'Assumptions', 'Earlier-revision disclosure', 'Approval records this revision. Starting work is a separate action.']) assert.match(plan.text, new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    for (const criterion of exactPlanCriteria) assert.match(plan.text, new RegExp(criterion.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.deepEqual(plan.buttons, ['Request changes', 'Approve plan revision 7']);
    assert.equal(plan.buttons.includes('Start building'), false, 'unapproved revision cannot expose the Start building action');
  }

  if (name === 'pending-answer') {
    const answer = data('()=>({conversation:document.querySelector("#conversation")?.textContent||"",delivery:document.querySelector("#task-delivery")?.textContent||"",mode:document.querySelector("#live-mode")?.textContent||"",label:document.querySelector("#change-label")?.textContent||"",questionCount:document.querySelector("#question-target")?.options.length||0,request:document.querySelector("#change-history")?.textContent||""})');
    assert.match(answer.conversation, /The worker state says the planning step was saved/);
    assert.match(answer.conversation, /If recovery succeeds, should the dashboard return to plan review/);
    assert.equal(answer.delivery, 'After the final answer is saved, planning continues automatically.');
    assert.equal(answer.mode, 'This does not approve a plan or start implementation.');
    assert.equal(answer.label, 'Your answer');
    assert.equal(answer.questionCount, 3);
    assert.match(answer.request, /req_autocode_20260922_141233_7f4a91/);
    assert.match(answer.request, /Refresh status and reconcile/);
  }
}

(async () => {
  const matrix = [], scrolled = [], focusMeasurements = [], reducedMotionFocusMeasurements = [], normalTextMeasurements = [], m2Interactions = [], representativeFlows = [], completionTimestampChecks = [], completionTimestampFallbacks = [];
  try {
    server = spawn('python3', [fixture], {
      cwd: root,
      env: {...process.env, AUTOCODE_FIXTURE_ROOT: fixtureRoot},
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const info = await fixtureInfo();

    openScenario(info, 'running', viewports[0]);
    let metrics = shellMetrics();
    assert.deepEqual(metrics.topbar, [0, 0, 1440, 56], 'desktop top bar occupies the full-width first shell row');
    assert.deepEqual(metrics.sidebar, [0, 56, 216, 968], 'desktop sidebar starts below the top bar at the 216 px Figma width');
    assert.deepEqual(metrics.main, [216, 56, 1224, 968], 'desktop main content begins after the second-row sidebar');
    assert.equal(metrics.horizontalOverflow, false, 'desktop has no page-level horizontal overflow');

    openScenario(info, 'running', {name: 'breakpoint-1024', width: 1024, height: 768});
    metrics = shellMetrics();
    assert.deepEqual(metrics.topbar, [0, 0, 1024, 56], 'tablet top bar occupies the full-width first shell row');
    assert.deepEqual(metrics.sidebar, [0, 56, 184, 712], '1024 px uses the 184 px tablet sidebar below the top bar');
    assert.deepEqual(metrics.main, [184, 56, 840, 712], '1024 px main content starts after the tablet sidebar');
    assert.equal(metrics.horizontalOverflow, false, '1024 px has no page-level horizontal overflow');

    openScenario(info, 'running', {name: 'breakpoint-760', width: 760, height: 768});
    metrics = shellMetrics();
    assert.deepEqual(metrics.topbar, [0, 0, 760, 56], '760 px keeps the full-width top bar');
    assert.deepEqual(metrics.sidebar, [0, 56, 184, 712], '760 px keeps the 184 px tablet sidebar below the top bar');
    assert.deepEqual(metrics.main, [184, 56, 576, 712], '760 px gives the remaining second-row viewport to main content');
    assert.equal(metrics.horizontalOverflow, false, '760 px has no page-level horizontal overflow');
    assert.deepEqual(metrics.undersized, [], 'every visible tablet control has a 44 by 44 px hit area');
    assert.deepEqual(data('()=>[...document.querySelectorAll(".detail-tabs [role=tab]")].map(t=>t.textContent.trim())'),
      ['Now', 'Conversation', 'Plan', 'Changes', 'Preview', 'Checks', 'History'], 'task destinations retain distinct labels');
    browser('focus', '.detail-tabs [data-tab="now"]');
    browser('press', 'ArrowRight');
    assert.equal(data('()=>document.activeElement.dataset.tab+":"+document.activeElement.getAttribute("aria-selected")'), 'interview:false',
      'keyboard navigation moves focus without silently selecting a new destination');

    openScenario(info, 'running', {name: 'breakpoint-759', width: 759, height: 844});
    const closed = shellMetrics();
    assert.deepEqual(closed.topbar, [0, 0, 759, 56], '759 px retains the full-width mobile top bar');
    assert.deepEqual(closed.main, [0, 56, 759, 788], '759 px reserves no sidebar width and starts content below the top bar');
    browser('screenshot', path.join(shellEvidenceRoot, 'drawer-closed-759.png'));
    browser('click', '#nav-toggle');
    metrics = shellMetrics();
    assert.equal(metrics.open, true, 'drawer trigger opens the overlay');
    assert.equal(metrics.inert, true, 'open drawer makes the underlying main area inert');
    assert.equal(metrics.topbarInert, true, 'open drawer makes the underlying top bar inert');
    assert.deepEqual(metrics.main, closed.main, 'opening the drawer does not reflow the underlying page');
    assert.ok(metrics.drawerClose[2] >= 44 && metrics.drawerClose[3] >= 44, 'drawer has an internal 44 px close control');
    assert.equal(metrics.active, 'drawer-close', 'opening the drawer moves focus to its close control');
    browser('screenshot', path.join(shellEvidenceRoot, 'drawer-open-759.png'));
    browser('click', '#drawer-close');
    metrics = shellMetrics();
    assert.equal(metrics.open, false, 'internal close control dismisses the drawer');
    assert.equal(metrics.inert, false, 'dismissing the drawer restores interaction with the page');
    assert.equal(metrics.topbarInert, false, 'dismissing the drawer restores top-bar interaction');
    assert.equal(metrics.active, 'nav-toggle', 'drawer close restores focus to the trigger');
    browser('click', '#nav-toggle');
    browser('press', 'Escape');
    assert.equal(shellMetrics().open, false, 'Escape dismisses the drawer');
    browser('click', '#nav-toggle');
    browser('click', '#drawer-scrim');
    assert.equal(shellMetrics().open, false, 'scrim dismissal closes the drawer');

    openScenario(info, 'waiting', viewports[2]);
    metrics = shellMetrics();
    assert.deepEqual(metrics.topbar, [0, 0, 390, 56], '390 px retains the full-width mobile top bar');
    assert.deepEqual(metrics.main, [0, 56, 390, 788], '390 px content begins below the top bar without reserved drawer width');
    assert.equal(metrics.horizontalOverflow, false, '390 px has no page-level horizontal overflow');
    assert.deepEqual(metrics.undersized, [], 'every visible mobile control has a 44 by 44 px hit area');
    assert.equal(metrics.continue[2], 358, 'mobile task primary action fills the 16 px-margin content width');
    browser('click', '#nav-toggle');
    assert.equal(shellMetrics().undersized.length, 0, 'drawer controls retain the 44 px minimum');
    browser('click', '#drawer-close');

    for (const theme of ['light', 'dark']) {
      setTheme(theme);
      for (const entry of meaningfulBoundaryRatios()) {
        assert.ok(contrast(entry.border, entry.surface) >= 3, theme + ' ' + entry.selector + ' boundary meets the 3:1 non-text contrast minimum');
      }
    }

    // Filled primary controls keep the darker accent token. Normal text uses a
    // separate foreground token, so selected and linked operational labels
    // continue to meet the 4.5:1 requirement in both themes.
    for (const theme of ['light', 'dark']) {
      openScenario(info, 'running', viewports[0]);
      setTheme(theme);
      normalTextMeasurements.push(assertNormalTextContrast('.detail-tabs [data-tab="now"]', theme, 'selected task tab'));
      normalTextMeasurements.push(assertNormalTextContrast('.task-path li.done', theme, 'completed workflow phase'));
      openScenario(info, 'waiting', viewports[0]);
      setTheme(theme);
      normalTextMeasurements.push(assertNormalTextContrast('.monitor-fact-objective [data-task-fact-full]', theme, 'operational fact body'));
      openScenario(info, 'workspace', viewports[0]);
      setTheme(theme);
      normalTextMeasurements.push(assertNormalTextContrast('.next-action', theme, 'workspace next action'));
    }

    for (const viewport of viewports) {
      for (const name of scenarios) {
        openScenario(info, name, viewport);
        setTheme(name === 'running' ? 'dark' : 'light');
        assertScenarioRendered(name);
        const operationalEvidence=assertM2Scenario(name, viewport);
        const screenshot = path.join(matrixEvidenceRoot, name + '-' + viewport.name + '.png');
        browser('screenshot', screenshot);
        // The browser command acknowledges capture before its image encoder has
        // always flushed. Wait before changing an internal scroller so the
        // unscrolled screenshot cannot inherit the subsequent scroll state.
        browser('wait', '100');
        const measurement = scenarioMetrics();
        assert.equal(measurement.viewport.width, viewport.width, name + ' ' + viewport.name + ' uses the requested viewport width');
        assert.equal(measurement.viewport.height, viewport.height, name + ' ' + viewport.name + ' uses the requested viewport height');
        assert.deepEqual(measurement.shell.topbar, {x: 0, y: 0, width: viewport.width, height: 56}, name + ' ' + viewport.name + ' keeps the fixed top bar in the unscrolled viewport');
        assert.equal(measurement.shell.topbarOwnsViewportOrigin, true, name + ' ' + viewport.name + ' does not obscure the top bar with page content');
        assert.equal(measurement.shell.activePageScrollTop, 0, name + ' ' + viewport.name + ' capture starts at the unscrolled page position');
        assert.equal(measurement.document.horizontalOverflow, false, name + ' ' + viewport.name + ' has no page-level horizontal overflow');
        assert.equal(measurement.header.titleClipped, false, name + ' ' + viewport.name + ' keeps its task title fully visible');
        assert.equal(measurement.header.statusClipped, false, name + ' ' + viewport.name + ' keeps its status text fully visible');
        if (viewport.name !== 'desktop') {
          assert.deepEqual(measurement.targets.undersized, [], name + ' ' + viewport.name + ' keeps every visible target at least 44 px');
        }
        matrix.push({
          scenario: name,
          viewport: {width: viewport.width, height: viewport.height, name: viewport.name},
          figma_node_id: viewport.nodes[name],
          screenshot: path.relative(root, screenshot),
          measurement,
          operational_fold: operationalEvidence?.fold || null,
          long_content_details: operationalEvidence?.details || [],
        });
        const scrolledCapture = captureRelevantScroll(name, viewport);
        if (scrolledCapture) scrolled.push({scenario: name, viewport: viewport.name, figma_node_id: viewport.nodes[name], ...scrolledCapture});
      }
    }
    assert.equal(matrix.length, 21, 'the browser matrix contains exactly one screenshot for each Figma node');
    assert.equal(new Set(matrix.map(entry => entry.figma_node_id)).size, 21, 'each matrix entry maps to a distinct Figma frame');

    // Completion authority must survive a dashboard refresh and remain stable
    // when only evidence/polling freshness changes. These direct-only fixtures
    // never appear in Workspace and cannot mutate a real run.
    freshBrowserSession('completion-record');
    for (const viewport of viewports) {
      openScenario(info, 'completed', viewport);
      const initial=completionRecordMetrics();
      browser('reload');
      browser('wait', '550');
      assertScenarioRendered('completed');
      const afterRefresh=completionRecordMetrics();
      assert.equal(afterRefresh.timestamp, initial.timestamp, viewport.name + ' completion timestamp survives refresh');
      assert.equal(afterRefresh.dateTime, initial.dateTime, viewport.name + ' completion time element survives refresh');
      openScenario(info, 'completed-freshness', viewport);
      const changedFreshness=completionRecordMetrics();
      assert.equal(changedFreshness.timestamp, initial.timestamp, viewport.name + ' evidence freshness change does not rewrite completion time');
      assert.notEqual(changedFreshness.freshness, initial.freshness, viewport.name + ' fixture changes evidence freshness independently');
      completionTimestampChecks.push({viewport: viewport.name, initial, after_refresh: afterRefresh, changed_freshness: changedFreshness});
    }
    for (const [name, state] of [['completed-missing', 'unavailable'], ['completed-invalid', 'invalid']]) {
      openScenario(info, name, viewports[0]);
      const fallback=completionRecordMetrics();
      assert.equal(fallback.status, 'TASK_COMPLETE', name + ' retains authoritative completed status');
      assert.equal(fallback.state, state, name + ' truthfully labels the timestamp state');
      assert.match(fallback.text, /Completion timestamp unavailable/);
      assert.ok(fallback.now.includes('No reply needed.'));
      assert.equal(fallback.primary, 'Review checks');
      assert.ok(fallback.tabs.includes('Changes') && fallback.tabs.includes('History'), name + ' retains completed secondary destinations');
      completionTimestampFallbacks.push({scenario: name, fallback});
    }

    // The fixture refuses writes, which lets this exercise keyboard and draft
    // retention behavior without mutating any user task or provider request.
    freshBrowserSession('draft-retention');
    openScenario(info, 'pending-answer', viewports[0]);
    browser('fill', '#change-text', 'Preserve the saved revision before any provider request.');
    browser('click', '.detail-tabs [data-tab="now"]');
    browser('wait', '80');
    browser('click', '.detail-tabs [data-tab="interview"]');
    browser('wait', '80');
    assert.equal(data('()=>document.querySelector("#change-text").value'), 'Preserve the saved revision before any provider request.', 'answer draft survives navigation');
    browser('focus', '#change-text');
    browser('press', 'Shift+Enter');
    browser('keyboard', 'type', 'Inspect the interrupted attempt first.');
    const multilineDraft = data('()=>document.querySelector("#change-text").value');
    assert.match(multilineDraft, /\nInspect the interrupted attempt first\.$/, 'Shift+Enter inserts a line break rather than sending');
    const beforeEnter = data('()=>({status:document.querySelector("#task-status").textContent,plan:document.querySelector("#brief-current").textContent,action:document.querySelector("#continue-run").textContent})');
    browser('press', 'Enter');
    browser('wait', '250');
    const afterEnter = data('()=>({status:document.querySelector("#task-status").textContent,draft:document.querySelector("#change-text").value,plan:document.querySelector("#brief-current").textContent,action:document.querySelector("#continue-run").textContent,error:document.querySelector("#change-history").textContent})');
    assert.equal(afterEnter.status, beforeEnter.status, 'sending an answer does not approve or start work');
    assert.equal(afterEnter.plan, beforeEnter.plan, 'sending an answer leaves plan approval untouched');
    assert.equal(afterEnter.action, beforeEnter.action, 'sending an answer leaves the task gate untouched');
    assert.equal(afterEnter.draft, multilineDraft, 'a failed fixture delivery retains the exact draft');
    m2Interactions.push({
      name: 'pending-answer draft retention and keyboard semantics',
      draft_before_submit: multilineDraft,
      post_submit: afterEnter,
    });

    openScenario(info, 'recovery', viewports[0]);
    browser('click', '.detail-tabs [data-tab="plan"]');
    browser('wait', '100');
    const approvedPlan = data('()=>({text:document.querySelector("#brief-current").textContent,buttons:[...document.querySelectorAll("#brief-current button")].filter(e=>!e.hidden).map(e=>e.textContent.trim())})');
    assert.match(approvedPlan.text, /Approval records this revision\. Starting work is a separate action\./);
    assert.deepEqual(approvedPlan.buttons, ['Start building'], 'only confirmed approval replaces approval controls with a separate Start building action');
    const approvedPlanCapture = path.join(matrixEvidenceRoot, 'plan-approved-start-separate-desktop.png');
    browser('screenshot', approvedPlanCapture);
    m2Interactions.push({
      name: 'confirmed approval exposes separate start',
      screenshot: path.relative(root, approvedPlanCapture),
      controls: approvedPlan.buttons,
    });

    // Exercise the complete representative path at every required viewport
    // against independent fixture copies. These are actual dashboard requests
    // to the disposable server: answer receipt, exact-plan approval, separate
    // start, uncertain pause, authoritative reconciliation, recovery, and an
    // unavailable-model failure state all remain distinct operations.
    for (const viewport of viewports) {
      const flow={viewport:{name:viewport.name,width:viewport.width,height:viewport.height}};

      freshBrowserSession('answer-'+viewport.name);
      openFlowScenario(info, 'flow-answer-'+viewport.name, viewport, 'Answer the saved recovery');
      browser('click', '.detail-tabs [data-tab="interview"]');
      browser('wait', '100');
      const answerBefore=data('()=>({questions:latestRun.questions?.length||0,status:latestRun.status,approval:latestRun.goal?.approval_status||"",draft:document.querySelector("#change-text").value})');
      browser('fill', '#change-text', 'Preserve the saved revision and inspect the interrupted attempt first.');
      browser('press', 'Enter');
      waitForCondition('(latestRun.questions?.length||0)===2', viewport.name + ' answer receipt');
      const answerAfter=data('()=>({questions:latestRun.questions?.length||0,status:latestRun.status,approval:latestRun.goal?.approval_status||"",draft:document.querySelector("#change-text").value,receipt:(latestRun.chat_messages||[]).find(message=>message.question_id==="question-1")||null})');
      assert.equal(answerBefore.questions, 3, viewport.name+' flow begins with three saved questions');
      assert.equal(answerAfter.questions, 2, viewport.name+' saved answer updates only the addressed question');
      assert.equal(answerAfter.status, answerBefore.status, viewport.name+' answer does not approve or start work');
      assert.equal(answerAfter.approval, answerBefore.approval, viewport.name+' answer preserves the plan gate');
      assert.equal(answerAfter.draft, '', viewport.name+' confirmed answer receipt clears only the submitted draft');
      assert.equal(answerAfter.receipt?.status, 'received', viewport.name+' answer records a durable receipt');
      flow.answer={before:answerBefore,after:answerAfter,screenshot:captureRepresentativeFlow('answer',viewport)};

      freshBrowserSession('plan-'+viewport.name);
      openFlowScenario(info, 'flow-plan-'+viewport.name, viewport, 'Approve the exact plan');
      browser('click', '.detail-tabs [data-tab="plan"]');
      browser('wait', '100');
      browser('click', '#brief-current button.primary');
      waitForCondition('document.querySelector("#brief-current")?.textContent.includes("Revision 7 is confirmed")', viewport.name + ' approval receipt');
      const approved=data('()=>({approval:latestRun.goal?.approval_status||"",buttons:[...document.querySelectorAll("#brief-current button")].filter(button=>!button.hidden).map(button=>button.textContent.trim()),receipt:(latestRun.actions||[]).find(action=>action.label==="Approve goal")||null})');
      assert.equal(approved.approval, 'approved', viewport.name+' exact plan approval returns an authoritative approved state');
      assert.deepEqual(approved.buttons, ['Start building'], viewport.name+' confirmed approval exposes a separate Start building action');
      assert.equal(approved.receipt?.status, 'finished', viewport.name+' approval records a receipt before build start');
      flow.approval={...approved,screenshot:captureRepresentativeFlow('approval',viewport)};
      browser('click', '#brief-current button.primary');
      waitForCondition('latestRun.status==="RUNNING"&&latestRun.active_stage?.stage==="terra"', viewport.name + ' Start building receipt');
      const started=data('()=>({status:latestRun.status,stage:latestRun.active_stage?.stage||"",live:latestRun.monitor?.live?.state||"",receipt:(latestRun.actions||[]).find(action=>action.label==="Start building")||null})');
      assert.equal(started.live, 'alive', viewport.name+' separate Start building action records a verified live worker');
      assert.equal(started.receipt?.status, 'finished', viewport.name+' Start building records its own receipt');
      flow.started={...started,screenshot:captureRepresentativeFlow('started',viewport)};

      browser('click', '#pause-run');
      browser('wait', '160');
      browser('click', '.detail-tabs [data-tab="interview"]');
      browser('wait', '80');
      const uncertain=data('()=>({text:document.querySelector("#change-history").textContent,entries:latestRun.interventions?.entries||[],primary:document.querySelector("#pause-run").textContent})');
      assert.match(uncertain.text, /could not be confirmed/i, viewport.name+' uncertain pause retains its request identity and reconciliation guidance');
      assert.equal(uncertain.entries[0]?.status, 'uncertain', viewport.name+' uncertain pause does not imply a completed mutation');
      flow.uncertain_pause={...uncertain,screenshot:captureRepresentativeFlow('pause-uncertain',viewport)};
      // A fresh route read is the explicit status reconciliation. It leaves
      // the persisted request untouched and receives the fixture's later
      // authoritative receipt without retrying the pause mutation.
      freshBrowserSession('pause-reconcile-'+viewport.name);
      openFlowScenario(info, 'flow-plan-'+viewport.name, viewport, 'Approve the exact plan');
      browser('click', '.detail-tabs [data-tab="interview"]');
      browser('wait', '80');
      const reconciled=data('()=>({entries:latestRun.interventions?.entries||[],text:document.querySelector("#change-history").textContent,status:latestRun.status})');
      assert.equal(reconciled.entries[0]?.status, 'reconciled', viewport.name+' explicit refresh receives the authoritative reconciled pause result');
      assert.match(reconciled.text, /reconciled/i, viewport.name+' reconciled receipt remains visible after refresh');
      flow.reconciled_pause={...reconciled,screenshot:captureRepresentativeFlow('pause-reconciled',viewport)};

      freshBrowserSession('recovery-'+viewport.name);
      openFlowScenario(info, 'flow-recovery-'+viewport.name, viewport, 'Recover the interrupted build');
      browser('click', '#continue-run');
      waitForCondition('document.querySelector("#task-attention")?.textContent.includes("Review recovery")', viewport.name + ' recovery review');
      const recoveryBefore=data('()=>({order:document.querySelector("#task-attention").textContent,inspectionOpen:document.querySelector("#task-attention details")?.open,action:[...document.querySelectorAll("#task-attention button")].map(button=>({label:button.textContent.trim(),disabled:button.disabled,description:button.getAttribute("aria-describedby")}))})');
      assert.match(recoveryBefore.order, /Review recovery.*Inspect interrupted attempt.*Recover saved work.*Resume separately/s, viewport.name+' recovery presents the required ordered inspection path');
      assert.equal(recoveryBefore.inspectionOpen, false, viewport.name+' recovery starts with the interrupted-attempt evidence closed');
      assert.deepEqual(recoveryBefore.action, [{label:'Recover saved work',disabled:true,description:'recovery-inspection-required'}], viewport.name+' recovery cannot bypass inspection or resume work');
      browser('click', '#task-attention summary');
      waitForCondition('document.querySelector("#task-attention details")?.open&&document.querySelector("#task-attention button")?.disabled===false', viewport.name + ' recovery inspection');
      const inspected=data('()=>({open:document.querySelector("#task-attention details")?.open,evidence:document.querySelector("#task-attention details")?.innerText,action:document.querySelector("#task-attention button")?.textContent.trim(),disabled:document.querySelector("#task-attention button")?.disabled})');
      assert.equal(inspected.open, true, viewport.name+' explicit inspection opens the saved interrupted attempt');
      assert.match(inspected.evidence, /req_autocode_20260922_141233_7f4a91/, viewport.name+' inspection exposes the matching saved attempt evidence');
      assert.equal(inspected.action, 'Recover saved work');assert.equal(inspected.disabled, false);
      // The recovery action follows an expanded disclosure and can sit below
      // the clipped viewport in the compact layouts. Dispatch the native
      // button activation in the rendered page so this still exercises the
      // exact UI handler and POST, rather than letting the automation
      // bridge's coordinate target silently miss an enabled control.
      data('()=>{const control=document.querySelector("#task-attention button");if(!control||control.disabled)throw Error("Recover saved work must be enabled after inspection");control.click();return true;}');
      waitForCondition('latestRun.status==="PAUSED_INTERVENTION"', viewport.name + ' recovery receipt');
      const recovered=data('()=>({status:latestRun.status,primary:document.querySelector("#continue-run").textContent,receipt:(latestRun.actions||[]).find(action=>action.label==="Recover saved work")||null})');
      assert.equal(recovered.primary, 'Resume task', viewport.name+' successful recovery leaves a later, separate Resume task action');
      assert.equal(recovered.receipt?.status, 'finished', viewport.name+' recovery records its own receipt');
      browser('click', '#continue-run');
      waitForCondition('latestRun.status==="RUNNING"&&latestRun.active_stage?.stage==="terra"', viewport.name + ' Resume task receipt');
      const resumed=data('()=>({status:latestRun.status,stage:latestRun.active_stage?.stage||"",live:latestRun.monitor?.live?.state||"",receipt:(latestRun.actions||[]).find(action=>action.label==="Resume task")||null})');
      assert.equal(resumed.live, 'alive', viewport.name+' explicit Resume task starts the next live step only after recovery');
      assert.equal(resumed.receipt?.status, 'finished', viewport.name+' separate resume records its own receipt');
      flow.recovery={before:recoveryBefore,inspected,after:recovered,resumed,screenshot:captureRepresentativeFlow('recovery-resumed',viewport)};

      freshBrowserSession('model-'+viewport.name);
      openFlowScenario(info, 'unavailable-model', viewport, 'Replace an unavailable saved planning model');
      data('()=>{syncModelOptions({usable:true,models:[]});document.querySelector("#task-detail .context-menu").open=true;return true;}');
      const unavailable=data('()=>{const selector=document.querySelector("#task-astra-replacement");return {saved:latestRun.model_settings.roles.astra,disabled:selector.disabled,reason:document.querySelector("#task-astra-replacement-reason").textContent,description:selector.getAttribute("aria-describedby"),continueDisabled:document.querySelector("#continue-run").disabled};}');
      assert.equal(unavailable.saved, 'openai/retired-model', viewport.name+' unavailable model flow preserves the saved value');
      assert.equal(unavailable.disabled, true, viewport.name+' unavailable model flow keeps an explicit replacement control disabled');
      assert.equal(unavailable.description, 'task-astra-replacement-reason', viewport.name+' unavailable model control exposes its adjacent reason');
      assert.equal(unavailable.continueDisabled, true, viewport.name+' unresolved unavailable model prevents a blind continue');
      flow.unavailable_model={...unavailable,screenshot:captureRepresentativeFlow('model-unavailable',viewport)};
      representativeFlows.push(flow);
    }

    for (const theme of ['light', 'dark']) {
      openScenario(info, 'plan', viewports[0]);
      setTheme(theme);
      focusMeasurements.push(assessFocus(focusProbe('#continue-run', '.main-area', 'primary'), theme));
      focusMeasurements.push(assessFocus(focusProbe('#task-back', '.main-area', 'secondary'), theme));
      focusMeasurements.push(assessFocus(focusProbe('.detail-tabs [data-tab="now"]', '.detail-toolbar', 'tab'), theme));
      data('()=>{const d=document.querySelector("#project-removal-dialog");if(!d.open)d.showModal();return true;}');
      focusMeasurements.push(assessFocus(focusProbe('#project-removal-confirm', '#project-removal-dialog', 'destructive dialog control'), theme));
      focusMeasurements.push(assessFocus(focusProbe('#project-removal-cancel', '#project-removal-dialog', 'dialog safe action'), theme));
      data('()=>{document.querySelector("#project-removal-dialog").close();return true;}');
      data('()=>{document.querySelector("#continue-run").disabled=true;return true;}');
      focusMeasurements.push(assessFocus(focusProbe('#continue-run', '.main-area', 'disabled-adjacent primary control'), theme));
      data('()=>{document.querySelector("#continue-run").disabled=false;return true;}');

      openScenario(info, 'workspace', viewports[0]);
      setTheme(theme);
      focusMeasurements.push(assessFocus(focusProbe('#task-search', '.main-area', 'input'), theme));
      focusMeasurements.push(assessFocus(focusProbe('#project-filter', '.main-area', 'selector'), theme));

      openScenario(info, 'plan', viewports[2]);
      setTheme(theme);
      focusMeasurements.push(assessFocus(focusProbe('#nav-toggle', '.topbar', 'drawer trigger'), theme));
    }

    browser('set', 'media', 'light', 'reduced-motion');
    openScenario(info, 'running', viewports[2]);
    setTheme('light');
    reducedMotionFocusMeasurements.push(assessFocus(focusProbe('#continue-run', '.main-area', 'reduced-motion primary'), 'light reduced-motion'));
    browser('set', 'media', 'light');

    auditBrowserSession('final');
    const manifest = {
      generated_at: new Date().toISOString(),
      figma_file: 'https://www.figma.com/design/kcSv6tbrqK3pzMFl2BRSUk',
      fixture: 'tools/dashboard/tests/unified_browser_fixture.py',
      screenshot_count: matrix.length,
      screenshots: matrix,
      relevant_scrolled_screenshots: scrolled,
      focus_indicator_measurements: focusMeasurements,
      reduced_motion_focus_measurements: reducedMotionFocusMeasurements,
      normal_text_contrast_measurements: normalTextMeasurements,
      m2_interactions: m2Interactions,
      representative_flows: representativeFlows,
      completion_timestamp_checks: completionTimestampChecks,
      completion_timestamp_fallbacks: completionTimestampFallbacks,
      browser_sessions: browserSessionAudits,
      source_snapshot: sourceSnapshot(),
      forced_colors_focus: {
        browser_emulation: 'unavailable',
        source_assertion: true,
        status: 'NOT_VERIFIED',
        limitation: 'agent-browser exposes color-scheme and reduced-motion emulation but not forced-colors; the CSS source assertion is retained without treating rendered forced-colors behavior as passed.',
      },
      comparison_scope: 'Each browser screenshot is explicitly mapped to its corresponding Figma node for independent visual comparison; M2 operational hierarchy, exact fixture text, action separation, and responsive geometry are asserted against deterministic local state.',
    };
    const manifestPath = path.join(evidenceRoot, 'm2-scenario-matrix-manifest.json');
    fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');

    console.log('Browser M1/M2 shell, focus, operational-flow, and 21-screen matrix passed. Manifest: ' + path.relative(root, manifestPath));
  } finally {
    if (server && !server.killed) server.kill('SIGINT');
    try { browser('close', '--all'); } catch {}
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
