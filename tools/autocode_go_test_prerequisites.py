"""Conservative Go loading metadata for identical-source regression comparisons.

Only extra indirect requirements at the original Go selection and additive
checksums qualify. This grants no plan approval or behavioral-test credit.
"""

import json
import os
import re

try:
    from . import autocode_command_receipt as command_receipt
except ImportError:
    import autocode_command_receipt as command_receipt

_REQUIRE = re.compile(r'([^\s"`]+)\s+(v[^\s"`]+)\Z')
_SUM = re.compile(r"(\S+) (v\S+?)(/go.mod)? (h1:[A-Za-z0-9+/]{43}=)\Z")


def _manifest(text):
    if not isinstance(text, str):
        raise ValueError("Go test prerequisites require an existing go.mod")
    directives, requirements, block = [], {}, False
    for line in text.splitlines():
        content, _, comment = line.partition("//")
        content = content.strip()
        if not content:
            continue
        if content == "require (":
            if block:
                raise ValueError("Nested Go requirement block")
            block = True
            continue
        if content == ")" and block:
            block = False
            continue
        if block or content.startswith("require "):
            match = _REQUIRE.fullmatch(content if block else content[len("require ") :])
            if not match or match[1] in requirements:
                raise ValueError("Invalid or duplicated Go requirement")
            requirements[match[1]] = (match[2], comment.strip() == "indirect")
        else:
            directives.append(content)
    if block or not any(line.startswith("module ") for line in directives):
        raise ValueError("Incomplete Go module declaration")
    return directives, requirements


def _sums(text):
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _SUM.fullmatch(line)
        if not match:
            raise ValueError("Invalid Go checksum")
        key = (match[1], match[2], bool(match[3]))
        if key in result:
            raise ValueError("Duplicated Go checksum")
        result[key] = match[4]
    return result


def metadata_changes(before, after, selected):
    """Return admissible changed manifest names, or reject a non-loading edit."""
    old_directives, old = _manifest(before.get("go.mod"))
    new_directives, new = _manifest(after.get("go.mod"))
    if old_directives != new_directives or any(new.get(path) != entry for path, entry in old.items()):
        raise ValueError("Go prerequisites cannot change existing directives or dependencies")
    for path in new.keys() - old.keys():
        version, indirect = new[path]
        if not indirect or selected.get(path) != version:
            raise ValueError("Go prerequisites may only record already-selected indirect dependencies")
    old_sums, new_sums = _sums(before.get("go.sum", "")), _sums(after.get("go.sum", ""))
    if any(new_sums.get(key) != value for key, value in old_sums.items()):
        raise ValueError("Go prerequisites cannot remove or rewrite checksums")
    for path, version, _ in new_sums.keys() - old_sums.keys():
        if selected.get(path) != version:
            raise ValueError("Go prerequisites cannot add a checksum for an unselected dependency")
    return sorted(name for name in ("go.mod", "go.sum") if before.get(name, "") != after.get(name, ""))


def selected_versions(run):
    """Read native selection only after Go has resolved the original graph offline.

    A missing .info may omit timestamp/origin while preserving Version. Missing
    module constraints cannot pass the separate graph command. Missing versions
    or duplicate rows still refuse, regardless of go list's -e exit status.
    """
    graph = run(["go", "mod", "graph"])
    if graph.get("exit_code") != 0 or any(len(line.split()) != 2 for line in graph.get("stdout", "").splitlines()):
        raise ValueError("Cannot resolve the original Go module graph")
    listing = run(["go", "list", "-mod=readonly", "-m", "-e", "-json", "all"])
    if listing.get("exit_code") != 0:
        raise ValueError("Cannot read the original Go module selection")
    decoder, text, selected, mains = json.JSONDecoder(), listing.get("stdout", ""), {}, 0
    while text.strip():
        row, end = decoder.raw_decode(text.lstrip())
        text = text.lstrip()[end:]
        if not isinstance(row, dict) or not row.get("Path"):
            raise ValueError("Malformed Go module selection")
        if row.get("Main") is True:
            mains += 1
            continue
        path, version = row["Path"], row.get("Version")
        if not isinstance(version, str) or not version.startswith("v") or path in selected or row.get("Replace"):
            raise ValueError("Incomplete or replaced Go module selection")
        selected[path] = version
    if mains != 1:
        raise ValueError("Go prerequisites require one original module")
    return selected


# A model cannot select another module/workspace/toolchain or enable downloads
# through ambient Go configuration while this metadata is inspected.
_OFFLINE = {
    "GOPROXY": "off",
    "GOSUMDB": "off",
    "GOVCS": "*:off",
    "GOWORK": "off",
    "GOTOOLCHAIN": "local",
    "GOENV": "off",
    "GOFLAGS": "-mod=readonly",
}


def _paths(paths):
    from pathlib import PurePosixPath

    result = sorted(set(paths))
    for name in result:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or str(path) != name
            or any(p in (".", "..", ".git", ".autocode") for p in path.parts)
            or path.name not in ("go.mod", "go.sum")
        ):
            raise ValueError("Go prerequisites require literal contained manifest paths")
    return result


def _contents(root, name):
    from pathlib import Path

    path = Path(root)
    for part in Path(name).parts:
        path /= part
        if path.is_symlink():
            raise ValueError("Go prerequisites cannot follow links")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_mode & 0o111:
        raise ValueError("Go prerequisites require plain non-executable files")
    return path.read_bytes().decode()


