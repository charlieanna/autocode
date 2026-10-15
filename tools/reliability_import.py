"""Fill a reliability campaign's ledger rows from native result records (#634).

The evaluator names the scheduled attempt each record belongs to:

    .venv/bin/python tools/reliability_import.py arena/campaign.json ledger.json \\
        --scenario scenario/bugfix-iso-weeks/01 .scenario-runs/RUN/result.json \\
        --arena arena/sympy-matrix-derivatives/01 .autocode/arena-hard-v1 ATTEMPT_ID \\
        --runtime-identity runtime.json

A scenario harness result (the `result.json` that `scenarios/run.py` writes) or an Arena attempt (a row
of the store's `results.sqlite3`, opened read-only) fills the `DEFERRED` row of the attempt named for it.
The native verdict, status and oracle checks are copied as recorded, never rescored. What a record does
not establish stays null: the commit of a checkout with uncommitted `tools/` changes, an incomplete cost,
a limit the harness does not measure. Neither record holds the runtime versions; `--runtime-identity`
adds the evaluator's admission record to every row the call imports. A recorded attempt is never
replaced and one record never fills two rows. The whole ledger is checked by `reliability_ledger.report`
before it is written back, so a refused import leaves the file as it was.

Reading an Arena attempt uses Arena's own runner-identity and option rules, which need psutil: run it with
the project's virtualenv.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import functools
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

try:
    from . import reliability_ledger as ledger
except ImportError:
    import reliability_ledger as ledger

LedgerError = ledger.LedgerError
COMMIT = re.compile(r"[0-9a-f]{40}")
ARENA_ATTEMPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")  # the Arena's own attempt-id rule


def _minutes(seconds):
    if type(seconds) in (int, float) and math.isfinite(seconds):
        return round(seconds / 60, 4)
    return seconds  # an unusable value stays as recorded, for the ledger check to refuse


# What the scenario harness measures against each declared limit name: its one wall-clock budget for the
# drive and its count of recorded CLI calls. Arena records neither per CLI invocation, so an Arena row's
# declared limits all stay unknown.
SCENARIO_SPEND = {
    "harness_minutes": lambda result: _minutes(result.get("wall_seconds")),
    "max_steps": lambda result: result.get("cli_calls"),
}


# How a profile's roles reach the runner: the scenario harness's MODEL_FLAGS and EFFORT_FLAGS
# (scenarios/harness/profiles.py), which a test keeps equal to this table. With the profile's provider,
# --joint-planning and `extra` flags they are the options the declared configuration forwards.
ROLE_FLAGS = {
    "requirements": ("--requirements-model", "--requirements-reasoning-effort"),
    "planner": ("--glm-model", "--glm-reasoning-effort"),
    "reviewer": ("--plan-reviewer-model", "--plan-reviewer-reasoning-effort"),
    "builder": ("--terra-model", "--reasoning-effort"),
    "validator": ("--sol-model", "--sol-reasoning-effort"),
    "resolver": ("--astra-model", "--astra-reasoning-effort"),
    "completion": ("--completion-model", "--completion-reasoning-effort"),
}

# Where AutoCode takes each role's model and effort from, the first of these flags given winning
# (autocode_configure, under the joint planning the declared profile runs): --single-model names every
# model; the Requirements stage falls back to the planner's model; a role's own effort flag overrides
# --reasoning-effort, which the harness writes as the builder's effort and the builder, validator, resolver
# and completion roles fall back to; and --resolver-model and --resolver-reasoning-effort set the resolver
# over the --astra-* role it otherwise copies. Arena records the options it forwarded, and this table reads
# each role back from them.
EFFECTIVE_FLAGS = {
    "requirements": (
        ("--single-model", "--requirements-model", "--glm-model"),
        ("--requirements-reasoning-effort",),
    ),
    "planner": (("--single-model", "--glm-model"), ("--glm-reasoning-effort",)),
    "reviewer": (("--single-model", "--plan-reviewer-model"), ("--plan-reviewer-reasoning-effort",)),
    "builder": (("--single-model", "--terra-model"), ("--terra-reasoning-effort", "--reasoning-effort")),
    "validator": (("--single-model", "--sol-model"), ("--sol-reasoning-effort", "--reasoning-effort")),
    "resolver": (
        ("--resolver-model", "--single-model", "--astra-model"),
        ("--resolver-reasoning-effort", "--astra-reasoning-effort", "--reasoning-effort"),
    ),
    "completion": (
        ("--single-model", "--completion-model"),
        ("--completion-reasoning-effort", "--reasoning-effort"),
    ),
}


def _arena():
    try:
        try:
            from . import autocode_arena
        except ImportError:
            import autocode_arena
    except ModuleNotFoundError as error:
        if error.name != "psutil":
            raise
        raise LedgerError("reading an Arena attempt needs psutil: run with the project's .venv/bin/python") from error
    return autocode_arena


def _arena_store():
    try:
        from . import autocode_arena_store
    except ImportError:
        import autocode_arena_store
    return autocode_arena_store


def _git(directory, *args) -> bytes:
    """A Git command's output, read from local objects; CalledProcessError when Git cannot answer."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    command = ["git", "-C", str(directory), *args]
    return subprocess.run(command, capture_output=True, check=True, timeout=120, env=env).stdout


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value, label: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise LedgerError(f"{label} must be a list of objects")
    return value


