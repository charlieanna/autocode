"""Bounded, model-free execution evidence for the live product audit only."""
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from . import autocode_process as processes
except ImportError:
    import autocode_process as processes


def capture(command, *, cwd, env=None, timeout=60, receipt=None, scope='host'):
    """Retain a write-once receipt even on timeout, launch or supervision failure."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    record = dict(command=list(command), cwd=str(Path(cwd).resolve()), scope=scope,
                  returncode=None, stdout='', stderr='', timed_out=False, interrupted=False, error=None)
    # Reserve before dispatch, so a reused receipt never overwrites earlier work.
    destination = None
    started = time.monotonic()
    with processes.interruption_handler():
        child = tree = None
        try:
            destination = Path(receipt).open('x') if receipt else None  # noqa: SIM115 - handle outlives this block; closed via `with destination:` at the end
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                try:
                    # Defer, rather than block, signals across Popen's ownership
                    # handoff. Children must not inherit a blocked signal mask.
                    pending = []
                    handlers = {sig: signal.signal(sig, lambda number, frame: pending.append(number))
                                for sig in (signal.SIGINT, signal.SIGTERM)}
                    try:
                        child = subprocess.Popen(command, cwd=cwd, env=env, stdout=out,
                                                 stderr=err, start_new_session=True)
                        tree = processes.ProcessTree(child.pid, lambda rows: None)
                        tree.sample(initial=True)
                    finally:
                        for sig, handler in handlers.items():
                            signal.signal(sig, handler)
                        if pending:
                            raise KeyboardInterrupt
                    record['returncode'], record['timed_out'] = processes.wait_for_stage(
                        child, timeout, lambda rows: None)
                except BaseException as error:
                    record['error'] = f'{type(error).__name__}: {error}'
                    record['interrupted'] = isinstance(error, (KeyboardInterrupt, SystemExit))
                    # wait_for_stage normally cleans up itself. This also covers
                    # cancellation after launch but before its try/finally starts.
                    handlers = {sig: signal.signal(sig, signal.SIG_IGN)
                                for sig in (signal.SIGINT, signal.SIGTERM)}
                    try:
                        if child is not None:
                            (tree or processes.ProcessTree(child.pid, lambda rows: None)).stop(child)
                            record['returncode'] = child.poll()
                    finally:
                        for sig, handler in handlers.items():
                            signal.signal(sig, handler)
                    if not isinstance(error, (OSError, processes.ProcessError, subprocess.TimeoutExpired)):
                        raise
                finally:
                    out.seek(0); err.seek(0)
                    record['stdout'] = out.read().decode('utf-8', errors='replace')
                    record['stderr'] = err.read().decode('utf-8', errors='replace')
        except BaseException as error:
            record['error'] = record['error'] or f'{type(error).__name__}: {error}'
            record['interrupted'] = record['interrupted'] or isinstance(error, (KeyboardInterrupt, SystemExit))
            raise
        finally:
            record['elapsed_seconds'] = time.monotonic() - started
            if destination:
                with destination:
                    json.dump(record, destination, indent=2)
    return record


def successful(record):
    return (record['returncode'] == 0 and not record['timed_out']
            and not record.get('interrupted') and not record['error'])


def candidate_identity(project, files=('go.mod', 'main.go', 'reference.cs')):
    return {name: hashlib.sha256((Path(project) / name).read_bytes()).hexdigest()
            for name in files}


def go_probe(project, timeout=60):
    """No expected values here: report direct executions, never shell summaries."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    identity = candidate_identity(project)
    deadline = time.monotonic() + timeout
    rows = []
    for tld in ('de', 'com', 'org'):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        row = capture(['go', 'run', '.', tld], cwd=project, timeout=remaining,
                      env=dict(os.environ, GOTOOLCHAIN='local', GOPROXY='off'), scope='probe')
        rows.append(row)
        # Emit each completed case immediately, preserving it if a later case stalls.
        print(json.dumps(dict(candidate=identity, execution=row)), flush=True)
        if not successful(row):
            break
    if candidate_identity(project) != identity:
        raise RuntimeError('Candidate changed during canonical probe')
    return len(rows) == 3 and all(successful(row) for row in rows)


BROWSER_PROBE = '''import base64,json,sys
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, chromium_sandbox=True)
    try:
        page = browser.new_page(viewport={"width":375,"height":812}, device_scale_factor=1)
        page.goto(sys.argv[1], wait_until="load")
        observed = page.evaluate("""() => ({
            viewport: {width: innerWidth, height: innerHeight, scrollX, scrollY},
            elements: Object.fromEntries([...document.querySelectorAll('[id]')].map(e => {
                const style = getComputedStyle(e);
                return [e.id, {text: e.textContent, rect: e.getBoundingClientRect().toJSON(),
                    style: {overflow: style.overflow, display: style.display,
                            visibility: style.visibility, opacity: style.opacity}}];
            }))
        })""")
        observed["url"] = page.url
        observed["screenshot_base64"] = base64.b64encode(page.screenshot()).decode("ascii")
    finally:
        browser.close()
print(json.dumps(observed))
'''


