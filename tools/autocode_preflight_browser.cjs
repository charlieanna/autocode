/* Model-free Playwright readiness. Fixtures and dependencies belong to the task. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { createHash } = require('node:crypto');

async function main() {
  const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const localRequire = createRequire(path.join(process.cwd(), 'package.json'));
  const playwright = localRequire(config.playwright_module || 'playwright');
  const fixture = localRequire(path.resolve(config.fixture));
  const result = { kind: 'prerequisite', status: 'BLOCKED', setup: false, teardown: false,
    launched: false, captured: false, ready: [], fonts: [], font_sources: [], viewport: config.viewport, canvas: config.canvas, errors: [] };
  let browser, context;
  try {
    if (typeof fixture.setup !== 'function' || typeof fixture.teardown !== 'function')
      throw new Error('The approved fixture must supply both setup and teardown');
    if (!Array.isArray(config.ready) || !config.ready.length) throw new Error('Declare deterministic readiness conditions');
    browser = await playwright.chromium.launch({ headless: true, ...(config.executable ? { executablePath: path.resolve(config.executable) } : {}) });
    result.launched = true;
    context = await browser.newContext({ viewport: { width: config.viewport.width, height: config.viewport.height }, deviceScaleFactor: config.viewport.device_scale_factor });
    const page = await context.newPage();
    await fixture.setup({ page, context });
    for (const condition of config.ready) {
      if (!condition.id || !condition.selector || !condition.attribute || typeof condition.equals !== 'string')
        throw new Error('Each readiness condition needs id, selector, attribute and exact equals value');
      await page.waitForFunction(c => document.querySelector(c.selector)?.getAttribute(c.attribute) === c.equals,
        condition, { timeout: config.readiness_timeout_ms || 10000 });
      result.ready.push(condition.id);
    }
    for (const font of config.fonts) {
      const bytes = fs.readFileSync(path.resolve(font.path));
      if (!bytes.length) throw new Error(`Empty font: ${font.path}`);
      const loaded = await page.evaluate(async ({ family, data }) => {
        const face = new FontFace(family, `url(data:font/woff2;base64,${data})`);
        await face.load(); document.fonts.add(face); await document.fonts.ready;
        return face.status === 'loaded' && document.fonts.has(face);
      }, { family: font.family, data: bytes.toString('base64') });
      if (!loaded) throw new Error(`Font cannot load: ${font.family}`);
      result.fonts.push(font.family);
      result.font_sources.push({ family: font.family, path: path.relative(process.cwd(), path.resolve(font.path)).split(path.sep).join('/'), sha256: createHash('sha256').update(bytes).digest('hex') });
    }
    if (config.canvas.alpha === 'composite') {
      if (!/^#[0-9a-f]{6}$/i.test(config.canvas.background)) throw new Error('An opaque canvas color is required');
      await page.addStyleTag({ content: `html { background: ${config.canvas.background} !important; }` });
    } else if (config.canvas.alpha !== 'preserve' || config.canvas.background !== null) {
      throw new Error('Unknown alpha policy');
    }
    const viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight, device_scale_factor: devicePixelRatio }));
    if (Object.keys(viewport).some(key => viewport[key] !== config.viewport[key])) throw new Error('Actual viewport/DPR differs from the capture contract');
    const capture = await page.screenshot({ type: 'png', fullPage: false, omitBackground: config.canvas.alpha === 'preserve' });
    if (capture.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a' ||
        capture.readUInt32BE(16) !== Math.round(viewport.width * viewport.device_scale_factor) ||
        capture.readUInt32BE(20) !== Math.round(viewport.height * viewport.device_scale_factor)) throw new Error('Capture is missing or has the wrong pixel dimensions');
    result.captured = true;
    result.setup = true;
  } catch (error) {
    result.errors.push(String(error.message || error));
  } finally {
    try {
      if (typeof fixture.teardown === 'function') await fixture.teardown({ context });
      else throw new Error('Fixture teardown is missing');
      result.teardown = true;
    } catch (error) { result.errors.push(`teardown: ${error.message || error}`); }
    try { if (context) await context.close(); } catch (error) { result.errors.push(`context close: ${error}`); }
    try { if (browser) await browser.close(); } catch (error) { result.errors.push(`browser close: ${error}`); }
  }
  if (!result.errors.length && result.setup && result.teardown) result.status = 'READY';
  process.stdout.write('AUTOCODE_PREREQUISITE=' + JSON.stringify(result) + '\n');
  process.exitCode = result.status === 'READY' ? 0 : 1;
}
main().catch(error => { process.stderr.write(`Prerequisite setup: ${error.stack || error}\n`); process.exitCode = 1; });
