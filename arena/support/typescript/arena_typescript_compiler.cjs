'use strict';

// Compile current candidate source freshly; never import candidate built/ or a fixed compiler.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

const source = process.cwd();
const deps = process.env.ARENA_TYPESCRIPT_DEPS;
if (!deps) throw new Error('ARENA_TYPESCRIPT_DEPS must identify the frozen external build bundle');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'arena-typescript-native-'));
process.on('exit', () => fs.rmSync(temporary, {recursive: true, force: true}));
fs.cpSync(path.join(source, 'src'), path.join(temporary, 'src'), {recursive: true});
fs.symlinkSync(path.join(deps, 'node_modules'), path.join(temporary, 'node_modules'), 'dir');
const bootstrap = require(path.join(deps, 'node_modules/typescript'));
const generator = path.join(temporary, 'processDiagnosticMessages.js');
fs.writeFileSync(generator, bootstrap.transpileModule(
  fs.readFileSync(path.join(source, 'scripts/processDiagnosticMessages.ts'), 'utf8'),
  {compilerOptions: {module: bootstrap.ModuleKind.CommonJS, target: bootstrap.ScriptTarget.ES2015}},
).outputText);
execFileSync(process.execPath, [generator, path.join(temporary, 'src/compiler/diagnosticMessages.json')], {cwd: temporary, timeout: 15000});
execFileSync(process.execPath, [path.join(deps, 'node_modules/typescript/lib/tsc.js'), '-b', path.join(temporary, 'src/compiler'), '--pretty', 'false'], {cwd: temporary, timeout: 90000});
const context = {require, process, Buffer, console, setTimeout, clearTimeout, module: {exports: {}},
  __filename: path.join(temporary, 'built/local/compiler.js'), __dirname: path.join(temporary, 'built/local')};
vm.createContext(context);
for (const artifact of ['shims.js', 'compiler.js']) {
  vm.runInContext(fs.readFileSync(path.join(temporary, 'built/local', artifact), 'utf8'), context);
}
const ts = context.ts;
const library = `
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
`;

function diagnose(sourceText) {
  const name = '/arena/native-probe.ts';
  const source = library + sourceText;
  const options = {strict: true, noLib: true, noEmit: true, target: ts.ScriptTarget.ES2015};
  const sf = ts.createSourceFile(name, source, options.target, true);
  const host = {
    getSourceFile: file => file === name ? sf : undefined,
    getDefaultLibFileName: () => '', writeFile: () => {},
    getCurrentDirectory: () => '/arena', getDirectories: () => [],
    fileExists: file => file === name, readFile: file => file === name ? source : undefined,
    getCanonicalFileName: file => file, useCaseSensitiveFileNames: () => true,
    getNewLine: () => '\n',
  };
  const program = ts.createProgram([name], options, host);
  return [...program.getGlobalDiagnostics(), ...program.getSyntacticDiagnostics(), ...program.getSemanticDiagnostics()]
    .map(d => ({code: d.code, message: ts.flattenDiagnosticMessageText(d.messageText, '\n')}));
}

module.exports = {diagnose};
