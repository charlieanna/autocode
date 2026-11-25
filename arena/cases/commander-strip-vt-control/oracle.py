"""Independent checks of a behavior-preserving refactor of Commander's colour stripping.

Usage: python3 oracle.py WORKSPACE. Runs the evaluator's own ES module probe with the ``node`` on PATH,
importing the candidate's modules by absolute file URL; never writes the workspace.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

workspace = Path(sys.argv[1]).resolve()
NAMES = ["display_width_ignores_colour", "default_output_strips_colour", "coloured_help_layout_unchanged",
         "plain_help_unchanged", "own_stripper_removed", "uses_node_util"]  # fmt: skip

PROBE = r"""
const [indexUrl, helpUrl] = process.argv.slice(1);
const { Command, Help } = await import(indexUrl);
const helpModule = await import(helpUrl);
const checks = [];
const check = (name, fn) => {
  try { fn(); checks.push({ name, ok: true }); }
  catch (error) { checks.push({ name, ok: false, detail: String(error && error.stack || error).slice(0, 400) }); }
};
const equal = (actual, expected) => { if (actual !== expected) throw new Error(JSON.stringify(actual) + ' !== ' + JSON.stringify(expected)); };
const CSI = '\u001b[';
const red = (s) => `${CSI}31m${s}${CSI}39m`;
const bold = (s) => `${CSI}1m${s}${CSI}22m`;

function program(colour) {
  const paint = colour ? red : (s) => s;
  const cmd = new Command('tool')
    .description('A tool with ' + paint('coloured') + ' words')
    .option('-v, --verbose', 'say ' + paint('more'))
    .option('--size <n>', paint('size') + ' of things', '3')
    .argument('<file>', 'the ' + paint('file'));
  cmd.command('serve').description(paint('start') + ' the server');
  cmd.configureHelp({ helpWidth: 60 });
  cmd.configureOutput({ getOutHasColors: () => true, getErrHasColors: () => true });
  return cmd;
}

check('display_width_ignores_colour', () => {
  const help = new Help();
  equal(help.displayWidth(red('abc')), 3);
  equal(help.displayWidth(bold(red('wide')) + ' x'), 6);
  equal(help.displayWidth(`${CSI}4;31m` + 'u' + `${CSI}0m`), 1);
  equal(help.displayWidth('plain'), 5);
});
check('default_output_strips_colour', () => {
  const config = new Command().configureOutput();
  equal(config.stripColor(bold('x') + red('y')), 'xy');
  equal(config.stripColor('none'), 'none');
});
check('coloured_help_layout_unchanged', () => {
  const plain = program(false).helpInformation();
  const coloured = program(true).helpInformation();
  equal(coloured.replace(/\u001b\[[\d;]*m/g, ''), plain);
});
check('plain_help_unchanged', () => equal(program(false).helpInformation(), process.env.ORACLE_EXPECTED_HELP));
check('own_stripper_removed', () => {
  if ('stripColor' in helpModule) throw new Error('lib/help.js still exports its own stripColor');
});
process.stdout.write(JSON.stringify({ checks }));
"""

EXPECTED_HELP = (
    "Usage: tool [options] [command] <file>\n\n"
    "A tool with coloured words\n\n"
    "Arguments:\n"
    "  file            the file\n\n"
    "Options:\n"
    "  -v, --verbose   say more\n"
    "  --size <n>      size of things (default: \"3\")\n"
    "  -h, --help      display help for command\n\n"
    "Commands:\n"
    "  serve           start the server\n"
    "  help [command]  display help for command\n"
)


def source_checks():
    library = workspace / "lib"
    sources = {path.name: path.read_text() for path in sorted(library.glob("*.js"))}
    own = [name for name, text in sources.items() if re.search(r"(\\x1b|\\u001b|\\e)\\\[", text)]
    uses = any("stripVTControlCharacters" in text and "node:util" in text for text in sources.values())
    return [
        {"name": "uses_node_util", "ok": uses, **({} if uses else {"detail": "lib/ does not use node:util's stripVTControlCharacters"})},
        {"name": "own_stripper_removed", "ok": not own, **({"detail": "an own escape-sequence pattern remains in " + ", ".join(own)} if own else {})},
    ]  # fmt: skip


done = subprocess.run(
    ["node", "--input-type=module", "-e", PROBE, (workspace / "index.js").as_uri(), (workspace / "lib/help.js").as_uri()],
    cwd=workspace,
    capture_output=True,
    text=True,
    timeout=120,
    stdin=subprocess.DEVNULL,
    env={"PATH": __import__("os").environ.get("PATH", ""), "ORACLE_EXPECTED_HELP": EXPECTED_HELP},
)
try:
    probed = json.loads(done.stdout)["checks"]
except (ValueError, KeyError):
    detail = (done.stderr or done.stdout)[-400:]
    probed = [{"name": name, "ok": False, "detail": detail} for name in NAMES if name not in ("uses_node_util",)]
merged = {}
for item in [*probed, *source_checks()]:
    # Both the module and the source must show the own stripper gone.
    if item["name"] in merged:
        merged[item["name"]] = merged[item["name"]] if not merged[item["name"]]["ok"] else item
    else:
        merged[item["name"]] = item
print(json.dumps({"checks": [merged[name] for name in NAMES]}))