def from_trees(before_tree, after_tree, paths, evidence_dir, run_command):
    """Inspect both selections and retain exact admissible metadata, without edits."""
    import hashlib
    import shlex
    from pathlib import Path

    paths = _paths(paths)
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    modules = {}
    # Read/validate all path topology before launching even an offline command.
    for directory in sorted({str(Path(name).parent) for name in paths}):
        original, candidate = {}, {}
        for name in ("go.mod", "go.sum"):
            relative = str(Path(directory) / name)
            for root, target in ((before_tree, original), (after_tree, candidate)):
                value = _contents(root, relative)
                if value is not None:
                    target[name] = value
        _manifest(original.get("go.mod"))
        _manifest(candidate.get("go.mod"))
        modules[directory] = original, candidate
    result = {"files": {}, "modules": []}
    for index, (directory, (original, candidate)) in enumerate(modules.items()):
        selections, commands = [], []
        for side, root in (("original", before_tree), ("candidate", after_tree)):

            def run(argv, cwd=None):
                log = evidence / f"{index}-{side}-{len(commands)}.log"
                receipt = run_command(
                    shlex.join(argv), cwd or Path(root) / directory, log, timeout=30, env={**os.environ, **_OFFLINE}
                )
                commands.append(receipt)
                if receipt.get("timed_out") or receipt.get("interrupted") or not command_receipt.completed(receipt):
                    raise ValueError("Go prerequisite inspection did not complete")
                output = Path(receipt["output"])
                raw = output.read_bytes()
                if hashlib.sha256(raw).hexdigest() != receipt.get("output_sha256"):
                    raise ValueError("Go prerequisite command output changed")
                return {**receipt, "stdout": raw.decode()}

            selections.append(selected_versions(run))
            if side == "original":
                changed = metadata_changes(original, candidate, selections[0])
        old_sums, new_sums = _sums(original.get("go.sum", "")), _sums(candidate.get("go.sum", ""))
        additions = new_sums.keys() - old_sums.keys()
        if additions:
            neutral = evidence / f"{index}-checksum-inspection"
            neutral.mkdir(exist_ok=True)
            for path, version in sorted({(p, v) for p, v, _ in additions}):
                fetched = run(["go", "mod", "download", "-json", path + "@" + version], cwd=neutral)
                value = json.loads(fetched["stdout"])
                if (
                    fetched.get("exit_code") != 0
                    or value.get("Error")
                    or value.get("Path") != path
                    or value.get("Version") != version
                ):
                    raise ValueError("Cannot verify the selected dependency checksums offline")
                for key in [key for key in additions if key[:2] == (path, version)]:
                    if new_sums[key] != value.get("GoModSum" if key[2] else "Sum"):
                        raise ValueError("Added Go checksum differs from the selected module artifact")
        for root, expected in ((before_tree, original), (after_tree, candidate)):
            for name in ("go.mod", "go.sum"):
                if _contents(root, str(Path(directory) / name)) != expected.get(name):
                    raise ValueError("Go metadata changed during prerequisite inspection")
        for name in changed:
            relative = str(Path(directory) / name)
            if relative not in paths:
                raise ValueError("Go prerequisite companion changed outside the recorded attempt")
            result["files"][relative] = candidate[name]
        result["modules"].append(
            {
                "directory": directory,
                "commands": commands,
                "original_sha256": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in original.items()},
                "candidate_sha256": {k: hashlib.sha256(v.encode()).hexdigest() for k, v in candidate.items()},
                "selection_changes": {
                    k: {"before": selections[0].get(k), "after": selections[1].get(k)}
                    for k in sorted(selections[0].keys() | selections[1].keys())
                    if selections[0].get(k) != selections[1].get(k)
                },
            }
        )
    (evidence / "receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def prepare_base(make_tree, workspace, base, run_dir, candidate, test_changes, changes, notes, run_command, **options):
    """Prepare the original-source test comparison with validated loading inputs."""
    from pathlib import Path

    tree = make_tree(workspace, base, run_dir / "scratch" / "base-with-tests", workspace, test_changes, **options)
    paths = [path for path in changes if Path(path).name in ("go.mod", "go.sum")]
    if not paths:
        return tree, None
    try:
        receipt = from_trees(tree, candidate, paths, run_dir / "go-test-prerequisites", run_command)
    except (OSError, ValueError) as error:
        # Dependency changes that are not loading prerequisites retain the ordinary base.
        notes.append("Go metadata was not admitted as a test prerequisite: " + str(error))
        return tree, None
    for path, content in receipt["files"].items():
        (tree / path).write_text(content)
    return tree, receipt


def from_attempt(workspace, record, paths, evidence_dir, run_command):
    """Use only original manifest bytes authenticated by the attempt's snapshot."""
    import hashlib
    import subprocess
    import tempfile
    from pathlib import Path

    paths = _paths(paths)
    snapshot = json.loads(Path(record["before_ref"]).read_text())
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    original = Path(tempfile.mkdtemp(prefix="original-", dir=evidence))
    for directory in sorted({str(Path(name).parent) for name in paths}):
        for basename in ("go.mod", "go.sum"):
            name = str(Path(directory) / basename)
            expected = snapshot["files"].get(name)
            if expected is None:
                continue
            read = subprocess.run(
                ["git", "-C", str(workspace), "show", f"{snapshot['head']}:{name}"], capture_output=True
            )
            if read.returncode:
                raise ValueError("Original Go prerequisite revision cannot be read")
            content = read.stdout
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("Original Go prerequisite bytes are not authenticated by the attempt")
            target = original / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return from_trees(original, workspace, paths, evidence, run_command)