def _checks(value, label: str):
    """The oracle checks as {name, ok}, or null when the record holds none."""
    checks = _list(value, label)
    return [{"name": check.get("name"), "ok": check.get("ok")} for check in checks] or None


def scenario_values(path: Path, task: dict) -> tuple[dict, str]:
    """A ledger row's values from a scenario harness result, and the key that identifies the record."""
    path = path / "result.json" if path.is_dir() else path
    if not path.is_file():
        if (path.parent / "attempt.json").is_file():
            raise LedgerError(
                f"{path.parent} holds an admitted scenario attempt with no result.json: the harness has not graded "
                "it. If it is still running, import it when it finishes. If `scenarios/run.py stats` reports it "
                "INTERRUPTED_UNGRADED or PENDING_UNGRADED, write its row by hand (docs/reliability-campaign.md)"
            )
        raise LedgerError(f"{path} is not a scenario result")
    data = path.read_bytes()
    result = ledger.parse_json(data, str(path))
    label = f"scenario result {path}"
    if result.get("scenario") != task["case_id"]:
        raise LedgerError(f"{label} is scenario {result.get('scenario')!r}, not the declared {task['case_id']!r}")
    if not isinstance(result.get("verdict"), str):
        raise LedgerError(f"{label} has no final verdict")
    # A live run names its resolved profile; a fake or hybrid run scripts some or all of its model stages. The
    # harness names a hybrid run's mode with a `-hybrid` part (harness/hybrid.py mode) before it sets the run's
    # `hybrid` block, so a hybrid run skipped before its setup has only that mode to show it.
    profile = result.get("profile")
    mode = str(result.get("mode"))
    scripted = mode.startswith("fake") or "hybrid" in mode.split("-") or bool(result.get("hybrid"))
    if isinstance(profile, dict) and not scripted:
        kind, actual = "live", dict(profile)
    else:
        kind, actual = "fixture", {"mode": result.get("mode")}
    metrics = _mapping(result.get("metrics"))
    if "model_routes" in metrics:
        actual["routes"] = metrics["model_routes"]
    # The harness records the commit and whether tools/ held uncommitted changes; then the commit did not run.
    autocode = _mapping(result.get("autocode"))
    commit = autocode.get("commit")
    clean = isinstance(commit, str) and COMMIT.fullmatch(commit) and autocode.get("dirty") is False
    # The status view's cost is a floor until it is complete; a floor is not the attempt's cost.
    cost = result.get("cost")
    usd = cost.get("spent_usd") if isinstance(cost, dict) and cost.get("complete") is True else None
    # Each answer and gate the harness gave on the person's behalf is a decision a person would make. A review
    # acceptance is both an answer and an approval, so it is counted once, as the approval.
    decisions = [
        {"kind": "decision", "note": f"the harness answered {answer.get('id')} ({answer.get('source')})"}
        for answer in _list(result.get("answers"), f"{label} answers")
        if answer.get("source") != "approval"
    ]
    decisions += [
        {"kind": "decision", "note": f"the harness passed the {gate.get('gate')} gate"}
        for gate in _list(result.get("approvals"), f"{label} approvals")
    ]
    values = {
        "execution_kind": kind,
        "source_revision": commit if clean else None,
        "actual_models_and_efforts": actual,
        "native_status": result.get("runner_status") or None,
        "native_outcome": result["verdict"],
        "oracle_checks": _checks(result.get("checks"), f"{label} checks"),
        "evidence_refs": [f"{label} sha256:{_digest(data)}"],
        "interventions_decision_or_repair": decisions,
        "elapsed_seconds": result.get("wall_seconds"),
        "usage": result.get("usage_snapshot"),
        "estimated_usd": usd,
        "spent_budget": {
            name: SCENARIO_SPEND[name](result) if name in SCENARIO_SPEND else None for name in task["limits"]
        },
        "reason": result.get("summary") or "",
    }
    return values, f"sha256:{_digest(data)}"


