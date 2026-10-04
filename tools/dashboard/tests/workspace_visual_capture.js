// M3 visual recapture: photographs the implemented workspace at the states
// the approved Figma exports define and records a manifest mapping every
// capture to its read-only reference export. Driven manually or by the M3
// gate runs; verifies lifecycle navigation and first-view hierarchy as well.
// Before visual acceptance, compare source_sha256 with the checkout. Missing
// or stale source bindings require recapture; PNG hashes alone are insufficient.
//
//   M3_VISUAL_EVIDENCE_DIR=<dir> node workspace_visual_capture.js
const assert = require('node:assert/strict');
const {spawn, execFileSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {dashboardReadinessExpression, waitForReadiness} = require('./browser_readiness');

const root = path.resolve(__dirname, '../../..');
const fixture = path.join(__dirname, 'workspace_visual_fixture.py');
const evidenceRoot = process.env.M3_VISUAL_EVIDENCE_DIR
  ? path.resolve(root, process.env.M3_VISUAL_EVIDENCE_DIR)
  : path.join(root, '.autocode', 'evidence', 'm3-visuals-current', 'run-' + process.pid);
fs.mkdirSync(evidenceRoot, {recursive: true});
const session = 'dashboard-m3-visuals-' + process.pid;
let server;
let scenarioReload = 0;

function browser(...args) {
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

function evaluate(script) { return JSON.parse(JSON.parse(browser('eval', script).trim())); }
function data(body) { return evaluate('JSON.stringify((' + body + ')())'); }
function waitForCondition(expression, label, attempts = 48) {
  let last;
  for (let attempt = 0; attempt < attempts; attempt++) {
    last = data('()=>({matched:Boolean(' + expression + '),ready:document.readyState,shell:!!document.querySelector(".app-shell"),title:document.querySelector("#task-title")?.textContent||""})');
    if (last.matched) return;
    if (attempt < attempts - 1) browser('wait', '250');
  }
  throw Error(label + ' did not become true: ' + JSON.stringify(last));
}
function openScenario(info, name, width, height) {
  browser('set', 'viewport', String(width), String(height));
  const url = new URL(info.scenarios[name]);
  url.searchParams.set('fixture_reload', String(++scenarioReload));
  browser('open', url.toString());
  waitForReadiness({label: name + ' initialization', attempts: 48,
    probe: () => data(dashboardReadinessExpression()),
    wait: () => browser('wait', '250')});
  waitForCondition('typeof latestRun!=="undefined"&&latestRun?.run?.endsWith('+JSON.stringify('/'+name)+')&&!taskReadError', name + ' render');
}
function shot(file) {
  const target = path.join(evidenceRoot, file);
  browser('eval', '(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode().catch(()=>{})));return true})()');
  browser('screenshot', target);
  return path.relative(root, target);
}

const sha256 = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const reference = name => {
  const file = path.join(root, '.autocode-ui', 'approved-design', name);
  return {reference: name, reference_path: path.relative(root, file), reference_sha256: sha256(file)};
};

function lifecycleContrast(selector){
  return data('()=>{const host=document.querySelector('+JSON.stringify(selector)+');const rgba=value=>value.match(/[\\d.]+/g).map(Number);const lum=value=>rgba(value).slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);return [...host.querySelectorAll("p,h3,button")].filter(e=>e.getBoundingClientRect().height).map(e=>{let bg=e;while(bg&&getComputedStyle(bg).backgroundColor==="rgba(0, 0, 0, 0)")bg=bg.parentElement;const fg=lum(getComputedStyle(e).color),back=lum(getComputedStyle(bg).backgroundColor);return {text:e.textContent,ratio:(Math.max(fg,back)+.05)/(Math.min(fg,back)+.05),background:getComputedStyle(bg).backgroundColor};});}');
}

function fixtureInfo() {
  return new Promise((resolve, reject) => {
    let output = '', errors = '';
    const deadline = setTimeout(() => reject(Error('Timed out waiting for the disposable M3 workspace fixture.')), 20000);
    server.stdout.on('data', chunk => {
      output += String(chunk);
      const match = output.match(/^FIXTURE=(.+)$/m);
      if (!match) return;
      clearTimeout(deadline);
      try { resolve(JSON.parse(match[1])); } catch (error) { reject(error); }
    });
    server.stderr.on('data', chunk => { errors += String(chunk); });
    server.once('error', reject);
    server.once('exit', code => { if (code) reject(Error('M3 workspace fixture exited with ' + code + ': ' + (errors || output))); });
  });
}

(async () => {
  server = spawn(process.env.AUTOCODE_TEST_PYTHON || 'python3', [fixture], {cwd: root, env: {...process.env, AUTOCODE_FIXTURE_ROOT: path.join(evidenceRoot, 'fixture-state')}, stdio: ['ignore', 'pipe', 'pipe']});
  const info = await fixtureInfo();
  const manifest = {generated_at: new Date().toISOString(), source: 'tools/dashboard/tests/workspace_visual_capture.js',
                    workspace_root: path.relative(root, root), captures: []};
  try {
  // Bind visual evidence to the actual served implementation, not just image
  // hashes. An older manifest cannot establish acceptance of later CSS/JS.
  // The list covers the shipped dashboard assets plus every changed M3 test
  // file (modified and new), so the manifest alone establishes the complete
  // declared source snapshot.
  const sourceFiles=execFileSync('git',['ls-files','-z','--cached','--others','--exclude-standard'],{cwd:root,encoding:'utf8'}).split('\0')
    .filter(file=>file&&/^(tools|tests|scenarios|test-scenarios)\//.test(file)&&/\.(py|js|css|html|json|svg)$/.test(file)&&!file.startsWith('.autocode/')&&fs.existsSync(path.join(root,file)));
  manifest.source_sha256=Object.fromEntries([...new Set(sourceFiles)].sort().map(file=>[file,sha256(path.join(root,file))]));
  manifest.git_head=execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();
  manifest.served_assets_sha256={};
  const expected=JSON.parse(execFileSync(process.env.AUTOCODE_TEST_PYTHON||'python3',['-c',
    "import sys,json,hashlib;sys.path[:0]=['tools/dashboard','tools'];import agent_console as c;print(json.dumps({p:hashlib.sha256(v.encode()).hexdigest() for p,v in [('/',c.INDEX),('/static/style.css',c.STYLE),('/static/app.js',c.APP)]}))"],{cwd:root,encoding:'utf8'}));
  for(const [route,digest] of Object.entries(expected)){
    const response=await fetch(new URL(route,info.scenarios['flow-m3-chat']));
    if(!response.ok)throw Error('Visual fixture asset unavailable: '+route);
    const actual=crypto.createHash('sha256').update(await response.text()).digest('hex');
    if(actual!==digest)throw Error('Visual fixture serves stale source: '+route);
    manifest.served_assets_sha256[route]=actual;
  }


    // Desktop building + Work overview (reference 422-1495, 1440x900).
    openScenario(info, 'flow-visual-building', 1440, 900);
    waitForCondition('document.querySelector("#now .work-summary")?.textContent.length>0', 'Work summary rendered');
    const hierarchy=data('()=>({lead:document.querySelector("#now .work-summary").getBoundingClientRect().top,checklist:document.querySelector("#now .work-checklist").getBoundingClientRect().top,facts:document.querySelector("#now .work-facts").getBoundingClientRect().top,sidebar:document.querySelector(".sidebar").getBoundingClientRect().top})');
    assert.ok(hierarchy.lead<hierarchy.checklist&&hierarchy.checklist<hierarchy.facts,JSON.stringify(hierarchy));
    assert.equal(hierarchy.sidebar,0);manifest.native_desktop_hierarchy=hierarchy;
    assert.equal(data('()=>getComputedStyle(document.querySelector("#pause-run")).backgroundColor'),'rgb(255, 255, 255)','Pause is the secondary action on the reference white surface');
    manifest.captures.push({capture: shot('desktop-building-work.png'), viewport: {width: 1440, height: 900}, state: 'desktop building + Work overview', reference_source_canvas: {width:1440,height:900}, reference_export_dimensions: {width:1024,height:640}, comparison_role: 'primary_visual_acceptance', comparison_transform: {scale: 1024 / 1440, output_width: 1024, output_height: 640}, geometry_evidence: '.autocode-ui/approved-design/422-1495.design.txt: sidebar220 + workspace1220, both height900; display this 1440x900 capture at 1024x640 to compare with the scaled reference export', ...reference('422-1495.png')});

    // A 1024x640 CSS viewport exercises responsive behavior. It is not a
    // matched-scale rendering of the 1440x900 design canvas and cannot alone
    // establish visual acceptance against the 1024x640 Figma export.
    openScenario(info, 'flow-visual-building', 1024, 640);
    waitForCondition('document.querySelector("#now .work-summary")?.textContent.length>0', 'Work summary rendered at export dimensions');
    manifest.captures.push({capture: shot('desktop-building-work-1024x640.png'), viewport: {width: 1024, height: 640}, state: 'desktop building + Work overview at supplementary responsive viewport', reference_source_canvas: {width:1440,height:900}, reference_export_dimensions: {width:1024,height:640}, comparison_role: 'supplementary_responsive_only', geometry_evidence: 'actual 1024x640 CSS viewport; compare the 1440x900 capture displayed at export scale for AC21 visual acceptance', ...reference('422-1495.png')});

    // Mobile conversation foreground (reference 423-306, 390x844).
    openScenario(info, 'flow-visual-building', 390, 844);
    waitForCondition('document.querySelectorAll("#conversation .chat-message").length>=3', 'mobile transcript rendered');
    manifest.captures.push({capture: shot('mobile-chat.png'), viewport: {width: 390, height: 844}, state: 'mobile conversation foreground', ...reference('423-306.png')});

    // Mobile details sheet above dimmed chat, captured in the Plan state its
    // mapped reference (423-353) shows.
    browser('click', '#details-drawer-toggle');
    waitForCondition('document.querySelector(".app-shell").classList.contains("details-open")', 'details sheet opened');
    browser('click', '.detail-tabs [data-tab="plan"]');
    waitForCondition('document.querySelector("#brief-current")?.textContent.includes("Plan revision")', 'details sheet shows the saved plan state');
    browser('wait', '250');
    const mobilePlan=data('()=>({milestones:document.querySelectorAll("#brief-current .plan-milestones li").length,currentBottom:document.querySelector("#brief-current .plan-current-task").getBoundingClientRect().bottom,paneBottom:document.querySelector(".context-pane-content").getBoundingClientRect().bottom-parseFloat(getComputedStyle(document.querySelector(".context-pane-content")).paddingBottom),requirements:!!document.querySelector("#brief-current .plan-checklist")})');
    assert.equal(mobilePlan.milestones,6);assert.equal(mobilePlan.requirements,true);
    assert.ok(mobilePlan.currentBottom<=mobilePlan.paneBottom,JSON.stringify(mobilePlan));manifest.mobile_plan_hierarchy=mobilePlan;
    manifest.captures.push({capture: shot('mobile-details-drawer.png'), viewport: {width: 390, height: 844}, state: 'mobile details sheet (Plan) over dimmed chat', ...reference('423-353.png')});
    browser('click', '#details-drawer-close');
    waitForCondition('!document.querySelector(".app-shell").classList.contains("details-open")', 'details sheet closed');

    // Mobile project sheet (reference 423-438).
    browser('click', '#nav-toggle');
    waitForCondition('document.querySelector(".app-shell").classList.contains("nav-open")', 'project sheet opened');
    browser('wait', '250');
    manifest.captures.push({capture: shot('mobile-project-drawer.png'), viewport: {width: 390, height: 844}, state: 'mobile project sheet', ...reference('423-438.png')});
    browser('click', '#drawer-close');
    waitForCondition('!document.querySelector(".app-shell").classList.contains("nav-open")', 'project sheet closed');

    // Every remaining approved operational state uses the same production
    // page with an isolated, explicit recorded-state fixture. This capture
    // inventory is evidence for visual review, not a PASS from that review.
    for(const [name,tab,ref] of [
      ['requirements','now','422-1180.png'],
      ['reviewed-plan','plan','422-1342.png'],
      ['building','plan','422-1642.png'],
      ['building','changes','422-1806.png'],
      ['building','execution','422-1942.png'],
      ['recovery','now','422-2081.png'],
      ['ready','now','422-2215.png'],
      ['building','preview','448-475.png'],
    ]){
      openScenario(info,'flow-visual-'+name,1440,900);
      browser('click','.detail-tabs [data-tab="'+tab+'"]');
      waitForCondition('typeof currentTab!=="undefined"&&currentTab==='+JSON.stringify(tab),name+' '+tab);
      if(tab==='changes'){waitForCondition('!!document.querySelector("#changes-content .diff-stage")','saved stage');browser('click','#changes-content .diff-stage>summary');waitForCondition('!!document.querySelector("#changes-content .diff-output")','saved diff');assert.match(data('()=>document.querySelector("#changes-content .diff-stage>summary").textContent'),/^Builder/);}
      if(name==='recovery'){
        const recovery=data('()=>({status:document.querySelector("#task-status").textContent,card:document.querySelector("#conversation .chat-recovery")?.textContent,top:document.querySelector("#conversation .chat-recovery")?.getBoundingClientRect().top,bottom:document.querySelector("#conversation .chat-recovery")?.getBoundingClientRect().bottom,viewportBottom:document.querySelector("#interview").getBoundingClientRect().bottom,buttons:document.querySelectorAll("#conversation .chat-recovery button").length})');
        assert.match(recovery.status,/Recovering/);assert.match(recovery.card,/No action is needed/);
        assert.ok(recovery.top>=0&&recovery.bottom<=recovery.viewportBottom,JSON.stringify(recovery));assert.equal(recovery.buttons,0);
        assert.match(data('()=>document.querySelector("#now .work-roles").textContent'),/Resolver · openai\/gpt-6-sol/);
        manifest.recovery_chat=recovery;
      }
      if(name==='reviewed-plan')assert.doesNotMatch(data('()=>document.querySelector("#conversation").textContent'),/Approved version 7/);
      if(name==='ready'){
        assert.equal(data('()=>getComputedStyle(document.querySelector("#conversation .chat-ready button.primary")).backgroundColor'),'rgb(98, 87, 232)','reference primary action is purple');
        assert.match(data('()=>document.querySelector("#conversation .chat-ready").textContent'),/Ready to inspect/);
        browser('click','#conversation .chat-ready button:first-child');
        waitForCondition('currentTab==="preview"','completion preview link');
        assert.match(data('()=>document.querySelector("#preview-empty-note").textContent'),/No app address is saved.*Start your app/);
        browser('click','#conversation .chat-ready button:last-child');
        waitForCondition('currentTab==="changes"&&!!document.querySelector("#changes-content .diff-stage")','completion saved changes link');
        browser('click','.detail-tabs [data-tab="now"]');
        waitForCondition('currentTab==="now"','completion Work pane');
      }
      if(name==='recovery'||name==='ready'){
        const card=name==='recovery'?'.chat-recovery':'.chat-ready';
        manifest.lifecycle_contrast??={};manifest.lifecycle_contrast[name]={};
        for(const theme of ['light','dark']){
          browser('eval','document.documentElement.dataset.theme='+JSON.stringify(theme));
          const measurements=lifecycleContrast('#conversation '+card);
          assert.ok(measurements.length>=2);
          for(const measured of measurements)assert.ok(measured.ratio>=4.5,theme+' '+name+' '+JSON.stringify(measured));
          manifest.lifecycle_contrast[name][theme]=measurements;
        }
        browser('eval','document.documentElement.dataset.theme="light"');
      }
      if(tab==='preview'){
        browser('fill','#preview-url',info.preview_url);
        browser('click','#preview-form button');
        waitForCondition('!!document.querySelector("#preview-content iframe")','real preview frame');
      }
      const send=data('()=>{const button=document.querySelector("#send-change");return {disabled:button.disabled,background:getComputedStyle(button).backgroundColor};}');
      assert.equal(send.disabled,true,'empty composer cannot send');assert.notEqual(send.background,'rgb(98, 87, 232)','disabled Send remains visually neutral');
      manifest.captures.push({capture:shot('desktop-'+name+'-'+tab+'.png'),viewport:{width:1440,height:900},state:name+' / '+tab,
        reference_source_canvas:{width:1440,height:900},reference_export_dimensions:{width:1024,height:640},
        comparison_role:'primary_visual_acceptance',comparison_transform:{scale:1024/1440,output_width:1024,output_height:640},...reference(ref)});
    }

    const errors = browser('errors').trim();
    if (errors && !/^(?:|\[\]|No page errors\.?|No errors\.?)$/i.test(errors)) throw Error('page errors during captures: ' + errors);
    for (const entry of manifest.captures) entry.sha256 = sha256(path.join(root, entry.capture));
    for(const [file,digest] of Object.entries(manifest.source_sha256))if(sha256(path.join(root,file))!==digest)throw Error('Source changed during visual capture: '+file);
    manifest.device_scale=data('()=>devicePixelRatio');
    manifest.fonts=data('()=>({status:document.fonts.status,body:getComputedStyle(document.body).fontFamily})');
    const manifestPath = path.join(evidenceRoot, 'm3-visual-manifest.json');
    fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
    console.log('M3 visual captures recorded. Manifest: ' + path.relative(root, manifestPath));
  } finally {
    if (server && !server.killed) server.kill('SIGINT');
    try { browser('close'); } catch {}
  }
})().catch(error => { console.error(error.stack || error); process.exit(1); });
