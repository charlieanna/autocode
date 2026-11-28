#!/usr/bin/env python3
"""Run AutoCode's own setup steps on pinned real repositories, with no model (the setup sweep).

Language and layout support was proven on fixtures of a few dozen lines, and a new language often broke
AutoCode's own setup the first time a real project met it. This sweep runs the steps AutoCode takes on
its own, before and around any model, on real repositories pinned to exact commits
(``tools/setup_sweep_repos.json``, docs/setup-sweep.md):

1. ``runner``: the suite AutoCode detects for the checkout (``autocode_verify.detect_framework``);
2. ``base``: that suite on a clean copy of the commit, as the regression proof runs it on the base
   (``autocode_verify.baseline``), and its health (``autocode_suite_health``);
3. ``targeted``: the suite narrowed to two of the tests that passed there
   (``autocode_proof_stability.narrow``), run on another clean copy: the narrowed command must report
   exactly those tests, passing. The mutation check and a focused proof both depend on it;
4. ``mutation``: the runner has a mutation source (``autocode_mutation_check.FRAMEWORKS``) and the
   narrowed run worked, so the mutation check could run;
5. ``coverage``: the coverage tool AutoCode would use for the narrowed command, or for the whole suite
   when the runner names no test to narrow to (``autocode_diff_coverage.prepare``, the project's own
   native tool included), run on a clean copy after that command, with a report that names files of the
   checkout.

Each repository is fetched at its pinned commit and prepared as a person prepares a checkout (its
``prepare`` commands: a virtualenv, ``npm ci``, ``composer install``); AutoCode itself installs
nothing. Every observation is compared with the entry's ``expect``; any difference fails the sweep.

    python3 tools/setup_sweep.py run --out .scenario-runs/setup-sweep [--only ID]... [--language L]...
    python3 tools/setup_sweep.py list

A developer script beside run_suite.py: it is not part of the installed package and no runtime module
imports it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import autocode_diff_coverage as diff_coverage  # noqa: E402
import autocode_mutation_check as mutation_check  # noqa: E402
import autocode_proof_stability as proof_stability  # noqa: E402
import autocode_verify as verify  # noqa: E402

REPOS = HERE / "setup_sweep_repos.json"
# What each step may report. ``runner`` is a framework name or None; ``coverage`` a tool or None.
EXPECT_KEYS = ("runner", "base", "targeted", "mutation", "coverage")
HEALTH = ("passing", "failing_tests", "failing", "broken", "timeout")
PREPARE_SECONDS = 900
SUITE_SECONDS = 900
# Files a person's global ignore keeps out of every checkout: the environments `prepare` creates.
LOCAL_IGNORES = (".venv/", "node_modules/", "vendor/", "target/", ".gradle/", "build/")
TARGETED_TESTS = 2
# How much of a log a difference carries into the job's own output, which stays readable when the
# uploaded artifacts are not (#4212: a lint failure's tail held only its stack, not the missing module).
TAIL_CHARACTERS = 2000


class EntryError(ValueError):
    """A repository entry that is not well formed."""


def load(path=REPOS) -> list[dict]:
    """The validated entries of the pinned repository list."""
    entries = json.loads(Path(path).read_text())["repositories"]
    seen = set()
    for entry in entries:
        validate(entry)
        if entry["id"] in seen:
            raise EntryError(f"{entry['id']}: listed twice")
        seen.add(entry["id"])
    return entries


def validate(entry) -> None:
    identity = entry.get("id") if isinstance(entry, dict) else None
    if not isinstance(identity, str) or not identity:
        raise EntryError(f"an entry has no id: {entry!r}")
    allowed = {"id", "language", "git", "commit", "prepare", "expect", "note", "env", "timeout"}
    if extra := set(entry) - allowed:
        raise EntryError(f"{identity}: unknown keys {sorted(extra)}")
    for key in ("language", "git", "commit"):
        if not isinstance(entry.get(key), str) or not entry[key]:
            raise EntryError(f"{identity}: {key} is required")
    commit = entry["commit"]
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise EntryError(f"{identity}: commit must be a full 40-character hash")
    if not isinstance(entry.get("prepare", []), list) or not all(isinstance(c, str) for c in entry.get("prepare", [])):
        raise EntryError(f"{identity}: prepare is a list of shell commands")
    expect = entry.get("expect")
    if not isinstance(expect, dict) or set(expect) != set(EXPECT_KEYS):
        raise EntryError(f"{identity}: expect needs exactly {', '.join(EXPECT_KEYS)}")
    if expect["base"] not in HEALTH if expect["runner"] is not None else expect["base"] is not None:
        raise EntryError(f"{identity}: expect.base is one of {', '.join(HEALTH)}, or null with no runner")
    if not isinstance(expect["targeted"], bool) or not isinstance(expect["mutation"], bool):
        raise EntryError(f"{identity}: expect.targeted and expect.mutation are true or false")


def _git(cwd, *args, timeout=600):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout)


def fetch(entry, destination: Path) -> str:
    """A checkout of exactly the pinned commit, as its one commit; returns that commit."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    _git(destination, "init", "-q")
    _git(destination, "fetch", "-q", "--depth", "1", entry["git"], entry["commit"])
    _git(destination, "-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD")
    head = _git(destination, "rev-parse", "HEAD").stdout.strip()
    if head != entry["commit"]:
        raise RuntimeError(f"fetched {head}, pinned {entry['commit']}")
    exclude = destination / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a") as handle:
        handle.write("\n" + "\n".join(LOCAL_IGNORES) + "\n")
    return head