def revision_identity(repository: Path, revision: str):
    """Arena's runner identity (`autocode_arena.version_identity`) of `revision`, or None when Git cannot
    produce that revision's tree. Arena records this identity, not a commit."""
    return _revision_identity(str(Path(repository).resolve()), revision)


@functools.lru_cache(maxsize=8)  # a full commit's tree never changes
def _revision_identity(repository: str, revision: str):
    arena = _arena()
    try:
        top = _git(repository, "rev-parse", "--show-toplevel").decode().strip()  # archive paths are from the top
        archive = _git(top, "archive", "--format=tar", revision, "--", "tools", "pyproject.toml")
        with tempfile.TemporaryDirectory(prefix="reliability-import-") as tree:
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(tree, filter="data")
            return arena.version_identity(Path(tree))
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError):
        return None


def declared_oracle(repository: Path, revision: str, task: dict) -> tuple[str, str | None]:
    """The path of the declared task's Arena oracle and the SHA-256 of its bytes at `revision`, read from local
    Git objects; None when Git cannot produce them."""
    definition = task.get("definition")
    if not isinstance(definition, dict) or not isinstance(definition.get("path"), str):
        raise LedgerError(f"task {task['id']} declares no definition path")
    path = f"{definition['path']}/oracle.py"
    return path, _blob_digest(str(Path(repository).resolve()), revision, path)


@functools.lru_cache(maxsize=32)
def _blob_digest(repository: str, revision: str, path: str):
    try:
        return _digest(_git(repository, "cat-file", "blob", f"{revision}:{path}"))
    except (OSError, subprocess.SubprocessError):
        return None


def declared_options(definition: dict, limits: dict) -> list[str]:
    """The runner options the declared configuration forwards: the scenario harness's flags for the profile
    (`profiles.flags`, which a test keeps equal to these) and the task's declared iteration cap."""
    options = ["--provider", definition["provider"], "--joint-planning", *definition.get("extra", [])]
    for role, (model, effort) in ROLE_FLAGS.items():
        if definition["models"].get(role):
            options += [model, definition["models"][role]]
        if definition["effort"].get(role):
            options += [effort, definition["effort"][role]]
    if "max_iterations" in limits:
        options.append(f"--max-iterations={limits['max_iterations']:g}")
    return options


def _option(value):
    """A parsed runner option as text: empty for a bare flag, None when it is not given."""
    if value is None or value is False or value == []:
        return None
    if value is True:
        return ""
    return " ".join(sorted(value)) if isinstance(value, list) else value


def _option_deviations(selected: dict, declared: dict) -> list[str]:
    """Each forwarded option missing, added or set otherwise than the declared configuration sets it. The
    test command is not compared: the declaration leaves each case's test command to the evaluator."""
    found = []
    for name in declared:
        actual, expected = _option(selected[name]), _option(declared[name])
        if name == "test_command" or actual == expected:
            continue
        flag = "--" + name.replace("_", "-")
        if actual is None:
            found.append(f"{flag} {expected}".rstrip() + " missing")
        elif expected is None:
            found.append(f"{flag} {actual}".rstrip() + " added")
        else:
            found.append(f"{flag} {actual} instead of {expected}")
    return found