def browser_probe(project, timeout=60):
    """Observe a fixed candidate entry point; the caller owns its acceptance oracle."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    project = Path(project).resolve()
    before = candidate_identity(project, files=('index.html',))
    row = capture([sys.executable, '-c', BROWSER_PROBE, (project / 'index.html').as_uri()],
                  cwd=project, timeout=timeout, scope='probe')
    print(json.dumps(dict(candidate=before, execution=row)), flush=True)
    if candidate_identity(project, files=('index.html',)) != before:
        raise RuntimeError('Candidate changed during the browser probe')
    return successful(row)


def preflight(kind, root, env, timeout=30, *, canonical=False):
    """Host-only prerequisite, not a claim about the reviewer's sandbox."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    root = Path(root)
    if kind == 'browser':
        deadline = time.monotonic() + timeout
        attempts = [('python-playwright', [sys.executable, '-c', '''from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, chromium_sandbox=True)
    page = browser.new_page(viewport={"width":375,"height":812})
    page.set_content('<p id="probe">rendered</p>')
    assert page.locator('#probe').is_visible()
    assert page.locator('#probe').bounding_box()['height'] > 0
    assert page.screenshot()
    browser.close()
print('rendered-browser-ready')
'''])]
        for package in ('playwright', 'puppeteer'):
            script = '''const assert = require('node:assert/strict');
const library = require(require.resolve(process.argv[1], {paths:[process.cwd(), process.argv[2]]}));
(async () => {
  const browser = process.argv[1] === 'playwright'
    ? await library.chromium.launch({headless:true, chromiumSandbox:true})
    : await library.launch({headless:true});
  try {
    const page = await browser.newPage();
    if (process.argv[1] === 'playwright') await page.setViewportSize({width:375,height:812});
    else await page.setViewport({width:375,height:812});
    await page.setContent('<p id="probe">rendered</p>');
    assert(await page.$eval('#probe', element => {
      const box = element.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && getComputedStyle(element).visibility === 'visible';
    }));
    assert((await page.screenshot()).length > 0);
  } finally { await browser.close(); }
  console.log('rendered-browser-ready');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
            attempts.append(('node-' + package, ['node', '-e', script, package,
                                                str(Path(__file__).resolve().parents[1])]))
        browsers = [shutil.which(name, path=env.get('PATH')) for name in
                    ('chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable')]
        browsers += [str(path) for path in (
            Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
            Path('/Applications/Chromium.app/Contents/MacOS/Chromium')) if path.is_file()]
        browsers = list(dict.fromkeys(path for path in browsers if path)) or ['chromium']
        html = root / 'preflight-browser.html'
        with html.open('x') as handle:
            handle.write('''<!doctype html><p id="probe">rendered</p><script>
const box = document.querySelector('#probe').getBoundingClientRect();
if (box.width > 0 && box.height > 0) document.body.dataset.rendered = 'browser-ready';
</script>''')
        for index, browser in enumerate(browsers):
            attempts.append((f'chromium-{index}', [browser, '--headless', '--no-first-run',
                '--no-default-browser-check', '--window-size=375,812',
                f'--user-data-dir={root / ("browser-profile-" + str(index))}',
                f'--screenshot={root / ("browser-" + str(index) + ".png")}', '--dump-dom', html.as_uri()]))
        if canonical:
            # Another usable browser is not proof that this interpreter can run
            # the trusted Python Playwright driver required by the live oracle.
            attempts = attempts[:1]
        receipts = []
        row = dict(returncode=None, timed_out=True, interrupted=False, error='Browser preflight budget exhausted',
                   stdout='', stderr='', scope='host-only')
        # Redistribute unused time to remaining backends without extending the
        # shared deadline or letting a stuck primary consume every fallback.
        for index, (name, command) in enumerate(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            receipt = root / f'preflight-browser-{name}.json'
            row = capture(command, cwd=root, env=env, timeout=remaining / (len(attempts) - index),
                          receipt=receipt, scope='host-only')
            receipts.append(str(receipt))
            if successful(row):
                rendered = 'rendered-browser-ready' in row['stdout'].splitlines()
                if name.startswith('chromium-'):
                    screenshot = root / ('browser-' + name.removeprefix('chromium-') + '.png')
                    try:
                        with screenshot.open('rb') as image:
                            header = image.read(24)
                        rendered = (len(header) == 24 and header[:8] == b'\x89PNG\r\n\x1a\n'
                                    and int.from_bytes(header[16:20], 'big') > 0
                                    and int.from_bytes(header[20:24], 'big') > 0
                                    and 'data-rendered="browser-ready"' in row['stdout'])
                    except OSError:
                        rendered = False
                if rendered:
                    break
                row = dict(row, error='Browser exited without rendered evidence')
        if not successful(row) and time.monotonic() >= deadline:
            row = dict(row, timed_out=True, error='Browser preflight budget exhausted')
        row = dict(row, attempt_receipts=receipts, scope='host-only')
        with (root / 'preflight-browser.json').open('x') as handle:
            json.dump(row, handle, indent=2)
        return row
    if kind != 'go':
        raise ValueError(kind)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        source = Path(directory) / 'main.go'
        source.write_text('package main\nfunc main() {}\n')
        return capture(['go', 'run', str(source)], cwd=root,
                       env=dict(env, GOTOOLCHAIN='local', GOPROXY='off'), timeout=timeout,
                       receipt=root / 'preflight-go.json', scope='host-only')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--browser', action='store_true', help='Render the candidate entry point instead of running Go')
    args = parser.parse_args()
    selected = browser_probe if args.browser else go_probe
    raise SystemExit(0 if selected(args.project, args.timeout) else 1)