def prepare(entry, checkout: Path, log: Path) -> str | None:
    """Run the entry's prepare commands in the checkout; the failure, or None."""
    env = {**os.environ, **entry.get("env", {})}
    with log.open("w") as handle:
        for command in entry.get("prepare", []):
            handle.write(f"$ {command}\n")
            handle.flush()
            try:
                done = subprocess.run(
                    command,
                    shell=True,
                    cwd=checkout,
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    timeout=PREPARE_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return f"{command}: timed out after {PREPARE_SECONDS}s"
            if done.returncode:
                return f"{command}: exit {done.returncode}"
    return None


def _scratch_run(framework, command, checkout, head, folder: Path, label, timeout):
    """``command`` on a clean copy of ``head``, with the checkout's dependencies, as a proof runs it."""
    tree = verify.make_tree(checkout, head, folder / "tree", checkout, {}, dependencies_from=str(checkout))
    try:
        return verify.run_suite(framework, command, tree, folder, label, timeout=timeout), tree
    except BaseException:
        verify.remove_tree(checkout, tree)
        raise


def pick_tests(passed) -> list[str]:
    """Two passing tests, from different files when the suite has more than one: first and last by name."""
    names = sorted(passed)
    if len(names) <= TARGETED_TESTS:
        return names
    return [names[0], names[-1]]


def coverage_files(tool, report: Path, tree) -> int:
    """How many files of ``tree`` the report measured, read as the proof reads it (``diff_coverage.parse``)."""
    listed = subprocess.run(["git", "ls-files"], cwd=tree, capture_output=True, text=True, check=False).stdout
    executed, missing = diff_coverage.parse(tool, report.read_bytes(), tree, listed.splitlines())
    names = set(executed) | set(missing)
    return sum(1 for name in names if (Path(tree) / name).is_file())


def observe(entry, out: Path) -> dict:
    """Every step's observation for one repository; never raises for a step that fails."""
    timeout = int(entry.get("timeout", SUITE_SECONDS))
    folder = out / entry["id"]
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    checkout = folder / "checkout"
    seen: dict = {"id": entry["id"], "language": entry["language"], "notes": []}
    started = time.monotonic()
    try:
        head = fetch(entry, checkout)
    except (subprocess.SubprocessError, OSError, RuntimeError) as error:
        seen["error"] = f"fetch: {getattr(error, 'stderr', '') or error}".strip()
        return seen
    if failure := prepare(entry, checkout, folder / "prepare.log"):
        seen["error"] = f"prepare: {failure} (see {folder / 'prepare.log'})"
        return seen
    seen["prepare_seconds"] = round(time.monotonic() - started, 1)

    framework = verify.detect_framework(checkout)
    seen["runner"] = framework.name if framework else None
    seen["command"] = framework.suite if framework else None
    if framework is None:
        seen.update(base=None, targeted=False, mutation=False, coverage=None)
        return seen

    base = verify.baseline(
        checkout,
        head,
        folder / "base",
        framework=framework,
        suite_command=framework.suite,
        timeout=timeout,
        dependencies_from=str(checkout),
    )
    receipt = base["receipt"]
    results = receipt.get("results") or {}
    seen["base"] = base["health"]
    seen["base_seconds"] = receipt.get("duration_seconds")
    seen["tests"] = {key: len(results.get(key) or []) for key in ("passed", "failed", "skipped")}
    seen["base_log"] = receipt.get("output")
    if base["health"] != "passing" and receipt.get("tail"):
        seen["notes"].append("base tail: " + receipt["tail"][-TAIL_CHARACTERS:])

    chosen = pick_tests(results.get("passed") or [])
    narrowed = proof_stability.narrow(framework.name, framework.suite, chosen) if chosen else None
    seen["targeted_tests"] = chosen
    seen["targeted_command"] = narrowed
    seen["targeted"] = False
    seen["coverage"] = None
    if narrowed:
        receipt, tree = _scratch_run(framework, narrowed, checkout, head, folder / "targeted", "targeted", timeout)
        try:
            reported = receipt.get("results") or {}
            passed = set(reported.get("passed") or [])
            seen["targeted"] = receipt["exit_code"] == 0 and set(chosen) <= passed
            if not seen["targeted"]:
                seen["notes"].append(
                    f"targeted: exit {receipt['exit_code']}, passed {sorted(passed)[:5]}, "
                    f"failed {(reported.get('failed') or [])[:5]}; tail: {(receipt.get('tail') or '')[-400:]}"
                )
            spec = diff_coverage.prepare(framework, narrowed, tree, folder / "coverage")
            if spec is not None:
                seen["coverage"] = _coverage(spec, tree, folder / "coverage", timeout, seen["notes"])
        finally:
            verify.remove_tree(checkout, tree)
    else:
        if chosen:
            seen["notes"].append(f"targeted: {framework.name} could not narrow {framework.suite!r}")
        if base["health"] in ("passing", "failing_tests"):
            seen["coverage"] = _suite_coverage(framework, checkout, head, folder, timeout, seen["notes"])
    seen["mutation"] = framework.name in mutation_check.FRAMEWORKS and seen["targeted"]
    seen["seconds"] = round(time.monotonic() - started, 1)
    return seen


def _suite_coverage(framework, checkout, head, folder: Path, timeout, notes) -> str | None:
    """Coverage of the whole suite's command, which the proof measures when no test can be narrowed to."""
    # A native tool (JaCoCo, gcov) reads what the command's own run left, so the suite runs first on the copy.
    _, tree = _scratch_run(framework, framework.suite, checkout, head, folder / "suite", "suite", timeout)
    try:
        spec = diff_coverage.prepare(framework, framework.suite, tree, folder / "coverage")
        return None if spec is None else _coverage(spec, tree, folder / "coverage", timeout, notes)
    finally:
        verify.remove_tree(checkout, tree)


def _coverage(spec, tree, folder: Path, timeout, notes) -> str | None:
    report = Path(spec["report"])
    report.unlink(missing_ok=True)
    verify.run_command(spec["command"], tree, folder / "run.log", timeout=timeout)
    if spec["finalize"]:
        verify.run_command(spec["finalize"], tree, folder / "report.log", timeout=timeout)
    if not report.is_file():
        notes.append(f"coverage: {spec['tool']} wrote no report")
        return None
    try:
        measured = coverage_files(spec["tool"], report, tree)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        notes.append(f"coverage: {spec['tool']} report unreadable: {error}")
        return None
    if not measured:
        notes.append(f"coverage: {spec['tool']} measured no file of the checkout")
        return None
    return spec["tool"]


def differences(entry, seen) -> list[str]:
    """What the sweep saw that the entry does not expect."""
    if "error" in seen:
        return [seen["error"]]
    return [
        f"{key}: expected {entry['expect'][key]!r}, saw {seen.get(key)!r}"
        for key in EXPECT_KEYS
        if seen.get(key) != entry["expect"][key]
    ]


def prepare_tail(log: Path) -> str:
    """The end of a repository's prepare log, for a difference: what it installed, and what it skipped."""
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return "prepare tail: no prepare log"
    return "prepare tail: " + (text[-TAIL_CHARACTERS:] or "(empty)")


def table(rows) -> str:
    lines = [
        "| Repository | Language | Runner | Base | Tests passed | Targeted | Mutation | Coverage | Result |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for entry, seen, problems in rows:
        tests = (seen.get("tests") or {}).get("passed", "")
        result = "ok" if not problems else "**" + "; ".join(problems).replace("|", "\\|")[:300] + "**"
        lines.append(
            f"| {entry['id']} | {entry['language']} | {seen.get('runner')} | {seen.get('base')} | {tests} | "
            f"{seen.get('targeted')} | {seen.get('mutation')} | {seen.get('coverage')} | {result} |"
        )
    return "\n".join(lines)


def select(entries, only=(), languages=(), shard=None) -> list[dict]:
    chosen = [e for e in entries if (not only or e["id"] in only) and (not languages or e["language"] in languages)]
    if only and (missing := set(only) - {e["id"] for e in entries}):
        raise EntryError(f"no entry named {', '.join(sorted(missing))}")
    if shard:
        index, total = shard
        chosen = chosen[index - 1 :: total]
    return chosen


def _shard(text):
    index, _, total = text.partition("/")
    index, total = int(index), int(total)
    if not 1 <= index <= total:
        raise argparse.ArgumentTypeError("shard is I/N with 1 <= I <= N")
    return index, total


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="fetch, prepare and observe the selected repositories")
    run.add_argument("--repos", type=Path, default=REPOS)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--only", action="append", default=[])
    run.add_argument("--language", action="append", default=[])
    run.add_argument("--shard", type=_shard)
    run.add_argument("--keep", action="store_true", help="keep each checkout and its evidence")
    listing = sub.add_parser("list", help="print the pinned repositories")
    listing.add_argument("--repos", type=Path, default=REPOS)
    args = parser.parse_args(argv)
    entries = load(args.repos)
    if args.command == "list":
        for entry in entries:
            print(f"{entry['id']:32} {entry['language']:11} {entry['git']}@{entry['commit'][:12]}")
        return 0
    chosen = select(entries, args.only, args.language, args.shard)
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry in chosen:
        print(f"== {entry['id']}", flush=True)
        seen = observe(entry, args.out.resolve())
        problems = differences(entry, seen)
        if problems:
            seen["notes"].append(prepare_tail(args.out / entry["id"] / "prepare.log"))
        rows.append((entry, seen, problems))
        print(json.dumps({**seen, "problems": problems}, indent=2), flush=True)
        if not args.keep:
            shutil.rmtree(args.out / entry["id"] / "checkout", ignore_errors=True)
    (args.out / "results.json").write_text(
        json.dumps([{**seen, "problems": problems} for _, seen, problems in rows], indent=2) + "\n"
    )
    summary = table(rows)
    (args.out / "summary.md").write_text(summary + "\n")
    print(summary)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a") as handle:
            handle.write(summary + "\n")
    failed = [entry["id"] for entry, _, problems in rows if problems]
    print(
        f"{len(rows) - len(failed)} of {len(rows)} repositories as expected"
        + (f"; differ: {', '.join(failed)}" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
