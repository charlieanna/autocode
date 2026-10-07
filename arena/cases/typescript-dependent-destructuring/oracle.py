"""Independent compiler behavior checks; build only an evaluator-owned copy."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
DEPS = Path(os.environ.get('ARENA_TYPESCRIPT_DEPS', ROOT / 'deps')).resolve()
NODE = os.environ.get('ARENA_NODE') or shutil.which('node')
NAMES = (
    'const_object', 'destructured_parameter', 'renamed_bindings',
    'generic_payload', 'switch_exhaustiveness', 'boolean_discriminant',
    'optional_payload', 'nested_payload', 'numeric_discriminant',
    'reassigned_payload', 'mutable_local', 'separate_bindings',
    'rest_binding', 'wrong_branch', 'ordinary_object_narrowing',
)

JS = r'''
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const workspace = process.argv[2];
const context = {require, process, Buffer, console, setTimeout, clearTimeout, module: {exports: {}}, __filename: path.join(workspace, 'built/local/compiler.js'), __dirname: path.join(workspace, 'built/local')};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(workspace, 'built/local/shims.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(workspace, 'built/local/compiler.js'), 'utf8'), context);
const ts = context.ts;
const prelude = `
interface Array<T> { readonly length: number; [index: number]: T; }
interface String { toUpperCase(): string; }
interface Number { toFixed(): string; }
interface Boolean {}
interface Function {}
interface CallableFunction {}
interface NewableFunction {}
interface IArguments { length: number; [index: number]: any; }
interface Object {}
interface RegExp {}
type EventValue = { tag: 'count', item: number } | { tag: 'label', item: string };
`;
const probes = [
['const_object', `function check(event: EventValue) { const {tag, item} = event; if (tag === 'count') { const n: number = item; item.toFixed(); } else { const s: string = item; item.toUpperCase(); } }`, []],
['destructured_parameter', `function check({tag, item}: EventValue) { if (tag === 'count') { const n: number = item; } else { const s: string = item; } }`, []],
['renamed_bindings', `function check(event: EventValue) { const {tag: flavor, item: contents} = event; if (flavor !== 'label') { const n: number = contents; } else { const s: string = contents; } }`, []],
['generic_payload', `type Wrapped<T> = { tag: 'single'; item: T } | { tag: 'many'; item: T[] }; function check<T>(input: Wrapped<T>) { const {tag, item} = input; if (tag === 'single') { const x: T = item; } else { const xs: T[] = item; } }`, []],
['switch_exhaustiveness', `function check(event: EventValue) { const {tag, item} = event; switch (tag) { case 'count': { const n: number = item; break; } case 'label': { const s: string = item; break; } default: { const impossible: never = item; } } }`, []],
['boolean_discriminant', `type Step = { done: false; item: number } | { done: true; item: undefined }; function check(step: Step) { const {done, item} = step; if (!done) { const n: number = item; } else { const u: undefined = item; } }`, []],
['optional_payload', `type MaybeEvent = {tag: 'count'; item: number | undefined} | {tag: 'label'; item: string | undefined}; function check(event: MaybeEvent) { const {tag, item} = event; if (item !== undefined) { if (tag === 'count') { const n: number = item; } else { const s: string = item; } } }`, []],
['nested_payload', `type Command = {tag: 'add'; item: {amount: number}} | {tag: 'remove'; item: {id: string}}; function check({tag, item}: Command) { switch(tag) { case 'add': return item.amount; case 'remove': return item.id; } }`, []],
['numeric_discriminant', `type Packet = {code: 10; data: number} | {code: 20; data: string} | {code: 30; data: null}; function check(packet: Packet) { const {code, data} = packet; if (code === 10) { const n: number = data; } else if (code === 20) { const s: string = data; } else { const empty: null = data; } }`, []],
['reassigned_payload', `function check({tag, item}: EventValue) { item = 'changed'; if (tag === 'count') { item.toFixed(); } }`, [2339]],
['mutable_local', `function check(event: EventValue) { let {tag, item} = event; if (tag === 'count') { item.toFixed(); } }`, [2339]],
['separate_bindings', `function check(event: EventValue) { const {tag} = event; const {item} = event; if (tag === 'count') { item.toFixed(); } }`, [2339]],
['rest_binding', `function check(event: EventValue) { const {tag, ...rest} = event; if (tag === 'count') { rest.item.toFixed(); } }`, [2339]],
['wrong_branch', `function check(event: EventValue) { const {tag, item} = event; if (tag === 'count') { item.toUpperCase(); } }`, [2339]],
['ordinary_object_narrowing', `function check(event: EventValue) { if (event.tag === 'count') { const n: number = event.item; } else { const s: string = event.item; } }`, []],
];
function diagnose(source) {
  const name = '/arena/probe.ts';
  const options = {strict: true, noLib: true, noEmit: true, target: ts.ScriptTarget.ES2015};
  const sf = ts.createSourceFile(name, prelude + source, options.target, true);
  const host = {
    getSourceFile: file => file === name ? sf : undefined,
    getDefaultLibFileName: () => '', writeFile: () => {},
    getCurrentDirectory: () => '/arena', getDirectories: () => [],
    fileExists: file => file === name, readFile: file => file === name ? prelude + source : undefined,
    getCanonicalFileName: file => file, useCaseSensitiveFileNames: () => true,
    getNewLine: () => '\n',
  };
  const program = ts.createProgram([name], options, host);
  return [...program.getGlobalDiagnostics(), ...program.getSyntacticDiagnostics(), ...program.getSemanticDiagnostics()].map(d => ({code: d.code, message: ts.flattenDiagnosticMessageText(d.messageText, '\n')}));
}
const checks = probes.map(([name, source, expected]) => {
  try {
    const diagnostics = diagnose(source);
    const actual = diagnostics.map(d => d.code).sort((a,b) => a-b);
    return {name, ok: JSON.stringify(actual) === JSON.stringify(expected), diagnostics};
  } catch (error) { return {name, ok: false, error: String(error.stack || error)}; }
});
console.log(JSON.stringify({checks}));
'''


def main():
    candidate = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='arena-typescript-copy-') as temporary:
        copy = Path(temporary)
        shutil.copytree(candidate / 'src', copy / 'src')
        (copy / 'node_modules').symlink_to(DEPS / 'node_modules', target_is_directory=True)
        generator = copy / 'processDiagnosticMessages.js'
        transpile = subprocess.run(
            [NODE, '-e', "const fs=require('fs');const ts=require(process.argv[1]);fs.writeFileSync(process.argv[3],ts.transpileModule(fs.readFileSync(process.argv[2],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2015}}).outputText)",
             str(DEPS / 'node_modules/typescript'), str(candidate / 'scripts/processDiagnosticMessages.ts'), str(generator)],
            text=True, capture_output=True, timeout=15,
        )
        if transpile.returncode:
            raise RuntimeError(transpile.stderr)
        generated = subprocess.run([NODE, str(generator), str(copy / 'src/compiler/diagnosticMessages.json')],
                                   text=True, capture_output=True, timeout=15)
        if generated.returncode:
            raise RuntimeError(generated.stderr)
        build = subprocess.run(
            [NODE, str(DEPS / 'node_modules/typescript/lib/tsc.js'), '-b', str(copy / 'src/compiler'), '--pretty', 'false'],
            text=True, capture_output=True, timeout=90,
        )
        if build.returncode:
            raise RuntimeError(build.stdout[-4000:] + build.stderr[-1000:])
        runner = copy / 'run-probes.js'
        runner.write_text(JS)
        result = subprocess.run([NODE, str(runner), str(copy)], text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr)
        print(result.stdout.strip())


if __name__ == '__main__':
    main()
