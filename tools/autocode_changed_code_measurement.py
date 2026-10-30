"""Source-bound measurement inputs for changed-code evidence (#2474).

The caller owns the proof's scratch tree, executes commands and persists this
record with its existing source/contract/receipt pins. This module does not
grant a verdict from an advisory coverage or mutation summary alone.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
import types
from copy import deepcopy
from pathlib import Path, PurePosixPath

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_diff_coverage as diff_coverage
    from . import autocode_mutants as mutants
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_diff_coverage as diff_coverage
    import autocode_mutants as mutants
    import autocode_verification_schedule as schedule

MAX_CAPTURE_BYTES = 64 * 1024 * 1024


def source(root: Path, name: str) -> bytes:
    """Read an ordinary product file in the already-owned candidate tree."""
    relative = PurePosixPath(name)
    if relative.is_absolute() or str(relative) != name or any(p in (".", "..") for p in relative.parts):
        raise ValueError("changed-code path is not a relative product path")
    path = root / name
    if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
        raise ValueError("changed-code path traverses a symlink")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("changed-code source is missing or outside the candidate")
    return path.read_bytes()


def python_lines(data: bytes) -> set[int]:
    """Executable Python line table, excluding declarations and docstrings.

    No source is executed to collect this inventory. Nested function/class code
    objects contribute their own line tables. Blank/comment-only continuation
    lines absent from the compiler's table do not become false obligations.
    """
    syntax = ast.parse(data)
    docs: set[int] = set()
    declarations: set[int] = set()
    evaluated: set[int] = set()
    future_annotations = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in syntax.body
    )
    for node in ast.walk(syntax):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            docs.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # A definition itself is not a product statement to test. Expressions
            # evaluated by that definition still are, including multiline ones.
            end = first.lineno if first is not None else node.lineno
            declarations.update(range(node.lineno, max(node.lineno + 1, end)))
            expressions = list(node.decorator_list)
            if isinstance(node, ast.ClassDef):
                expressions += [*node.bases, *(keyword.value for keyword in node.keywords)]
            else:
                expressions += [*node.args.defaults, *(item for item in node.args.kw_defaults if item is not None)]
                if not future_annotations:
                    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                    arguments += [item for item in (node.args.vararg, node.args.kwarg) if item is not None]
                    expressions += [item.annotation for item in arguments if item.annotation is not None]
                    expressions += [node.returns] if node.returns is not None else []
            for expression in expressions:
                evaluated.update(range(expression.lineno, (expression.end_lineno or expression.lineno) + 1))
            if (
                first is not None
                and first.lineno == node.lineno
                and not (
                    isinstance(first, ast.Pass) or isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                )
            ):
                evaluated.add(first.lineno)  # a one-line body is executable on its declaration's line
    pending = [compile(syntax, "<changed-code>", "exec", dont_inherit=True)]
    lines: set[int] = set()
    while pending:
        code = pending.pop()
        lines.update(line for _, _, line in code.co_lines() if line is not None and line > 0)
        pending.extend(value for value in code.co_consts if isinstance(value, types.CodeType))
    return (lines - declarations - docs) | (evaluated & lines)


def inventory(tree, files, diff_text) -> list[dict]:
    """Inventory every changed product line, retaining uncertainty explicitly.

    For other languages coverage's own statement map refines this inventory.
    Without a parser/tool only blank and whole-line comments are omitted; all
    other changed lines require evidence or an independently checked reason.
    """
    root = Path(tree).resolve()
    changed = mutants.changed_lines(diff_coverage.hunks(diff_text))
    rows = []
    for name, status in files:
        if status == "deleted":
            continue
        data = source(root, name)
        selected = set(changed.get(name, ()))
        note = None
        if name.endswith(".py"):
            try:
                selected &= python_lines(data)
            except (SyntaxError, ValueError, TypeError):
                note = "Python statements could not be inventoried; changed lines require independent review"
        elif name.endswith(".pyi"):
            selected = set()
            note = "Python type stub: no runtime statement"
        else:
            note = "No language statement parser; coverage or independent review must classify changed lines"
        text = data.decode("utf-8", errors="replace").splitlines()
        comments = ("#",) if name.endswith((".py", ".pyi", ".sh", ".rb")) else ("//",)
        block_comments = _block_comment_lines(data, name)
        selected = {
            line
            for line in selected
            if 0 < line <= len(text)
            and line not in block_comments
            and text[line - 1].strip()
            and not text[line - 1].lstrip().startswith(comments)
        }
        rows.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "lines": sorted(selected), "note": note})
    return rows


def _block_comment_lines(data: bytes, name: str) -> set[int]:
    """Recognize only whole-line C-style comments with unambiguous boundaries.

    Multiline strings and escaped newlines need a language parser. In their
    presence retain every line, as for languages with nested block comments.
    """
    if Path(name).suffix not in {".go", ".js", ".mjs", ".cjs", ".ts", ".c", ".h", ".cc", ".cpp", ".hpp"}:
        return set()
    text = data.decode("utf-8", errors="replace")
    if "`" in text or 'R"' in text or re.search(r"\\\r?\n", text):
        return set()
    excluded: set[int] = set()
    inside = False
    for number, line in enumerate(text.splitlines(), 1):
        remaining = line.lstrip()
        comment_only = inside
        while remaining or inside:
            if inside:
                comment_only = True
                end = remaining.find("*/")
                if end < 0:
                    break
                inside = False
                remaining = remaining[end + 2 :].lstrip()
            elif remaining.startswith("/*"):
                inside = True
                comment_only = True
                remaining = remaining[2:]
            else:
                comment_only = False
                break
        if comment_only:
            excluded.add(number)
    return excluded


def refine_inventory(inventory_rows, authenticated_coverage, *, source_revision, proof_context):
    """Use an already authenticated statement map to classify non-Python lines.

    Only coverage_envelope output with the exact current bindings can refine
    conservative inventory. Missing or malformed evidence preserves obligations.
    """
    result = deepcopy(inventory_rows)
    if (
        not isinstance(result, list)
        or not isinstance(authenticated_coverage, dict)
        or not isinstance(source_revision, str)
        or not source_revision
        or not isinstance(proof_context, str)
        or not proof_context
        or authenticated_coverage.get("source_revision") != source_revision
        or authenticated_coverage.get("proof_context") != proof_context
        or authenticated_coverage.get("tool") not in {"coverage.py", "go coverprofile", "c8", "vitest"}
        or not isinstance(authenticated_coverage.get("files"), list)
    ):
        return result
    files: dict[str, dict | None] = {}
    for measured in authenticated_coverage["files"]:
        if isinstance(measured, dict) and isinstance(measured.get("path"), str):
            path = measured["path"]
            files[path] = None if path in files else measured
    for row in result:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            continue
        if row["path"].endswith((".py", ".pyi")):
            continue
        measured = files.get(row["path"])
        if not measured or measured.get("sha256") != row.get("sha256"):
            continue
        digest = row.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        lists = (row.get("lines"), measured.get("executed_lines"), measured.get("missing_lines"))
        if not all(
            isinstance(lines, list) and all(type(line) is int and line > 0 for line in lines) for lines in lists
        ):
            continue
        row["lines"] = sorted(set(row["lines"]) & (set(measured["executed_lines"]) | set(measured["missing_lines"])))
        row["note"] = f"Executable lines classified by the authenticated {authenticated_coverage['tool']} statement map"
    return result


def coverage_envelope(report, inventory_rows, *, source_revision, proof_context, receipts, root):
    """Bind measured statements only when every producing command is authenticated.

    An advisory report without successful owned commands remains unavailable.
    This prevents an old report or failed coverage invocation supplying proof.
    """
    result = {"source_revision": source_revision, "proof_context": proof_context, "files": [], "tool": None}
    if not isinstance(report, dict) or report.get("collected") is not True or report.get("report_fresh") is not True:
        return result
    if not isinstance(receipts, (list, tuple)) or not receipts:
        return result
    if not all(_owned(receipt, root) and receipt.get("exit_code") == 0 for receipt in receipts):
        return result
    outputs = [receipt["output"] for receipt in receipts]
    if report.get("command_outputs") != outputs or len(set(outputs)) != len(outputs):
        return result
    try:
        raw = pinned(report.get("report_path"), report.get("report_sha256"), root)
        source_root = Path(report["source_root"])
        if not source_root.is_absolute() or any(path.is_symlink() for path in (source_root, *source_root.parents)):
            return result
        tool = report.get("tool")
        if tool == "go coverprofile":
            # Zero-statement blocks (for example a declaration) carry no line
            # execution obligation. The legacy advisory parser includes them.
            text = "\n".join(
                line for line in raw.decode().splitlines() if not re.search(r":\d+\.\d+,\d+\.\d+\s+0\s+\d+$", line)
            )
            executed, missing = diff_coverage.parse_go_cover(text, source_root)
        elif tool in ("c8", "vitest"):
            executed, missing = diff_coverage.parse_istanbul(json.loads(raw), source_root)
        elif tool == "coverage.py":
            executed, missing = diff_coverage.parse_coverage_json(json.loads(raw), source_root)
        else:
            return result
        if any(
            type(line) is not int or line < 1
            for mapping in (executed, missing)
            for lines in mapping.values()
            for line in lines
        ):
            return result
        measured = []
        for row in inventory_rows:
            # Read once, and bind the bytes that supplied the inventory. A report
            # from another or modified tree cannot be projected onto this file.
            if hashlib.sha256(source(source_root, row["path"])).hexdigest() != row["sha256"]:
                return result
            if row["path"] in executed or row["path"] in missing:
                measured.append(
                    {
                        "path": row["path"],
                        "sha256": row["sha256"],
                        "executed_lines": sorted(executed.get(row["path"], ())),
                        "missing_lines": sorted(missing.get(row["path"], ())),
                    }
                )
        result.update(files=measured, tool=tool)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return result
    return result


def pinned(path, digest, root):
    """Read bounded immutable capture bytes through no-follow directory FDs."""
    if not isinstance(path, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("measurement artifact has no exact pin")
    if (
        not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))
        or os.open not in os.supports_dir_fd
    ):
        raise ValueError("descriptor-anchored measurement reads are unavailable on this host")
    root = Path(root)
    if root != root.resolve() or root.is_symlink():
        raise ValueError("measurement root is not physical and canonical")
    target = Path(path)
    if not target.is_absolute():
        raise ValueError("measurement artifact path is not absolute")
    relative = target.relative_to(root)
    if not relative.parts or any(part in (".", "..") for part in relative.parts):
        raise ValueError("measurement artifact path is not an ordinary owned path")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        leaf = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CAPTURE_BYTES:
                raise ValueError("measurement artifact is not a bounded regular file")
            with os.fdopen(leaf, "rb", closefd=False) as stream:
                data = stream.read(MAX_CAPTURE_BYTES + 1)
            after = os.fstat(leaf)
            if len(data) > MAX_CAPTURE_BYTES or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("measurement artifact changed while being captured")
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("measurement artifact changed")
    return data


def _owned(receipt, root):
    return (
        isinstance(receipt, dict)
        and all(name in receipt for name in command_receipt.OWNERSHIP_FIELDS)
        and command_receipt.completed(receipt, root=root)
        and schedule.intact(receipt, root=root)
    )


def known_capture(receipt, root):
    """Authenticate a completed capture or a closed, nonpositive deadline.

    A deadline never proves a kill or execution, but known terminal custody must
    not turn it into an unknown-process hold. No process or artifact is changed.
    """
    if not isinstance(receipt, dict) or not all(key in receipt for key in command_receipt.OWNERSHIP_FIELDS):
        return False
    try:
        pinned(receipt.get("output"), receipt.get("output_sha256"), root)
    except (OSError, ValueError, TypeError):
        return False
    if command_receipt.completed(receipt, root=root):
        return True
    pin = receipt.get("supervision_sha256")
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin):
        return False
    terminal = command_receipt.load(receipt.get("supervision"), root=root, pin=pin)
    return bool(
        terminal
        and terminal["phase"] == "stopped"
        and not terminal["cleanup_error"]
        and terminal["cause"] == "stage_deadline"
        and receipt.get("timed_out") is True
        and receipt.get("exit_code") is None
        and not receipt.get("interrupted")
        and receipt.get("supervision_errors") == []
    )


def _names(value):
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
        and len(set(value)) == len(value)
    )


def _outcome(receipt, selected, root):
    if not _owned(receipt, root) or not schedule.complete_results(receipt):
        return "not_run", []
    results = receipt["results"]
    if any(results.get(key) for key in ("skipped", "setup_errors", "collection_errors", "uncollected")):
        return "not_run", []
    passed, failed = set(results["passed"]), set(results["failed"])
    if passed | failed != set(selected):
        return "not_run", []
    if failed and receipt["exit_code"] != 0:
        return "killed", sorted(failed)
    if not failed and receipt["exit_code"] == 0:
        return "survived", sorted(passed)
    return "not_run", []


def mutation_envelope(report, *, source_revision, proof_context, receipts, root):
    """Retain every mutant; only exact owned test failures support a kill.

    Invalid, timed-out or unrun observations must not disappear beside a valid
    kill on the same line. Uncertain rows become explicit not_run observations.
    The unmutated baseline must first report every selected test passing.
    """
    result = {"source_revision": source_revision, "proof_context": proof_context, "files": []}
    if not isinstance(report, dict) or not isinstance(report.get("files"), list):
        return result
    outputs = {}
    for receipt in receipts if isinstance(receipts, (list, tuple)) else ():
        if isinstance(receipt, dict) and isinstance(receipt.get("output"), str):
            output = receipt["output"]
            outputs[output] = None if output in outputs else receipt
    selected = report.get("selected_tests")
    reference = report.get("reference_output")
    baseline = outputs.get(reference) if isinstance(reference, str) else None
    eligible = (
        report.get("collected") is True and _names(selected) and _outcome(baseline, selected, root)[0] == "survived"
    )
    for file in report["files"]:
        if not isinstance(file, dict) or not isinstance(file.get("mutants"), list):
            return {**result, "files": []}
        made = []
        for original in file["mutants"]:
            row = original if isinstance(original, dict) else {}
            entry = {key: row.get(key) for key in ("line", "end_line", "operator")}
            entry.update(outcome="not_run", failed_tests=[], evidence=[])
            references = row.get("evidence")
            if eligible and row.get("source_sha256") == file.get("sha256") and _names(references):
                observed = [_outcome(outputs.get(ref), selected, root) for ref in references]
                failed = sorted({name for outcome, names in observed if outcome == "killed" for name in names})
                if (
                    all(outcome == "killed" for outcome, _ in observed)
                    and row.get("outcome") == "killed"
                    and row.get("failed_tests") == failed
                ):
                    entry.update(outcome="killed", failed_tests=failed, evidence=list(references))
                elif all(outcome == "survived" for outcome, _ in observed) and row.get("outcome") == "survived":
                    entry.update(outcome="survived", evidence=list(references))
            if entry["outcome"] == "not_run" and row.get("outcome") in ("timeout", "unsupported", "error"):
                entry["outcome"] = row["outcome"]
            made.append(entry)
        result["files"].append({"path": file.get("path"), "sha256": file.get("sha256"), "mutants": made})
    return result
