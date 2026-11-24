"""Observe runner source resolution without executing the project's tests."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shlex
import site
import stat
import subprocess
import sys
import sysconfig
from pathlib import Path

try:
    from . import autocode_python_tests as python_tests
except ImportError:
    import autocode_python_tests as python_tests

MAX_FILES = 1000
MAX_SCAN_BYTES = 8 * 1024 * 1024
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
NODE_FLAG_OPTIONS = {
    "--test",
    "--test-only",
    "--test-force-exit",
    "--no-warnings",
    "--no-deprecation",
    "--enable-source-maps",
    "--experimental-strip-types",
    "--no-strip-types",
    "--experimental-transform-types",
    "--experimental-test-coverage",
}
NODE_VALUE_OPTIONS = {
    "--require",
    "-r",
    "--import",
    "--conditions",
    "-C",
    "--loader",
    "--experimental-loader",
    "--test-concurrency",
    "--test-name-pattern",
    "--test-skip-pattern",
    "--test-timeout",
    "--test-isolation",
    "--experimental-test-isolation",
    "--test-shard",
    "--test-global-setup",
    "--test-coverage-branches",
    "--test-coverage-functions",
    "--test-coverage-lines",
    "--test-coverage-exclude",
    "--test-coverage-include",
    "--unhandled-rejections",
}
GO_VALUE_OPTIONS = {
    "-run",
    "-bench",
    "-fuzz",
    "-count",
    "-timeout",
    "-p",
    "-parallel",
    "-cpu",
    "-shuffle",
    "-mod",
    "-modfile",
    "-tags",
    "-covermode",
    "-coverpkg",
    "-exec",
    "-toolexec",
    "-overlay",
    "-pgo",
    "-gcflags",
    "-asmflags",
    "-ldflags",
}
PYTHON_VALUE_OPTIONS = {
    "-p",
    "-o",
    "-c",
    "-k",
    "-m",
    "--override-ini",
    "--rootdir",
    "--confcutdir",
    "--import-mode",
    "--tb",
    "--assert",
    "--maxfail",
    "--ignore",
    "--ignore-glob",
    "--deselect",
    "--color",
    "--capture",
    "--junitxml",
    "--xml",
    "--rootpath",
    "--start-directory",
    "--pattern",
    "-t",
    "--top-level-directory",
    "--durations",
}
IMPORT_PATTERN = re.compile(r"(?:\brequire\s*\(\s*|\bfrom\s+|\bimport\s*\(\s*|\bimport\s+)['\"]([^'\"]+)['\"]")


def _write(path, result):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(result, sort_keys=True).encode()
    if len(data) > MAX_CAPTURE_BYTES:
        data = json.dumps({"observations": [], "errors": ["source-resolution capture is too large"]}).encode()
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _run(command, cwd):
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, "", str(error)
    return completed.returncode, completed.stdout, completed.stderr


def _tail(text):
    return text[-2000:]


def _selectors(words, value_options, *, stop=()):
    selected, index = [], 0
    while index < len(words):
        word = words[index]
        if word in stop:
            break
        if word in value_options:
            index += 2
            continue
        if word.startswith("-"):
            index += 1
            continue
        selected.append(word)
        index += 1
    return selected


def _go(request):
    words = shlex.split(request["command"])
    offset = 1 if words and words[0] in ("env", "/usr/bin/env", "/bin/env") else 0
    while offset < len(words) and re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", words[offset]):
        offset += 1
    if len(words) < offset + 2 or Path(words[offset]).name not in ("go", "go.exe") or words[offset + 1] != "test":
        return [], ["the Go command is not a literal test invocation"]
    prefix, executable = words[:offset], words[offset]
    arguments = words[offset + 2 :]
    if os.environ.get("GOFLAGS", "").strip() or any(
        word == "GOFLAGS" or word.startswith("GOFLAGS=") for word in prefix
    ):
        return [], ["GOFLAGS source resolution is not observed"]
    if any(
        word in ("-overlay", "-exec", "-toolexec") or word.startswith(("-overlay=", "-exec=", "-toolexec="))
        for word in arguments
    ):
        return [], ["Go wrapper or overlay source resolution is not observed"]
    root = Path(request["root"])
    selectors = _selectors(arguments, GO_VALUE_OPTIONS, stop=("-args", "--")) or ["."]
    observations, errors = [{"kind": "go_root", "path": str(root.resolve()), "role": "root"}], []
    code, stdout, stderr = _run(
        [*prefix, executable, "env", "GOMOD", "GOROOT", "GOMODCACHE", "GOWORK", "GOFLAGS"], root
    )
    values = stdout.splitlines() if code == 0 else []
    module = values[0].strip() if values else ""
    goroot = Path(values[1]).resolve() if len(values) > 1 and values[1] else None
    modcache = Path(values[2]).resolve() if len(values) > 2 and values[2] else None
    gowork = values[3].strip() if len(values) > 3 else ""
    configured_flags = values[4].strip() if len(values) > 4 else ""
    if code != 0:
        errors.append("go env source roots failed: " + _tail(stderr or stdout))
    else:
        if configured_flags:
            errors.append("configured GOFLAGS source resolution is not observed")
        if module and module != "/dev/null":
            observations.append({"kind": "go_module", "path": module, "role": "root"})
        if gowork:
            observations.append({"kind": "go_module", "path": gowork, "role": "root"})
    build_flags = []
    index = 0
    while index < len(arguments):
        word = arguments[index]
        if word in ("-mod", "-modfile", "-tags", "-coverpkg"):
            if index + 1 >= len(arguments):
                errors.append(f"Go flag {word} has no value")
            else:
                build_flags.extend(arguments[index : index + 2])
                if word == "-modfile":
                    observations.append(
                        {"kind": "go_module", "path": str((root / arguments[index + 1]).resolve()), "role": "root"}
                    )
                index += 1
        elif word.startswith(("-mod=", "-modfile=", "-tags=", "-coverpkg=")):
            build_flags.append(word)
            if word.startswith("-modfile="):
                observations.append(
                    {"kind": "go_module", "path": str((root / word.split("=", 1)[1]).resolve()), "role": "root"}
                )
        index += 1
    code, stdout, stderr = _run(
        [*prefix, executable, "list", "-deps", "-f", "{{.Dir}}", *build_flags, *selectors], root
    )
    if code == 0:
        directories = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not directories:
            errors.append("go list returned no package directory")
    elif module == "/dev/null":
        directories = [str(root)]
    else:
        errors.append("go list could not establish package directories: " + _tail(stderr or stdout))
        directories = []
    sources = []
    for value in directories:
        path = Path(value).resolve()
        dependency = any(root and path.is_relative_to(root) for root in (goroot, modcache))
        kind = "go_dependency" if dependency else "go_package"
        observations.append({"kind": kind, "path": str(path), "role": "dependency" if dependency else "source"})
        if not dependency:
            sources.append(path)
    scanned, scanned_bytes = 0, 0
    for directory in sources:
        for source in sorted(Path(directory).glob("*.go")):
            if scanned >= MAX_FILES or scanned_bytes >= MAX_SCAN_BYTES:
                errors.append("Go source observation exceeded its file bound")
                return observations, errors
            scanned += 1
            try:
                size = source.stat().st_size
                if size > MAX_CAPTURE_BYTES or scanned_bytes + size > MAX_SCAN_BYTES:
                    errors.append(f"Go test source exceeds the observation bound: {source}")
                    continue
                scanned_bytes += size
                text = source.read_text()
                if request.get("workspace") and request["workspace"] in text:
                    errors.append(f"Go source names the live workspace directly: {source}")
                if re.search(r"\bplugin\.Open\s*\(", text):
                    errors.append(f"Go dynamic plugin source resolution is not observed: {source}")
            except (OSError, UnicodeError):
                errors.append(f"Go test source could not be observed: {source}")
    return observations, errors


def _node_files(root, files):
    suffixes = (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts")
    patterns = (
        "**/*.test.*",
        "**/*-test.*",
        "**/*_test.*",
        "**/test-*.*",
        "**/test.*",
        "test/**/*.*",
        "**/test/**/*.*",
    )
    candidates = []
    for value in files:
        path = root / value
        if "node_modules" in path.parts or "node_modules" in path.resolve().parts:
            raise ValueError("explicit Node test selectors under node_modules are not observed")
        candidates.extend(root.glob(value) if any(char in value for char in "*?[") else [path])
    if not files:
        for pattern in patterns:
            candidates.extend(root.rglob(pattern))
    found = []
    for path in candidates:
        sources = path.rglob("*") if path.is_dir() else [path]
        for source in sources:
            if files and "node_modules" in source.resolve().parts:
                raise ValueError("explicit Node test selectors under node_modules are not observed")
            if "node_modules" in source.parts or source.suffix not in suffixes or not source.is_file():
                continue
            found.append(str(source.resolve()))
            if len(found) > MAX_FILES:
                raise ValueError("Node source observation exceeded its file bound")
    return list(dict.fromkeys(found))


def _node_arguments(arguments):
    selectors = []
    test = False
    index = 0
    while index < len(arguments):
        word = arguments[index]
        if word == "--":
            selectors.extend(arguments[index + 1 :])
            break
        if word.startswith("-r="):
            raise ValueError("Node short require equals syntax is not a test invocation")
        if word in NODE_VALUE_OPTIONS:
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("-"):
                raise ValueError(f"Node option {word} has no observable value")
            index += 2
            continue
        if "=" in word and word.split("=", 1)[0] in NODE_VALUE_OPTIONS:
            index += 1
            continue
        if word in NODE_FLAG_OPTIONS:
            test |= word == "--test"
            index += 1
            continue
        if word.startswith("-"):
            raise ValueError(f"Node option is not observed: {word}")
        if not test:
            raise ValueError("the Node command executes a script rather than its test runner")
        selectors.append(word)
        index += 1
    if not test:
        raise ValueError("the Node command is not a literal test invocation")
    return selectors


def _node_preloads(root, arguments):
    values = []
    index = 0
    while index < len(arguments):
        word = arguments[index]
        if word == "--":
            break
        if word in ("--require", "-r"):
            index += 1
            if index >= len(arguments):
                raise ValueError("Node preload has no path")
            values.append(arguments[index])
        elif word.startswith("--require="):
            values.append(word.split("=", 1)[1])
        index += 1
    files = []
    for value in values:
        path = (root / value).resolve()
        if not path.is_relative_to(root.resolve()) or path.suffix != ".cjs":
            raise ValueError("Node preload source resolution is not observed")
        try:
            if path.is_symlink() or path.stat().st_size > MAX_CAPTURE_BYTES:
                raise ValueError("Node preload is not a bounded regular file")
            text = path.read_text()
        except OSError as error:
            raise ValueError(f"Node preload could not be observed: {error}") from error
        remaining = text.lstrip()
        decoder = json.JSONDecoder()
        while remaining:
            match = re.match(r"(?:globalThis|global)\.([A-Za-z_$][\w$]*)\s*=\s*", remaining)
            if not match or match.group(1) in {
                "process",
                "global",
                "globalThis",
                "require",
                "module",
                "exports",
                "__proto__",
                "constructor",
                "prototype",
            }:
                raise ValueError("Node preload is not a deterministic global assignment")
            _value, end = decoder.raw_decode(remaining, match.end())
            remaining = remaining[end:].lstrip()
            if not remaining:
                break
            if not remaining.startswith(";"):
                raise ValueError("Node preload is not a deterministic global assignment")
            remaining = remaining[1:].lstrip()
            if not remaining:
                break
            if remaining.startswith(";"):
                raise ValueError("Node preload contains an empty statement")
        if not text.strip():
            raise ValueError("Node preload is empty")
        files.append(str(path))
    return files


def _node(request):
    words = shlex.split(request["command"])
    offset = 1 if words and words[0] in ("env", "/usr/bin/env", "/bin/env") else 0
    while offset < len(words) and re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", words[offset]):
        offset += 1
    if (
        len(words) < offset + 2
        or Path(words[offset]).name not in ("node", "node.exe")
        or "--test" not in words[offset + 1 :]
    ):
        return [], ["the Node command is not a literal test invocation"]
    prefix, executable = words[:offset], words[offset]
    arguments = words[offset + 1 :]
    if os.environ.get("NODE_OPTIONS") or any(
        word == "NODE_OPTIONS" or word.startswith("NODE_OPTIONS=") for word in prefix
    ):
        return [], ["NODE_OPTIONS source resolution is not observed"]
    if any(word == "--conditions" or word.startswith("--conditions=") or word == "-C" for word in arguments):
        return [], ["Node conditional source resolution is not observed"]
    if any(
        word in ("--import", "--loader", "--experimental-loader", "--test-global-setup")
        or word.startswith(("--import=", "--loader=", "--experimental-loader=", "--test-global-setup="))
        or word == "--test-reporter"
        or word.startswith("--test-reporter=")
        for word in arguments
    ):
        return [], ["Node preload, loader or global-setup source resolution is not observed"]
    root = Path(request["root"])
    try:
        selectors = _node_arguments(arguments)
        preloads = _node_preloads(root, arguments)
    except ValueError as error:
        return [], [str(error)]
    package_path = root / "package.json"
    package = {}
    if package_path.is_symlink():
        return [], ["package.json is a link"]
    if package_path.is_file():
        try:
            if package_path.stat().st_size > MAX_CAPTURE_BYTES:
                raise ValueError("package.json exceeds the observation bound")
            data = package_path.read_bytes()
            if request.get("package_sha256") and hashlib.sha256(data).hexdigest() != request["package_sha256"]:
                return [], ["package.json changed after the test command was classified"]
            package = json.loads(data)
            if not isinstance(package, dict):
                return [], ["package.json is not an object"]
        except (OSError, ValueError) as error:
            return [], [f"package.json could not be read: {error}"]
    if request.get("npm_test") and not package_path.is_file():
        return [], ["the observed npm package disappeared before source resolution"]
    script = r"""
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';
const request = JSON.parse(process.argv[1]);
const root = path.resolve(request.root);
const observations = [{kind: 'node_root', path: root, role: 'root'}];
const errors = [];
const pattern = /(\brequire\s*\(\s*|\bfrom\s+|\bimport\s*\(\s*|\bimport\s+)['"]([^'"]+)['"]/g;
if (request.package_root) observations.push({kind: 'node_package', path: root, role: 'root'});
if (request.name && request.exports) {
  try {
    const resolved = createRequire(path.join(root, 'package.json')).resolve(request.name);
    observations.push({kind: 'node_self', path: resolved, role: 'source'});
  } catch (error) { observations.push({kind: 'node_self', name: request.name, path: null, role: 'source'}); }
}
let observedBytes = 0;
const queue = [...request.files, ...request.preloads];
const seen = new Set();
for (let index = 0; index < queue.length; index++) {
  const file = queue[index];
  if (seen.has(file)) continue;
  if (seen.size >= 1000) { errors.push('Node source observation exceeded its file bound'); break; }
  seen.add(file);
  let text;
  try {
    const size = fs.statSync(file).size;
    if (size > 2097152 || observedBytes + size > 8388608) throw new Error('test source exceeds the observation bound');
    observedBytes += size;
    text = fs.readFileSync(file, 'utf8');
  }
  catch (error) { errors.push(`test file could not be read: ${file}: ${error.message}`); continue; }
  const isTest = request.files.includes(file);
  observations.push({kind: isTest ? 'node_test' : 'node_source', path: file, role: isTest ? 'test' : 'source'});
  if (request.workspace && text.includes(request.workspace)) {
    errors.push(`${isTest ? 'test' : 'source'} file names the live workspace directly: ${file}`);
  }
  if (/\b(?:child_process|execSync|spawnSync|execFileSync)\b/.test(text)) {
    errors.push(`Node child-process source resolution is not observed: ${file}`);
  }
  const literals = [...text.matchAll(pattern)];
  const withoutLiterals = text.replace(pattern, '');
  if (/\brequire\b/.test(withoutLiterals) || /\bimport\s*(?:\?\.)?\s*\(/.test(withoutLiterals) || /\bimport\s*\.\s*meta\s*\.\s*resolve\b/.test(text) || /\b(?:eval|Function|vm|SourceTextModule|SyntheticModule|register|registerHooks|createRequire|_load|_resolveFilename|_extensions|dlopen|runInThisContext|runInNewContext|compileFunction)\b/.test(text) || /\bmodule\s*\.\s*paths\b/.test(text) || /(?:\.\s*NODE_PATH\b|\[\s*['"]NODE_PATH['"]\s*\])/.test(text)) {
    errors.push(`Node dynamic source resolution is not observed: ${file}`);
  }
  for (const match of literals) {
    const form = match[1];
    const specifier = match[2];
    if (specifier.startsWith('node:')) continue;
    try {
      const resolved = form.startsWith('require')
        ? createRequire(file).resolve(specifier)
        : fileURLToPath(import.meta.resolve(specifier, pathToFileURL(file).href));
      const dependency = resolved.includes(`${path.sep}node_modules${path.sep}`);
      observations.push({kind: 'node_import', name: specifier, path: resolved,
        role: dependency ? 'dependency' : 'source'});
      if (!dependency && resolved.startsWith(root + path.sep) && !seen.has(resolved)) queue.push(resolved);
    } catch (error) {
      if (error.code === 'MODULE_NOT_FOUND' || error.code === 'ERR_MODULE_NOT_FOUND') {
        observations.push({kind: 'node_import', name: specifier, path: null, role: 'source'});
      } else { errors.push(`import resolution failed: ${file}: ${specifier}: ${error.message}`); }
    }
  }
}
process.stdout.write(JSON.stringify({observations, errors}));
"""
    payload = json.dumps(
        {
            "root": str(root),
            "workspace": request.get("workspace"),
            "package_root": package_path.is_file(),
            "name": package.get("name"),
            "exports": package.get("exports"),
            "files": _node_files(root, selectors),
            "preloads": preloads,
            "npm_test": request.get("npm_test") is True,
        }
    )
    code, stdout, stderr = _run([*prefix, executable, "--input-type=module", "-e", script, payload], root)
    if code != 0:
        return [], ["Node source-resolution probe failed: " + _tail(stderr or stdout)]
    try:
        result = json.loads(stdout)
    except ValueError:
        return [], ["Node source-resolution probe returned invalid JSON"]
    return result.get("observations") or [], result.get("errors") or []


def _module_name(path, root):
    relative = path.relative_to(root)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _python_targets(words, kind, root, *, script=False):
    values = [] if script else _selectors(words, PYTHON_VALUE_OPTIONS, stop=("--",))
    if kind == "unittest" and values[:1] == ["discover"]:
        values = []
    targets = []
    for value in values:
        path = (root / value).resolve()
        if path.suffix == ".py" and path.is_file():
            targets.append((_module_name(path, root), path))
        elif re.match(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$", value):
            targets.append((value, None))
    if targets:
        return targets
    files = sorted(path.resolve() for path in root.rglob("*.py") if python_tests.TEST_MODULE.match(path.name))
    if len(files) > MAX_FILES:
        raise ValueError("Python test observation exceeded its file bound")
    return [(_module_name(path, root), path) for path in files]


def _python_file_root(node, filename):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("str", "Path"):
        if len(node.args) != 1 or node.keywords:
            raise ValueError(f"unsupported {node.func.id}() in an import path")
        return _python_file_root(node.args[0], filename)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "resolve"
        and not node.args
        and not node.keywords
    ):
        return _python_file_root(node.func.value, filename)
    if isinstance(node, ast.Name) and node.id == "__file__":
        return Path(filename)
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "parents"
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, int)
        and node.slice.value >= 0
    ):
        return _python_file_root(node.value.value, filename).parents[node.slice.value]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        right = _python_file_root(node.right, filename)
        if right.is_absolute():
            raise ValueError("absolute path division is not an observed import root")
        return _python_file_root(node.left, filename) / right
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return Path(node.value)
    raise ValueError("unsupported expression in an import path")


def _python_path_mutation(statement, filename):
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return None
    node = statement.value
    owner = node.func.value if isinstance(node.func, ast.Attribute) else None
    if not (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in ("insert", "append")
        and isinstance(owner, ast.Attribute)
        and owner.attr == "path"
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "sys"
    ):
        return None
    if node.func.attr == "insert" and (
        len(node.args) != 2
        or not isinstance(node.args[0], ast.Constant)
        or not isinstance(node.args[0].value, int)
        or node.args[0].value < 0
    ):
        raise ValueError("unsupported sys.path mutation")
    if node.func.attr == "append" and len(node.args) != 1:
        raise ValueError("unsupported sys.path append")
    if node.keywords:
        raise ValueError("unsupported sys.path keyword")
    return (
        node.func.attr,
        node.args[0].value if node.func.attr == "insert" else 0,
        _python_file_root(node.args[-1], filename).resolve(),
    )


def _python_import_paths(parsed, filename):
    mutations = []
    recognized = set()
    prelude = True
    for statement in parsed.body:
        mutation = _python_path_mutation(statement, filename)
        if mutation is not None:
            if not prelude:
                raise ValueError("sys.path mutation after project execution is not observed")
            mutations.append(mutation)
            recognized.add(id(statement.value.func.value))
            continue
        if (
            prelude
            and isinstance(statement, ast.Import)
            and len(statement.names) == 1
            and statement.names[0].name == "sys"
            and not statement.names[0].asname
        ):
            continue
        if (
            prelude
            and isinstance(statement, ast.ImportFrom)
            and (
                (
                    statement.module == "pathlib"
                    and statement.level == 0
                    and len(statement.names) == 1
                    and statement.names[0].name == "Path"
                    and not statement.names[0].asname
                )
                or (statement.module == "__future__" and statement.level == 0)
            )
        ):
            continue
        if (
            prelude
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            continue
        prelude = False
    sys_aliases = {"sys"}
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            sys_aliases.update(alias.asname or alias.name for alias in node.names if alias.name == "sys")
            if any(alias.name.partition(".")[0] in {"builtins", "importlib"} for alias in node.names):
                raise ValueError("Python dynamic source resolution or import machinery mutation is not observed")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.partition(".")[0] in {"builtins", "importlib"}:
                raise ValueError("Python dynamic source resolution or import machinery mutation is not observed")
            if node.module == "sys" and any(alias.name in {"path", "modules"} for alias in node.names):
                raise ValueError("Python dynamic source resolution or import machinery mutation is not observed")
    for node in ast.walk(parsed):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in sys_aliases:
            sys_aliases.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.value, ast.Name)
            and node.value.id in sys_aliases
            and isinstance(node.target, ast.Name)
        ):
            sys_aliases.add(node.target.id)

    def rooted_name(node):
        while isinstance(node, ast.Attribute):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    computed_aliases = {"getattr", "setattr", "delattr", "vars"}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(parsed):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in computed_aliases:
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in computed_aliases:
                        computed_aliases.add(target.id)
                        changed = True

    dynamic = {
        "__import__",
        "import_module",
        "run_module",
        "run_path",
        "resolve_name",
        "find_spec",
        "module_from_spec",
        "spec_from_file_location",
        "addsitedir",
    }
    for node in ast.walk(parsed):
        if isinstance(node, ast.Name) and node.id in {"__import__", "__builtins__"}:
            raise ValueError("Python dynamic source resolution or import machinery mutation is not observed")
        if isinstance(node, ast.Attribute):
            if node.attr in dynamic or node.attr in {"meta_path", "path_hooks", "modules", "__dict__"}:
                raise ValueError("Python dynamic source resolution or import machinery mutation is not observed")
            if node.attr == "path" and rooted_name(node.value) in sys_aliases and id(node) not in recognized:
                raise ValueError("unsupported sys.path access")
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in computed_aliases
                and any(rooted_name(argument) in sys_aliases for argument in node.args)
            ):
                raise ValueError("computed sys.path mutation is not observed")
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {
                    "insert",
                    "append",
                    "extend",
                    "remove",
                    "pop",
                    "clear",
                }
                and rooted_name(node.func.value) in sys_aliases
                and id(node.func.value) not in recognized
            ):
                raise ValueError("unsupported aliased sys.path mutation")
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and rooted_name(node.value) in sys_aliases
        ):
            raise ValueError("Python dynamic source resolution or import machinery mutation is not observed")
    if mutations:
        protected = {"sys", "Path", "str", "__file__"}
        for node in ast.walk(parsed):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in protected:
                raise ValueError(f"import bootstrap name is reassigned: {node.id}")
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in sys_aliases:
                raise ValueError("the sys module is aliased during import bootstrap")
            if isinstance(node, ast.arg) and node.arg in protected:
                raise ValueError(f"import bootstrap name is shadowed: {node.arg}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in protected:
                raise ValueError(f"import bootstrap name is shadowed: {node.name}")
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if (alias.asname or alias.name) in protected and alias.name != "Path":
                        raise ValueError(f"import bootstrap name is shadowed: {alias.asname or alias.name}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if (alias.asname or alias.name) in protected and alias.name != "sys":
                        raise ValueError(f"import bootstrap name is shadowed: {alias.asname or alias.name}")
    return mutations


def _dependency_roots():
    values = set()
    for key in ("purelib", "platlib", "stdlib"):
        value = sysconfig.get_paths().get(key)
        if value:
            values.add(Path(value).resolve())
    with contextlib.suppress(AttributeError):
        values.update(Path(value).resolve() for value in site.getsitepackages())
    return values


def _origins(spec):
    if spec is None:
        return []
    if spec.origin not in (None, "built-in", "frozen"):
        return [spec.origin]
    return list(spec.submodule_search_locations or [])


def _pytest_plugins(arguments):
    found = []
    index = 0
    while index < len(arguments):
        word = arguments[index]
        value = None
        if word == "-p" and index + 1 < len(arguments):
            index += 1
            value = arguments[index]
        elif word.startswith("-p") and not word.startswith("--"):
            value = word[2:]
        if value and not value.startswith("no:"):
            found.append(value)
        index += 1
    return found


def _pytest_setup(arguments, root):
    try:
        from _pytest.config.findpaths import determine_setup
    except ImportError as error:
        return None, None, [f"pytest rootdir could not be observed: {error}"]
    inifile, overrides, rootdir = None, [], None
    index = 0
    while index < len(arguments):
        word = arguments[index]
        if word in ("-c", "--inifile"):
            index += 1
            if index < len(arguments):
                inifile = arguments[index]
        elif word.startswith(("--inifile=", "-c=")):
            inifile = word.split("=", 1)[1]
        elif word in ("-o", "--override-ini"):
            index += 1
            if index < len(arguments):
                overrides.append(arguments[index])
        elif word.startswith(("--override-ini=", "-o=")):
            overrides.append(word.split("=", 1)[1])
        elif word in ("--rootdir", "--rootpath"):
            index += 1
            if index < len(arguments):
                rootdir = arguments[index]
        elif word.startswith(("--rootdir=", "--rootpath=")):
            rootdir = word.split("=", 1)[1]
        index += 1
    try:
        found, _inifile, inicfg, _invocation_dir = determine_setup(
            inifile=inifile,
            override_ini=overrides,
            args=_selectors(arguments, PYTHON_VALUE_OPTIONS, stop=("--",)),
            rootdir_cmd_arg=rootdir,
            invocation_dir=root,
        )

        def configured(name):
            value = inicfg.get(name, []) if inicfg is not None else []
            return getattr(value, "value", value)

        return found, configured, []
    except (Exception, SystemExit) as error:
        return None, None, [f"pytest rootdir probe failed: {error}"]


def _python_environment(prefix):
    environment = dict(os.environ)
    words = prefix[1:] if prefix[:1] in (("env",), ("/usr/bin/env",), ("/bin/env",)) else prefix
    for word in words:
        name, value = word.split("=", 1)
        environment[name] = value
    return environment


def _python_startup_errors(dependencies, editable_root=None):
    errors = []
    editable_root = Path(editable_root).resolve() if editable_root else Path(python_tests.__file__).parent.resolve()
    for name in ("sitecustomize", "usercustomize"):
        if name in sys.modules:
            errors.append(f"Python {name} source resolution is not observed")
    scanned = 0
    for directory in dependencies:
        for path in sorted(directory.glob("*.pth")):
            scanned += 1
            if scanned > MAX_FILES:
                return [*errors, "Python startup path observation exceeded its file bound"]
            try:
                if path.is_symlink() or path.stat().st_size > MAX_CAPTURE_BYTES:
                    raise ValueError("not a bounded regular file")
                for line in path.read_text().splitlines():
                    statement = line.strip()
                    if not statement.startswith(("import ", "import\t")):
                        continue
                    match = re.fullmatch(r"import (__editable___[A-Za-z0-9_]+_finder); \1\.install\(\)", statement)
                    finder = sys.modules.get(match.group(1)) if match else None
                    mapping = getattr(finder, "MAPPING", None)
                    virtualenv = sys.modules.get("_virtualenv") if statement == "import _virtualenv" else None
                    if (
                        mapping == {"autocode_cli": str(editable_root)}
                        and Path(str(getattr(finder, "__file__", "") or "")).resolve().is_relative_to(path.parent)
                    ) or (
                        virtualenv is not None
                        and Path(str(getattr(virtualenv, "__file__", "") or "")).resolve().is_relative_to(path.parent)
                    ):
                        continue
                    errors.append(f"Python executable startup path is not observed: {path}")
                    break
            except (OSError, UnicodeError, ValueError) as error:
                errors.append(f"Python startup path could not be observed: {path}: {error}")
    return errors


def _python_test_files(start, excluded=(), *, namespace=False):
    files = []
    for path in sorted(start.rglob("test*.py")):
        if not python_tests.TEST_MODULE.match(path.name) or any(path.is_relative_to(root) for root in excluded):
            continue
        relative = path.relative_to(start)
        parents = [start]
        for part in relative.parts[:-1]:
            parents.append(parents[-1] / part)
        if namespace or all((parent / "__init__.py").is_file() for parent in parents[1:]):
            files.append(path.resolve())
            if len(files) > MAX_FILES:
                raise ValueError("component test observation exceeded its file bound")
    return files


def _component_parent_errors(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("not a regular file")
            data = stream.read(MAX_CAPTURE_BYTES + 1)
        if len(data) > MAX_CAPTURE_BYTES:
            raise ValueError("source exceeds the observation bound")
        parsed = ast.parse(data.decode(), filename=str(path))
    except (OSError, UnicodeError, SyntaxError, ValueError) as error:
        raise ValueError(f"component family parent could not be observed: {error}") from error
    none_targets = {
        id(target)
        for node in ast.walk(parsed)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "load_tests"
        and isinstance(node.value, ast.Constant)
        and node.value.value is None
        for target in node.targets
    }
    for node in ast.walk(parsed):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__":
            raise ValueError("component family dynamic load_tests source resolution is not observed")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "load_tests":
            raise ValueError("component family load_tests source resolution is not observed")
        if (
            isinstance(node, ast.Name)
            and node.id == "load_tests"
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and id(node) not in none_targets
        ):
            raise ValueError("component family load_tests source resolution is not observed")
        if isinstance(node, ast.Attribute) and node.attr == "load_tests" and isinstance(node.ctx, (ast.Store, ast.Del)):
            raise ValueError("component family load_tests source resolution is not observed")
        if isinstance(node, ast.alias) and (node.asname or node.name) == "load_tests":
            raise ValueError("component family load_tests source resolution is not observed")
    return []


def _component_contexts(arguments, root, tree):
    selected, families, packages, index = ".", [], [], 0
    while index < len(arguments):
        word = arguments[index]
        if word in ("-v", "--verbose", "-q", "--quiet", "-f", "--failfast"):
            index += 1
        elif word == "-k":
            if index + 1 >= len(arguments):
                raise ValueError("component test selector has no value")
            index += 2
        elif word == "--root":
            if index + 1 >= len(arguments):
                raise ValueError("component test root has no value")
            selected, index = arguments[index + 1], index + 2
        elif word in ("--components", "--repos"):
            kind = word.removeprefix("--")
            members = []
            index += 1
            while index < len(arguments) and not arguments[index].startswith("-"):
                members.append(arguments[index])
                index += 1
            families.append((kind, members))
        elif word == "--package-family":
            group = []
            index += 1
            while index < len(arguments) and not arguments[index].startswith("-"):
                group.append(arguments[index])
                index += 1
            if not group:
                raise ValueError("component package family has no folder")
            packages.append((group[0], group[1:]))
        else:
            raise ValueError(f"component collector argument is not observed: {word}")
    start = (root / selected).resolve()
    if not start.is_relative_to(tree):
        raise ValueError("component collector root resolves outside its owning copy")
    excluded = [(root / kind).resolve() for kind, _members in families]
    excluded.extend((root / folder).resolve() for folder, _members in packages)
    contexts = []
    if start.is_dir():
        paths = (start, root / "src", root)
        contexts.append((paths, _python_test_files(start, excluded), ()))
        if selected == ".":
            for name in ("tests", "test"):
                markerless = root / name
                if markerless.is_dir() and not (markerless / "__init__.py").is_file():
                    contexts.append(((markerless, root / "src", root), _python_test_files(markerless), ()))
    for kind, members in [*families, *packages]:
        family = (root / kind).resolve()
        if not family.is_relative_to(tree):
            raise ValueError("component family resolves outside its owning copy")
        parent_init = family / "__init__.py"
        if parent_init.is_file():
            _component_parent_errors(parent_init)
            contexts.append(((family, family / "src", root), (), (parent_init.resolve(),)))
        for member in members:
            directory = (family / member).resolve()
            if not directory.is_relative_to(family):
                raise ValueError("component member resolves outside its family")
            files = _python_test_files(directory, namespace=True) if directory.is_dir() else []
            sources = []
            for path in files:
                parent = path.parent
                while parent != directory and parent.is_relative_to(directory):
                    marker = parent / "__init__.py"
                    if marker.is_file():
                        sources.append(marker.resolve())
                    parent = parent.parent
            member_init = directory / "__init__.py"
            if member_init.is_file():
                sources.append(member_init.resolve())
            contexts.append(((directory, directory / "src", root), files, tuple(dict.fromkeys(sources))))
    return contexts


def _python(request):
    invocation = python_tests.parse(
        request["command"], include_owned=True, owned_directory=request.get("observer_root")
    )
    if invocation is None or invocation.kind not in ("pytest", "unittest"):
        return [], ["the Python command is not a literal pytest or unittest invocation"]
    kind, arguments = invocation.kind, list(invocation.arguments)
    environment = _python_environment(invocation.prefix)
    root = Path(request["root"])
    tree = Path(request["tree"]).resolve()
    dependencies = _dependency_roots()
    observations = [{"kind": "python_root", "path": str(root.resolve()), "role": "root"}]
    errors = _python_startup_errors(dependencies, request.get("observer_root"))
    queue, seen = [], set()
    scan_budget = [0, 0]
    observed_contexts = set()
    context_targets = {}
    context_mutations = {}
    target_paths = set()
    if kind == "pytest" and (
        environment.get("PYTEST_ADDOPTS")
        or environment.get("PYTEST_PLUGINS")
        or (
            not environment.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD")
            and any(importlib.metadata.entry_points(group="pytest11"))
        )
    ):
        errors.append("pytest environment options or plugin autoload are not observed")

    def imports(text, filename, inherited=()):
        try:
            parsed = ast.parse(text, filename=filename)
        except (SyntaxError, ValueError) as error:
            errors.append(f"Python source could not be parsed: {filename}: {error}")
            return [], []
        try:
            own_paths = _python_import_paths(parsed, filename)
            if own_paths and Path(filename).resolve() not in target_paths:
                raise ValueError("cross-module sys.path mutation is not observed")
        except (ValueError, IndexError, OSError) as error:
            errors.append(f"Python import-path mutation is not observed: {filename}: {error}")
            own_paths = []
        found = []
        active = list(inherited)
        for statement in parsed.body:
            mutation = _python_path_mutation(statement, filename)
            if mutation is not None:
                active.append(mutation)
            for node in ast.walk(statement):
                if isinstance(node, ast.Call):
                    target = node.func
                    name = (
                        target.id
                        if isinstance(target, ast.Name)
                        else target.attr
                        if isinstance(target, ast.Attribute)
                        else ""
                    )
                    if name in {"__import__", "exec", "eval"}:
                        errors.append(f"Python dynamic source resolution is not observed: {filename}: {name}")
                if isinstance(node, ast.Import):
                    found.extend((alias.name, 0, tuple(active)) for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    found.append((node.module or "", node.level, tuple(active)))
        return found, own_paths

    def observe_file(path, role, inherited=(), search_paths=()):
        path = path.resolve()
        context = (path, inherited, tuple(search_paths))
        if context in observed_contexts:
            return
        if role == "test":
            context_targets[tuple(search_paths)] = context_targets.get(tuple(search_paths), 0) + 1
        if scan_budget[1] >= MAX_FILES:
            errors.append("Python source observation exceeded its file bound")
            return
        try:
            size = path.stat().st_size
            if size > MAX_CAPTURE_BYTES or scan_budget[0] + size > MAX_SCAN_BYTES:
                errors.append(f"Python source observation exceeded its file bound: {path}")
                return
            scan_budget[0] += size
            scan_budget[1] += 1
            observed_contexts.add(context)
            text = path.read_text()
        except (OSError, UnicodeError):
            errors.append(f"Python source could not be observed: {path}")
            return
        observations.append(
            {"kind": "python_test" if role == "test" else "python_source", "path": str(path), "role": role}
        )
        if request.get("workspace") and request["workspace"] in text:
            errors.append(f"Python {role} names the live workspace directly: {path}")
        if "PYTHONPATH" in text or "addsitedir" in text or "pytest_plugins" in text:
            errors.append(f"Python import-path mutation is not observed: {path}")
        found, paths = imports(text, str(path), inherited)
        if paths:
            mutations = context_mutations.setdefault(tuple(search_paths), {})
            mutations[path] = tuple((method, index, str(directory.resolve())) for method, index, directory in paths)
        for _method, _index, directory in paths:
            observations.append({"kind": "python_root", "path": str(directory), "role": "root"})
        queue.extend((name, level, path, paths, search_paths) for name, level, paths in found)

    def resolve(name, level=0, context=None, paths=(), search_paths=()):
        if level and context is not None:
            base = Path(context).parent
            for _ in range(level - 1):
                base = base.parent
            candidate = base.joinpath(*name.split(".")) if name else base
            source = candidate if candidate.suffix == ".py" else candidate / "__init__.py"
            if source.is_file():
                path = source.resolve()
                observations.append({"kind": "python_module", "name": name, "path": str(path), "role": "source"})
                observe_file(path, "source", paths, search_paths)
            elif candidate.is_dir():
                path = candidate.resolve()
                observations.append({"kind": "python_module", "name": name, "path": str(path), "role": "source"})
                for member in sorted(path.rglob("*.py")):
                    observe_file(member, "source", paths, search_paths)
            else:
                observations.append({"kind": "python_module", "name": name, "path": None, "role": "source"})
            return
        top = name.partition(".")[0]
        key = (
            top,
            tuple((method, index, str(directory)) for method, index, directory in paths),
            tuple(str(directory) for directory in search_paths),
        )
        if not top or key in seen:
            return
        seen.add(key)
        original_path = sys.path[:]
        try:
            for method, index, directory in paths:
                if method == "insert":
                    sys.path.insert(index, str(directory))
                else:
                    sys.path.append(str(directory))
            spec = importlib.util.find_spec(top)
        except (Exception, SystemExit) as error:
            errors.append(f"Python module resolution failed for {top}: {error}")
            return
        finally:
            sys.path[:] = original_path
        origins = _origins(spec)
        if not origins:
            observations.append({"kind": "python_module", "name": top, "path": None, "role": "source"})
            return
        for value in origins:
            path = Path(value).resolve()
            dependency = any(path.is_relative_to(directory) for directory in dependencies)
            role = "dependency" if dependency else "source"
            observations.append({"kind": "python_module", "name": top, "path": str(path), "role": role})
            if role != "source" or not path.is_relative_to(tree):
                continue
            files = [path] if path.suffix == ".py" else sorted(path.rglob("*.py"))
            for source in files:
                observe_file(source, "source", paths, search_paths)

    if kind == "pytest":
        pytest_root, inicfg, setup_errors = _pytest_setup(arguments, root)
        errors.extend(setup_errors)
        configured_addopts = inicfg("addopts") if inicfg is not None else []
        if isinstance(configured_addopts, str):
            try:
                arguments = [*shlex.split(configured_addopts), *arguments]
            except ValueError as error:
                errors.append(f"pytest addopts could not be parsed: {error}")
        if plugins := _pytest_plugins(arguments):
            errors.append("pytest plugin source resolution is not observed: " + ", ".join(plugins))
        if configured_addopts:
            pytest_root, inicfg, setup_errors = _pytest_setup(arguments, root)
            errors.extend(setup_errors)
        if pytest_root is not None:
            observations.append({"kind": "pytest_rootdir", "path": str(pytest_root), "role": "root"})
            values = inicfg("pythonpath")
            if isinstance(values, str):
                values = [values]
            for value in values:
                path = (pytest_root / value).resolve()
                observations.append({"kind": "pytest_pythonpath", "path": str(path), "role": "root"})
                sys.path.insert(0, str(path))

    sys.path.insert(0, str(root))
    if invocation.script:
        contexts = _component_contexts(arguments, root, tree)
        target_paths = {path.resolve() for _paths, files, _sources in contexts for path in files}
        for paths, files, sources in contexts:
            search_paths = tuple(Path(path).resolve() for path in paths)
            inherited = tuple(("insert", index, path) for index, path in enumerate(search_paths))
            for path in search_paths:
                observations.append({"kind": "python_root", "path": str(path), "role": "root"})
            for path in sources:
                observe_file(path, "source", inherited, search_paths)
            for path in files:
                observe_file(path, "test", inherited, search_paths)
    else:
        targets = _python_targets(arguments, kind, root)
        target_paths = {path.resolve() for _name, path in targets if path is not None}
        for _name, path in targets:
            if path is not None:
                sys.path.insert(0, str(path.parent))
        for name, path in targets:
            context = path
            if path is not None:
                observe_file(path, "test")
            elif "." in name:
                candidate = root.joinpath(*name.split(".")).with_suffix(".py")
                if candidate.is_file():
                    observe_file(candidate, "test")
                    context = candidate
            resolve(name, context=str(context) if context is not None else None)
        if kind == "pytest":
            conftests = set()
            starts = [root, *(path.parent for _name, path in targets if path is not None)]
            for start in starts:
                directory = start.resolve()
                while directory.is_relative_to(tree):
                    candidate = directory / "conftest.py"
                    if candidate.is_file():
                        conftests.add(candidate.resolve())
                    if directory == tree:
                        break
                    directory = directory.parent
            for path in sorted(conftests):
                observe_file(path, "test")
    for context, count in context_targets.items():
        mutations = context_mutations.get(context, {})
        if count > 1 and mutations and (len(mutations) != count or len(set(mutations.values())) != 1):
            errors.append("cross-module Python import-path mutation is not observed")
    while queue:
        entry = queue.pop(0)
        resolve(*entry)
        if len(seen) >= MAX_FILES:
            errors.append("Python import observation exceeded its file bound")
            break
    return observations, errors


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    try:
        if request["framework"] == "go":
            observations, errors = _go(request)
        elif request["framework"] == "node":
            observations, errors = _node(request)
        else:
            observations, errors = _python(request)
    except (Exception, SystemExit) as error:
        observations, errors = [], [f"source-resolution probe failed: {type(error).__name__}: {error}"]
    _write(request["capture"], {"observations": observations, "errors": errors})


if __name__ == "__main__":
    main()
