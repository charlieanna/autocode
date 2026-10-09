/* Synthetic acquisition/behavior fixture. No independent image acceptance. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {createHash} = require('node:crypto');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
let server;
const served = new Map([
  ['/visual-controls', ['app.html', 'text/html; charset=utf-8']],
  ['/app.css', ['app.css', 'text/css; charset=utf-8']],
  ['/variant.css', ['variant.css', 'text/css; charset=utf-8']],
  ['/app.js', ['app.js', 'text/javascript; charset=utf-8']],
  ['/fonts/inter.woff2', ['assets/fonts/Inter-Regular.woff2', 'font/woff2']],
  ['/fonts/mono.woff2', ['assets/fonts/JetBrainsMono-Regular.woff2', 'font/woff2']],
  ['/assets/status.svg', ['assets/status.svg', 'image/svg+xml']],
]);

async function loadPinnedResources(page) {
  const result = await page.evaluate(async () => {
    const sample = 'Build the release safely. Tests are ready for review.';
    const families = ['FixtureInter', 'FixtureMono'];
    const fonts = [];
    for (const family of families) {
      const faces = await document.fonts.load(`400 24px ${family}`, sample);
      fonts.push({family, check: document.fonts.check(`400 24px ${family}`, sample),
        faces: faces.map(face => ({family: face.family.replace(/^['"]|['"]$/g, ''),
          style: face.style, weight: face.weight, status: face.status}))});
    }
    await document.fonts.ready;
    const image = document.getElementById('status-asset');
    await image.decode();
    return {fonts, image: {complete: image.complete, width: image.naturalWidth, height: image.naturalHeight}};
  });
  for (const row of result.fonts) {
    assert.equal(row.check, true, `Font unavailable: ${row.family}`);
    assert.ok(row.faces.length > 0, `No actual loaded face: ${row.family}`);
    assert.ok(row.faces.some(face => face.family === row.family && face.style === 'normal'
      && face.weight === '400' && face.status === 'loaded'), `Wrong loaded face: ${row.family}`);
  }
  assert.deepEqual(result.image, {complete: true, width: 6, height: 6});
}

async function assertProtectedRunBehavior(page) {
  assert.equal(await page.locator('#count').textContent(), '0');
  await page.locator('#run').click();
  assert.equal(await page.locator('#count').textContent(), '1', 'Run mouse action');
  await page.evaluate(() => { document.getElementById('count').textContent = '0'; document.body.focus(); });
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'run', 'First protected Tab target');
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#count').textContent(), '1', 'Run Enter action');
  await page.evaluate(() => { document.getElementById('count').textContent = '0'; document.body.focus(); });
  await page.mouse.move(0, 0);
  assert.equal(await page.locator('#count').textContent(), '0');
}

exports.setup = async ({page, context}) => {
  const identity = JSON.parse(fs.readFileSync('fixture-identity.json', 'utf8'));
  assert.equal(identity.bound, true, 'Unbound proposal is not executable');
  assert.ok(/^[a-f0-9]{64}$/.test(identity.browser_executable_sha256));
  assert.equal(hash(fs.readFileSync(identity.browser_executable_path)), identity.browser_executable_sha256);
  assert.equal(context.browser().version(), identity.browser_version);
  for (const [name, expected] of Object.entries(identity.resource_pins))
    assert.equal(hash(fs.readFileSync(path.resolve(name))), expected, `Resource input changed: ${name}`);
  server = http.createServer((req, res) => {
    const item = served.get(req.url);
    if (!item) { res.writeHead(404); res.end('Undeclared route'); return; }
    res.writeHead(200, {'content-type': item[1], 'cache-control': 'no-store'});
    res.end(fs.readFileSync(path.resolve(item[0])));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  await context.route('**/*', route => route.request().url().startsWith(origin + '/')
    ? route.continue() : route.abort());
  await page.emulateMedia({reducedMotion: 'reduce', colorScheme: 'light'});
  await page.goto(origin + '/visual-controls', {waitUntil: 'load'});
  assert.equal(new URL(page.url()).pathname, '/visual-controls');
  assert.deepEqual(await page.evaluate(() => ({width: innerWidth, height: innerHeight, dpr: devicePixelRatio})),
    {width: 1440, height: 900, dpr: 1});
  await loadPinnedResources(page);
  await assertProtectedRunBehavior(page);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.evaluate(() => {
    document.body.dataset.protectedRun = 'mouse-tab-enter-pass';
    document.body.dataset.state = 'ready';
  });
};
exports.teardown = async () => {
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); server = undefined; }
};