def arena_profile(options, declared: list[str]) -> dict:
    """The provider, and the model and effort AutoCode gives each role, under Arena's forwarded runner options,
    and each of those options that differs from `declared`, the options the declared configuration forwards."""
    if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
        raise LedgerError("Arena runner options must be a list of strings")
    try:
        selected = vars(_arena().runner_options(options))
    except ValueError as error:
        raise LedgerError(f"Arena runner options: {error}") from error
    try:
        expected = vars(_arena().runner_options(declared))
    except ValueError as error:
        raise LedgerError(f"Arena cannot forward the declared configuration: {error}") from error

    def first(flags):
        return next((value for flag in flags if (value := selected[flag[2:].replace("-", "_")]) is not None), None)

    models = {role: first(flags) for role, (flags, _) in EFFECTIVE_FLAGS.items()}
    effort = {role: first(flags) for role, (_, flags) in EFFECTIVE_FLAGS.items()}
    return {
        "provider": selected["provider"],
        "models": {role: value for role, value in models.items() if value is not None},
        "effort": {role: value for role, value in effort.items() if value is not None},
        "runner_options": options,
        "option_deviations": _option_deviations(selected, expected),
    }


def _arena_attempt(root: Path, attempt: str) -> tuple[dict, str]:
    if not ARENA_ATTEMPT.fullmatch(attempt):
        raise LedgerError(f"Arena attempt id {attempt!r} must be a simple name")
    database = root / "results.sqlite3"
    if not database.is_file():
        raise LedgerError(f"{database} is not an Arena attempt ledger")
    try:
        with contextlib.closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            saved = db.execute("SELECT payload FROM attempts WHERE id=?", (attempt,)).fetchone()
    except sqlite3.Error as error:
        raise LedgerError(f"{database}: {error}") from error
    if saved is None or not isinstance(saved[0], str):
        raise LedgerError(f"{database} holds no attempt {attempt}")
    payload = saved[0].encode()
    row = ledger.parse_json(payload, f"Arena attempt {attempt}")
    if row.get("id") != attempt:
        raise LedgerError(f"Arena attempt {attempt} is stored under another id")
    return row, _digest(payload)


def _arena_case(root: Path, row: dict, task: dict, label: str, oracle: tuple[str, str | None], revision: str) -> None:
    """The store's case for the attempt is the declared task: its id, base commit, required checks and oracle.

    The case is read as Arena's Store.case reads it: its recorded digest must still be the digest of its
    definition. `oracle` is the declared oracle's path and digest at the declared `revision`."""
    if row.get("case_id") != task["case_id"]:
        raise LedgerError(f"{label} is case {row.get('case_id')!r}, not the declared {task['case_id']!r}")
    path = root / "catalog.json"
    cases = ledger.parse_json(path.read_bytes(), str(path)).get("cases")
    found = [case for case in cases or [] if isinstance(case, dict) and case.get("id") == row["case_id"]]
    if len(found) != 1:
        raise LedgerError(f"{label}: {path} holds no single case {row['case_id']!r}")
    case = found[0]
    store = _arena_store()
    if store.digest(store.encode({key: value for key, value in case.items() if key != "sha256"}).encode()) != case.get(
        "sha256"
    ):
        raise LedgerError(f"{label}: case {case['id']} in {path} changed after it was admitted")
    if case["sha256"] != row.get("case_sha256"):
        raise LedgerError(f"{label}: {path} no longer holds the case definition it was attempted against")
    upstream = task.get("upstream") or {}
    same_base = case.get("base_commit") == upstream.get("base_commit")
    same_checks = sorted(map(str, case.get("required_checks") or [])) == sorted(upstream.get("checks") or [])
    if not (same_base and same_checks):
        raise LedgerError(
            f"{label} ran case {case['id']} at base {str(case.get('base_commit'))[:12]} with checks "
            f"{case.get('required_checks')}, not the declared base {str(upstream.get('base_commit'))[:12]} "
            f"and checks {upstream.get('checks')}"
        )
    oracle_path, declared = oracle
    if declared is None:
        raise LedgerError(
            f"{label}: cannot read {oracle_path} at the declared revision {revision[:12]} from local Git objects; "
            "the pinned commit must be present (git fetch --unshallow)"
        )
    if case.get("oracle_sha256") != declared:
        raise LedgerError(
            f"{label} was judged by an oracle (sha256 {str(case.get('oracle_sha256'))[:12]}) other than the "
            f"declared {oracle_path} (sha256 {declared[:12]})"
        )


