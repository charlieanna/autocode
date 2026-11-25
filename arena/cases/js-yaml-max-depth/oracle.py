"""Independent behavior checks of js-yaml's nesting limit; reads the candidate, never writes it.

Usage: python3 oracle.py WORKSPACE. Runs the evaluator's own probe with the ``node`` on PATH, loading
the candidate's CommonJS entry point by its absolute path.
"""
import json
import subprocess
import sys
from pathlib import Path

workspace = Path(sys.argv[1]).resolve()

PROBE = r"""
const yaml = require(process.argv[1]);
const checks = [];
function check(name, fn) {
  try { fn(); checks.push({ name, ok: true }); }
  catch (error) { checks.push({ name, ok: false, detail: String(error && error.stack || error).slice(0, 400) }); }
}
function refused(fn, limit) {
  try { fn(); } catch (error) {
    if (!(error instanceof yaml.YAMLException)) throw new Error('expected YAMLException, got ' + error.name);
    if (!/maxDepth/.test(error.message)) throw new Error('message does not name maxDepth: ' + error.message);
    if (limit !== undefined && !error.message.includes(String(limit))) throw new Error('message does not give the limit ' + limit);
    return;
  }
  throw new Error('loaded without an error');
}
const flow = (n) => '['.repeat(n) + 'x' + ']'.repeat(n);
const mapping = (n) => '{a: '.repeat(n) + 'x' + '}'.repeat(n);
const block = (n) => { let text = ''; for (let i = 0; i < n; i++) text += ' '.repeat(2 * i) + 'k:\n'; return text + ' '.repeat(2 * n) + 'v\n'; };
const depth = (value) => { let n = 0; while (value !== null && typeof value === 'object') { value = Array.isArray(value) ? value[0] : value.a !== undefined ? value.a : value.k; n++; } return n; };

check('deep_flow_sequence_refused', () => refused(() => yaml.load('['.repeat(100000)), 100));
check('deep_flow_mapping_refused', () => refused(() => yaml.load('{a: '.repeat(100000)), 100));
check('deep_block_mapping_refused', () => refused(() => yaml.load(block(150))));
check('moderate_nesting_loads', () => {
  if (depth(yaml.load(flow(40))) !== 40) throw new Error('flow nesting of 40 changed');
  if (depth(yaml.load(mapping(40))) !== 40) throw new Error('mapping nesting of 40 changed');
  if (depth(yaml.load(block(40))) !== 40) throw new Error('block nesting of 40 changed');
});
check('custom_lower_limit', () => {
  refused(() => yaml.load(flow(30), { maxDepth: 10 }), 10);
  if (depth(yaml.load(flow(3), { maxDepth: 10 })) !== 3) throw new Error('nesting of 3 refused under maxDepth 10');
});
check('custom_higher_limit', () => {
  if (depth(yaml.load(flow(300), { maxDepth: 1000 })) !== 300) throw new Error('nesting of 300 refused under maxDepth 1000');
});
check('load_all_limited', () => refused(() => yaml.loadAll('---\n' + flow(500) + '\n---\na: 1\n')));
check('limit_resets_between_documents', () => {
  const documents = yaml.loadAll(Array.from({ length: 5 }, () => '---\n' + flow(60) + '\n').join(''));
  if (documents.length !== 5 || documents.some((doc) => depth(doc) !== 60)) throw new Error('separate documents share one depth');
});
check('ordinary_documents_unchanged', () => {
  const doc = yaml.load('a: [1, 2, {b: c}]\nd:\n  - e\n  - f: g\n');
  if (JSON.stringify(doc) !== '{"a":[1,2,{"b":"c"}],"d":["e",{"f":"g"}]}') throw new Error(JSON.stringify(doc));
});
process.stdout.write(JSON.stringify({ checks }));
"""

entry = workspace / "index.js"
done = subprocess.run(
    ["node", "-e", PROBE, str(entry)],
    cwd=workspace,
    capture_output=True,
    text=True,
    timeout=120,
    stdin=subprocess.DEVNULL,
)
try:
    result = json.loads(done.stdout)
except ValueError:
    names = ["deep_flow_sequence_refused", "deep_flow_mapping_refused", "deep_block_mapping_refused",
             "moderate_nesting_loads", "custom_lower_limit", "custom_higher_limit", "load_all_limited",
             "limit_resets_between_documents", "ordinary_documents_unchanged"]  # fmt: skip
    detail = (done.stderr or done.stdout)[-400:]
    result = {"checks": [{"name": name, "ok": False, "detail": detail} for name in names]}
print(json.dumps(result))
