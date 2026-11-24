"""Pure base-suite definition closure analyzer (#587, #3468).

Operates strictly on in-memory tree entries, a blob reader callback, and
candidate changes — never spawns git or runs tests directly.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from pathlib import Path, PurePosixPath

try:
    from . import autocode_shell_syntax as shell_syntax
    from . import autocode_suite_definition_inputs as definition_inputs
    from . import autocode_suite_definition_policy as definition_policy
    from . import autocode_suite_definition_scan as definition_scan
except ImportError:
    import autocode_shell_syntax as shell_syntax
    import autocode_suite_definition_inputs as definition_inputs
    import autocode_suite_definition_policy as definition_policy
    import autocode_suite_definition_scan as definition_scan


def package_scripts(text) -> dict:
    try:
        package = json.loads(text or "{}")
    except ValueError:
        return {}
    scripts = package.get("scripts")
    return dict(scripts) if isinstance(scripts, dict) else {}


def suite_package_script(command):
    """The package.json script name an npm/yarn/pnpm suite command runs, if any."""
    if not isinstance(command, str) or shell_syntax.outside_quotes(command, ";&|<>`$\n\r"):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or PurePosixPath(words[0]).name not in ("npm", "npm.cmd", "yarn", "yarn.cmd", "pnpm", "pnpm.cmd"):
        return None
    positional = [word for word in words[1:] if not word.startswith("-")]
    if not positional:
        return None
    if positional[0] in ("run", "run-script"):
        return positional[1] if len(positional) > 1 else None
    return "test" if positional[0] in ("test", "t") else None


def analyze(
    workspace,
    entries,
    read,
    patched,
    suite_command,
    *,
    changes=None,
    is_test,
    read_paths=definition_scan._read_paths,
):
    """Analyze the transitive base-suite definition closure from in-memory entries."""
    definition_file = definition_scan._definition_file
    runner_configs = definition_scan._RUNNER_CONFIG_BASENAMES
    import_sites = definition_scan._import_sites
    js_scan = definition_scan._js_scan
    shell_script = definition_scan._shell_script
    exec_command_literals = definition_scan._exec_command_literals

    tracked = set(entries)
    found = definition_policy.Definition(set(), "", patched)
    manifests = {}
    for path in sorted(tracked):
        if PurePosixPath(path).name != "package.json":
            continue
        target, seen = path, set()
        while entries.get(target, (None,))[0] == "120000":
            if target in seen:
                raise ValueError(f"cyclic package manifest at {path}")
            seen.add(target)
            target = posixpath.normpath(posixpath.join(posixpath.dirname(target), read(target)))
        if target in entries:
            manifests[path] = read(target)
    tests = {path for path in tracked if is_test(path)}
    fixed = {path for path in tracked if definition_file(path)}
    definition = definition_inputs.linked_inputs(entries, read, fixed | tests, data=tests - fixed)

    def resolve(literal, directory):
        for root in (directory, ".") if directory != "." else (".",):
            candidate = posixpath.normpath(posixpath.join(root, literal))
            if not candidate.startswith("../") and candidate in tracked:
                return candidate
        return None

    # Product code: what base tests import transitively through relative literals.
    # The candidate's version must enter the definition tree, or a runner importing
    # the module under test would pin it (#587's product carve-out).
    product, queue, seen = set(), sorted(tests), set(tests)
    while queue:
        path = queue.pop()
        for specifier, _ in import_sites(read(path)):
            if not specifier or not specifier.startswith(("./", "../")):
                continue
            target = resolve(specifier, posixpath.dirname(path) or ".")
            if target and target not in seen:
                seen.add(target)
                product.add(target)
                queue.append(target)
    found.sourced = definition_scan.sourced_product(tests, tracked, read)

    def shell_bindings(command, directory):
        # shlex keeps an ordinary quoted filename as one operand. A shell's
        # -c operand is itself a command line, expanded in the directory
        # active where that shell appears, so `sh -c 'node run-tests.js'`
        # pins the base runner. A literal `cd <dir> &&` or `cd <dir>;`
        # moves that directory for what follows: `sh -c 'cd lib && node
        # run-tests.js'` must pin lib/run-tests.js, not a root file of the
        # same name. A computed cd target cannot establish the boundary.
        try:
            parsed = shlex.split(command)
        except ValueError:
            return [], "a shell command could not be parsed, so the suite definition boundary is unestablished"
        bindings, segment, current = [], [], directory

        def finish(keep_directory):
            nonlocal current
            if segment[:1] == ["cd"] and keep_directory:
                target = segment[1] if len(segment) == 2 else ""
                if (
                    len(segment) != 2
                    or not target
                    or target.startswith(("-", "/", "~"))
                    or any(mark in target for mark in ("$", "`", "*", "?"))
                ):
                    return "a cd target is computed, so the suite definition boundary is unestablished"
                landed = posixpath.normpath(posixpath.join(current, target))
                inside = landed in ("", ".") or (
                    not landed.startswith("../")
                    and any(path == landed or path.startswith(landed + "/") for path in tracked)
                )
                if not inside:
                    return f"cd {target} does not name a directory in the base tree"
                current = "." if landed in ("", ".") else landed
            else:
                bindings.extend((word, current) for word in segment)
            segment.clear()
            return ""

        index = 0
        while index < len(parsed):
            word = parsed[index]
            if (
                posixpath.basename(word) in {"sh", "bash", "dash", "zsh"}
                and index + 2 < len(parsed)
                and parsed[index + 1] == "-c"
            ):
                if segment:
                    reason = finish(False)
                    if reason:
                        return [], reason
                nested, reason = shell_bindings(parsed[index + 2], current)
                if reason:
                    return [], reason
                bindings.extend(nested)
                index += 3
                continue
            if word in {"&&", ";"}:
                reason = finish(True)
                if reason:
                    return [], reason
                bindings.append((word, current))
                index += 1
                continue
            if word in {"||", "|", "&"}:
                reason = finish(False)
                if reason:
                    return [], reason
                bindings.append((word, current))
                index += 1
                continue
            segment.append(word)
            index += 1
        reason = finish(False)
        if reason:
            return [], reason
        return bindings, ""

    cwd_of = {}
    pinned, queue, queued, selected_scripts = set(), [], set(), set()
    loading_readers = set()
    package_names = set()
    for text in manifests.values():
        try:
            value = json.loads(text)
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            package_names.add(value["name"])

    def loading_reference(text):
        # The same fields may route a runner's selector as well as product
        # code. Such a dual use cannot safely take the candidate's mapping.
        for specifier, _ in import_sites(text):
            if (
                specifier is None
                or specifier in {"module", "node:module"}
                or (
                    specifier.startswith("#")
                    or any(specifier == name or specifier.startswith(name + "/") for name in package_names)
                )
            ):
                return True
        # Aliased loaders and resolution-only calls are not ordinary import
        # sites. Their target can still depend on a candidate loading map.
        mask, _ = js_scan(text)
        return bool(re.search(r"\brequire\b(?!\s*\()|\bimport\s*\.\s*meta\s*\.\s*resolve\s*\(", mask))

    for path in definition | product:
        if path in entries and (path in tests or path in product or PurePosixPath(path).name in runner_configs):
            text = read(path)
            if loading_reference(text) or "package.json" in text:
                loading_readers.add(path)

    def consider(candidate, directory):
        """Queue a tracked file the suite command reaches. Tests are scanned only when they
        are shell scripts; product code and manifests are never queued from here."""
        if (
            not candidate
            or candidate.startswith("../")
            or candidate not in tracked
            or candidate in product
            or definition_file(candidate)
            or candidate in queued
        ):
            return
        cwd_of.setdefault(candidate, directory)
        queued.add(candidate)
        if not is_test(candidate):
            pinned.add(candidate)
        queue.append(candidate)

    def follow_shell(command, directory):
        if any("npm_package_" + field in command for field in definition_policy.LOADING_FIELDS):
            loading_readers.add("package script environment")
        bindings, reason = shell_bindings(command, directory)
        if reason:
            return reason
        for word, effective in bindings:
            # Inline JavaScript (node -e/--eval) is a runner input too. shlex
            # has removed its enclosing shell quotes from this operand.
            if loading_reference(word):
                loading_readers.add("suite command")
            consider(posixpath.normpath(posixpath.join(effective, word)), effective)
        try:
            roots = list(definition_policy.script_roots(bindings, manifests))
        except ValueError as error:
            return str(error)
        for manifest, name, script in roots:
            key = (manifest, name)
            if key in selected_scripts:
                continue
            selected_scripts.add(key)
            reason = follow_shell(script, posixpath.dirname(manifest) or ".")
            if reason:
                return f"{manifest} script {name}: {reason}"
        return ""

    unestablished = follow_shell(suite_command or "", ".")
    scanned = set()
    fallback_scanned = False
    while not unestablished:
        if not queue:
            if selected_scripts or fallback_scanned or not manifests:
                break
            # An opaque task runner (for example make) can invoke package
            # scripts without a literal manager call. Keep the conservative
            # roots there rather than silently dropping its runner inputs.
            fallback_scanned = True
            for manifest, text in manifests.items():
                for name, script in package_scripts(text).items():
                    selected_scripts.add((manifest, name))
                    unestablished = follow_shell(str(script), posixpath.dirname(manifest) or ".")
                    if unestablished:
                        break
                if unestablished:
                    break
            if unestablished or not queue:
                break
        path = queue.pop()
        if path in scanned:
            continue
        scanned.add(path)
        text = read(path)
        if loading_reference(text) or "package.json" in text:
            loading_readers.add(path)
        file_dir = posixpath.dirname(path) or "."
        cwd = cwd_of.get(path, ".")
        if shell_script(path, text):
            # A test-directory script is pinned wholesale and would otherwise not be read,
            # which hides `sh test/run.sh` -> `node list.js` (#662).
            unestablished = follow_shell(text, cwd)
            continue
        if is_test(path):
            continue  # JS tests are pinned wholesale, never scanned
        for specifier, inside_exec in import_sites(text):
            if specifier is None:
                unestablished = f"{path} selects its suite inputs through a computed path"
                break
            if not specifier.startswith(("./", "../")):
                continue  # a package or core module, resolved by the runtime
            target = resolve(specifier, file_dir)
            if not target or target in product:
                continue
            if is_test(target) or definition_file(target):
                continue
            if inside_exec:
                consider(target, file_dir)
            else:
                unestablished = (
                    f"{path} reaches {target}, which is neither a test, a manifest "
                    "nor an input of the executed test command"
                )
                break
        if unestablished:
            break
        for kind, specifier in read_paths(text):
            # ``fs`` paths are cwd-relative; ``path.join(__dirname, ...)`` is file-relative.
            directory = cwd if kind == "cwd" else file_dir
            target = resolve(specifier, directory)
            if target and target not in product:
                consider(target, directory)
        for command in exec_command_literals(text):
            unestablished = follow_shell(command, cwd)
            if unestablished:
                break
    found.paths = definition_inputs.linked_inputs(entries, read, definition | pinned, data=tests - fixed)
    found.boundary = unestablished
    if changes is not None:
        measured = definition_inputs.reviewed_changes(workspace, changes, patched)
        # An incomplete runner closure need not refuse a change confined to
        # known product code and retained test policy. Everything whose role
        # is unknown stays byte-for-byte on base in that case. A changed
        # unknown selector still makes preservation unproven (T15).
        unclassified = {
            path
            for path in measured
            if path not in found.paths and path not in product and not definition_file(path) and not is_test(path)
        }
        if unestablished and not unclassified:
            found.boundary = ""
        found.changed = any(path in found.paths or definition_file(path) or is_test(path) for path in measured)
        for path, status in measured.items():
            if (
                PurePosixPath(path).name != "package.json"
                or is_test(path)
                or status == "deleted"
                or (Path(workspace) / path).is_symlink()
                or (path in entries and entries[path][0] != "100644" and entries[path][0] != "100755")
            ):
                continue
            original = manifests.get(path)
            merged = definition_policy.merged_manifest(original, (Path(workspace) / path).read_text())
            if merged is not None and merged != original:
                found.manifests[path] = merged
        if found.manifests and loading_readers:
            found.boundary = "candidate package loading fields may also select runner inputs in " + ", ".join(
                sorted(loading_readers)
            )
    return found