def _arena_reason(row: dict, checks) -> str:
    verdict = row.get("verdict")
    if verdict == "RUNNING":
        return "Arena recorded no final verdict: the attempt is still running, or its runner stopped first"
    if row.get("error"):
        return f"Arena {verdict}: {row['error']}"
    text = f"Arena {verdict} at {row.get('runner_status')}"
    if checks:
        failing = [str(check["name"]) for check in checks if check["ok"] is not True]
        text += f"; oracle {len(checks) - len(failing)}/{len(checks)} checks"
        text += f", failing {', '.join(failing)}" if failing else ""
    return text


def arena_values(
    root: Path, attempt: str, task: dict, campaign: dict, identity, oracle: tuple[str, str | None]
) -> tuple[dict, str]:
    """A ledger row's values from an Arena attempt, and the key that identifies the record.

    The attempt ran the declared revision only when its runner identity is that revision's identity, and the
    declared task only when its case pins the declared base, checks and oracle (`declared_oracle`)."""
    row, payload_digest = _arena_attempt(root, attempt)
    label = f"Arena attempt {attempt}"
    _arena_case(root, row, task, label, oracle, campaign["source_revision"])
    checks = _checks(row.get("checks"), f"{label} checks")
    approved = (root / "attempts" / attempt / "approved-plan.txt").is_file()
    values = {
        "execution_kind": row.get("execution_kind"),
        "source_revision": campaign["source_revision"]
        if identity is not None and row.get("version_sha256") == identity
        else None,
        "actual_models_and_efforts": arena_profile(
            row.get("options"), declared_options(campaign["profile"]["definition"], task["limits"])
        ),
        "native_status": row.get("runner_status") or None,
        "native_outcome": row.get("verdict"),
        "oracle_checks": checks,
        "evidence_refs": [f"{label} in {root / 'results.sqlite3'} sha256:{payload_digest}"],
        "interventions_decision_or_repair": [
            {"kind": "decision", "note": "Arena approved the displayed benchmark plan on the person's behalf"}
        ]
        if approved
        else [],
        "elapsed_seconds": row.get("elapsed_seconds"),
        "usage": row.get("usage"),
        "estimated_usd": row.get("estimated_usd"),
        "spent_budget": dict.fromkeys(task["limits"]),
        "reason": _arena_reason(row, checks),
    }
    return values, f"{label} in "


