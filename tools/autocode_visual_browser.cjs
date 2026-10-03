/* Capture acquisition only: an independent reviewer must still inspect the image. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const {createHash} = require('node:crypto');
const hash = data => createHash('sha256').update(data).digest('hex');

async function main() {
  const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const localRequire = createRequire(path.join(process.cwd(), 'package.json'));
  const {chromium} = localRequire(config.playwright_module || 'playwright');
  const fixture = localRequire(path.resolve(config.fixture));
  const result = {status: 'UNAVAILABLE', setup: false, teardown: false, responses: [], ready: [], errors: []};
  let browser, context, page;
  const responses = [];
  const pending = new Set();
  let sealed = false;
  const viewport = config.case.viewport;
  try {
    if (typeof fixture.setup !== 'function' || typeof fixture.teardown !== 'function')
      throw new Error('Capture fixture must expose setup and teardown');
    browser = await chromium.launch({headless: true, ...(config.executable ? {executablePath: config.executable} : {})});
    context = await browser.newContext({viewport: {width: viewport.width, height: viewport.height},
      deviceScaleFactor: viewport.device_scale_factor, serviceWorkers: 'block'});
    page = await context.newPage();
    page.setDefaultTimeout(Math.min(config.timeout_ms, 15000));
    page.on('request', request => {
      pending.add(request);
      if (sealed) result.errors.push(`New request after readiness: ${request.url()}`);
    });
    page.on('requestfinished', request => pending.delete(request));
    page.on('requestfailed', request => pending.delete(request));
    page.on('response', response => {
      // Keep the actual bytes loaded by this fresh browser context, not a second HTTP request.
      responses.push((async () => {
        const bytes = await response.body();
        return {url: response.url(), status: response.status(), sha256: hash(bytes)};
      })().catch(error => ({error: String(error.message || error)})));
    });
    page.on('requestfailed', request => result.errors.push(`Request failed: ${request.url()}`));
    await fixture.setup({page, context});
    result.setup = true;
    for (const condition of config.ready) {
      await page.waitForFunction(c => document.querySelector(c.selector)?.getAttribute(c.attribute) === c.equals,
        condition, {timeout: Math.min(config.timeout_ms, 15000)});
      result.ready.push(condition);
    }
    await page.evaluate(() => document.fonts.ready);
    if (pending.size) throw new Error('Browser still has pending requests after fixture readiness');
    sealed = true;
    result.url = page.url();
    if (!/^https?:/.test(result.url) || new URL(result.url).pathname !== config.case.route)
      throw new Error('Actual browser route differs from the capture case');
    result.viewport = await page.evaluate(() => ({width: innerWidth, height: innerHeight, device_scale_factor: devicePixelRatio}));
    if (Object.keys(viewport).some(key => viewport[key] !== result.viewport[key]))
      throw new Error('Actual browser viewport differs from the capture case');
    const dom = await page.content();
    const png = await page.screenshot({type: 'png', fullPage: false, animations: 'disabled'});
    if (await page.content() !== dom) throw new Error('Rendered DOM changed during capture');
    result.dom_sha256 = hash(dom);
    result.screenshot_sha256 = hash(png);
    // All successful responses, including document, CSS, JS, fonts and data, must be declared.
    for (const observed of await Promise.all(responses)) {
      if (observed.error) throw new Error(observed.error);
      const url = new URL(observed.url);
      const relative = url.origin === new URL(result.url).origin ? url.pathname + url.search : null;
      const asset = config.assets.find(item => item.url === observed.url || item.url === relative);
      if (!asset) throw new Error(`Undeclared served asset: ${observed.url}`);
      if (!(observed.status >= 200 && observed.status < 300) ||
          observed.sha256 !== hash(fs.readFileSync(path.resolve(asset.path))))
        throw new Error(`Served asset differs from local source/build output: ${asset.url}`);
      result.responses.push({...observed, asset: asset.url});
    }
    if (config.assets.some(asset => !result.responses.some(row => row.asset === asset.url)))
      throw new Error('A declared asset was not loaded by the browser');
    fs.writeFileSync(path.join(config.output, 'candidate.png'), png, {flag: 'wx'});
  } catch (error) {
    result.errors.push(String(error.message || error));
  } finally {
    try {
      if (typeof fixture.teardown !== 'function') throw new Error('Capture teardown is missing');
      await fixture.teardown({page, context});
      result.teardown = true;
    } catch (error) { result.errors.push(`teardown: ${error.message || error}`); }
    try { if (context) await context.close(); } catch (error) { result.errors.push(String(error)); }
    try { if (browser) await browser.close(); } catch (error) { result.errors.push(String(error)); }
  }
  if (!result.errors.length && result.setup && result.teardown && result.screenshot_sha256) result.status = 'CAPTURED';
  fs.writeFileSync(path.join(config.output, 'browser.json'), JSON.stringify(result, null, 2) + '\n', {flag: 'wx'});
  process.exitCode = result.status === 'CAPTURED' ? 0 : 2;
}
main().catch(error => { process.stderr.write(String(error.stack || error) + '\n'); process.exitCode = 2; });