def import_records(
    campaign: dict,
    campaign_sha256: str,
    document: dict,
    *,
    scenarios=(),
    arenas=(),
    runtime_identity=None,
    repository: Path,
) -> tuple[dict, dict]:
    """The ledger with each named record in its scheduled attempt's row, and the checked report of it.

    `scenarios` holds (schedule id, result path) pairs, `arenas` (schedule id, Arena directory, attempt id)
    triples. Raises LedgerError, changing nothing, when any record cannot be imported."""
    ledger.report(campaign, campaign_sha256, document)  # the ledger is sound before anything is added
    document = copy.deepcopy(document)
    tasks = {task["id"]: task for task in campaign["tasks"]}
    task_of = {row["id"]: tasks[row["task"]] for row in campaign["schedule"]}
    index = {row["schedule_id"]: number for number, row in enumerate(document["attempts"])}
    recorded = [ref for row in document["attempts"] for ref in row["evidence_refs"]]
    identity: list = []  # computed once, on the first Arena record
    filled = set()
    records: list[tuple[str, str, Path, Any]] = [("scenario", sid, Path(path), None) for sid, path in scenarios]
    records += [("arena", sid, Path(root), attempt) for sid, root, attempt in arenas]
    for kind, sid, path, attempt in records:
        if sid not in task_of:
            raise LedgerError(f"{sid!r} is not a scheduled attempt")
        if sid in filled:
            raise LedgerError(f"{sid} is named twice; one scheduled attempt has one record")
        current = document["attempts"][index[sid]]
        if current["native_outcome"] != ledger.DEFERRED:
            raise LedgerError(
                f"{sid} already records a {current['native_outcome']} attempt; a recorded attempt is never "
                "replaced (a retry needs a new declared schedule)"
            )
        task = task_of[sid]
        if task["kind"] != kind:
            raise LedgerError(f"{sid} is a task of kind {task['kind']}; a {kind} record cannot fill it")
        if kind == "scenario":
            values, key = scenario_values(path, task)
        else:
            if not identity:
                identity.append(revision_identity(repository, campaign["source_revision"]))
            oracle = declared_oracle(repository, campaign["source_revision"], task)
            values, key = arena_values(path, attempt, task, campaign, identity[0], oracle)
        if any(key in ref for ref in recorded):
            raise LedgerError(f"{sid}: {values['evidence_refs'][0]} is already in the ledger; it fills one row")
        values.update(schedule_id=sid, runtime_identity=copy.deepcopy(runtime_identity))
        document["attempts"][index[sid]] = {
            field: values[field] for field in campaign["acceptance"]["required_attempt_fields"]
        }
        recorded += values["evidence_refs"]
        filled.add(sid)
    return document, ledger.report(campaign, campaign_sha256, document)


def _write(path: Path, document: dict) -> None:
    """Replace the ledger in one step, keeping its permissions; the field order stays as declared."""
    data = json.dumps(document, indent=2, allow_nan=False) + "\n"
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fill campaign ledger rows from scenario and Arena result records.")
    parser.add_argument("campaign", type=Path)
    parser.add_argument("ledger", type=Path, help="the ledger to update in place")
    parser.add_argument(
        "--scenario",
        nargs=2,
        action="append",
        default=[],
        metavar=("SCHEDULE_ID", "RESULT"),
        help="a scenario run's result.json, or its evidence directory (repeat)",
    )
    parser.add_argument(
        "--arena",
        nargs=3,
        action="append",
        default=[],
        metavar=("SCHEDULE_ID", "ARENA", "ATTEMPT"),
        help="an attempt in the Arena store at ARENA (repeat)",
    )
    parser.add_argument(
        "--runtime-identity",
        type=Path,
        help="a JSON object recording the runtime versions and dependency freeze, added to every imported row",
    )
    args = parser.parse_args(argv)
    if not args.scenario and not args.arena:
        parser.error("name at least one record with --scenario or --arena")
    try:
        campaign, digest = ledger.load_campaign(args.campaign)
        document = ledger.parse_json(args.ledger.read_bytes(), "ledger")
        runtime = (
            ledger.parse_json(args.runtime_identity.read_bytes(), "runtime identity") if args.runtime_identity else None
        )
        updated, result = import_records(
            campaign,
            digest,
            document,
            scenarios=[tuple(item) for item in args.scenario],
            arenas=[tuple(item) for item in args.arena],
            runtime_identity=runtime,
            repository=args.campaign.resolve().parent,
        )
        _write(args.ledger, updated)
    except (OSError, LedgerError) as error:
        parser.exit(2, f"ledger error: {error}\n")
    imported = {sid for sid, *_ in [*args.scenario, *args.arena]}
    for row in result["rows"]:
        if row["schedule_id"] in imported:
            notes = "; ".join([*row["evidence_gaps"], *row["deviations"], *row["over_limits"]])
            print(
                f"{row['schedule_id']}: {row['native_outcome']} -> {row['outcome']}" + (f" ({notes})" if notes else "")
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
