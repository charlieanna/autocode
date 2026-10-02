"""Plan the same build requests with today's pipeline and with adaptive planning.

``scenarios/planning.toml`` lists the requests. Each is planned once per variant,
in a fresh copy of its seed, and driven like a user would (questions answered with
AutoCode's proposed default) until AutoCode shows the plan for approval. Nothing is
built. The comparison reports, per request and variant, which stages ran, the
review calls and blocking concerns, the questions asked, tokens and model time,
and the plan's shape. It also writes each pair of final plans side by side under
neutral labels (``blind/``, key in ``blind/key.json``) so a reader can judge plan
quality without knowing which variant wrote which.

Like the rest of the harness this drives the CLI; ``state.json`` is read only after
a run stops, for evidence.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import catalog
from .driver import DriveError, Driver, fake_setup, live_setup, metrics
from .project import materialize

TABLE = Path(__file__).resolve().parent.parent / "planning.toml"
# Adaptive planning is the default for new runs; the fixed pipeline is now the opt-out.
VARIANTS = {"today": ["--no-adaptive-planning"], "adaptive": ["--adaptive-planning"]}
REVIEW_STAGES = ("astra_challenge", "astra_finalize")


@dataclasses.dataclass(frozen=True)
class Case:
    id: str
    seed: catalog.Scenario
    brief: str
    expect: dict
    fake: dict


def load(path: Path = TABLE) -> list[Case]:
    table = tomllib.loads(path.read_text())
    cases = []
    for row in table.get("case") or []:
        unknown = set(row) - {"id", "seed", "brief", "expect", "fake"}
        if unknown or not row.get("id") or not row.get("seed"):
            raise ValueError(f"{path}: case {row.get('id')!r} needs id and seed; unknown keys {sorted(unknown)}")
        seed = catalog.load(row["seed"])
        cases.append(Case(row["id"], seed, (row.get("brief") or seed.brief).strip(),
                          dict(row.get("expect") or {}), dict(row.get("fake") or {})))
    if len({case.id for case in cases}) != len(cases):
        raise ValueError(f"{path}: case ids must be unique")
    return cases


def plan_one(case: Case, variant: str, out: Path, *, fake: bool, profile: str | None, autocode: list[str],
             timeout_minutes: int, max_steps: int) -> dict:
    """Plan one request with one variant, stopping at the plan the user is asked to approve."""
    root = out / f"{case.id}-{variant}"
    root.mkdir(parents=True)
    project = materialize(case.seed.seed, root / "project")
    if fake:
        stand_in = dataclasses.replace(case.seed, brief=case.brief, fake_check=case.seed.fake_check or "true")
        flags, env = fake_setup(stand_in, root, case.seed.reference)
        env["SCENARIO_FAKE_CLARITY"] = case.fake.get("clarity", "clear")
        env["SCENARIO_FAKE_BLOCKING_REVIEW"] = str(case.fake.get("blocking_review", 0))
    else:
        flags, env = live_setup(profile)
    driver = Driver(project, root, [*flags, *VARIANTS[variant]], env, autocode=autocode,
                    max_steps=max_steps, timeout_seconds=60 * timeout_minutes)
    started, error, view = time.monotonic(), "", {}
    try:
        driver.call("start", task=case.brief)
        runs = project / ".autocode" / "runs"
        found = sorted(runs.glob("*/state.json"), key=lambda p: p.stat().st_mtime) if runs.is_dir() else []
        if not found:
            raise DriveError("the first CLI call did not create a run")
        driver.run_dir = found[-1].parent
        view = driver.until_stopped(say_at="needs:approve_plan")
    except DriveError as failure:
        error = str(failure)
    wall = round(time.monotonic() - started, 1)
    state = driver.state()
    if state:
        (root / "state.json").write_text(json.dumps(state, indent=2))
    record = summarize(state, driver.run_dir)
    record.update(case=case.id, variant=variant, wall_seconds=wall, error=error, evidence=str(root),
                  ended=(view.get("needs") or {}).get("kind") or state.get("status"),
                  answers=driver.answers, cli_calls=len(driver.steps))
    (root / "record.json").write_text(json.dumps(record, indent=2))
    return record


def _report(record: dict) -> dict:
    """A stage's saved report, read back from its output file."""
    try:
        return json.loads(Path(record.get("output") or "").read_text())
    except (OSError, ValueError):
        return {}


def summarize(state: dict, run_dir: Path | None) -> dict:
    """What a planning run did and produced, from its saved state."""
    measured = metrics(state)
    stages = [row for row in state.get("stages") or [] if not row.get("runner_owned")
              and row.get("stage") != "orchestrator"]
    reviews = []
    for row in stages:
        if row.get("stage") in REVIEW_STAGES:
            report = _report(row)
            concerns = report.get("concerns") or []
            reviews.append({"stage": row["stage"], "concerns": len(concerns),
                            "blocking": sum(1 for c in concerns if c.get("blocking")),
                            "unresolved": sum(1 for d in report.get("decisions") or [] if not d.get("resolved"))})
    body = ((state.get("goal_contract") or {}).get("body") or {})
    planning = state.get("planning") or {}
    return {
        "workflow": (state.get("workflow") or {}).get("kind"),
        "clarity": (state.get("workflow") or {}).get("clarity"),
        "adaptive": planning.get("adaptive"),
        "final_plan": bool(planning.get("final_token")),
        "model_stages": measured["model_stage_names"],
        "model_calls": measured["model_stages"],
        "report_repairs": measured["report_repairs"],
        "review_calls": sum(1 for row in stages if row.get("stage") in REVIEW_STAGES),
        "reviews": reviews,
        "tokens": measured["tokens"],
        "model_seconds": measured["model_seconds"],
        "plan": {"milestones": len(body.get("milestones") or []),
                 "acceptance_criteria": len(body.get("acceptance_criteria") or []),
                 "affected_paths": len({p for m in body.get("milestones") or [] for p in m.get("affected_paths") or []}),
                 "open_questions": len(body.get("open_blocking_questions") or []),
                 "initial_task": (body.get("initial_task") or {}).get("kind")},
        "contract": body,
    }


def render_plan(body: dict) -> str:
    """A final plan as a reader sees it: outcome, requirements, milestones, criteria, first task."""
    if not body:
        return "_No plan was produced._\n"
    lines = [f"**Outcome:** {body.get('intended_outcome', '')}", ""]
    for title, key in (("Required behaviors", "required_behaviors"), ("Failure cases", "important_failure_cases"),
                       ("Scope exclusions", "scope_exclusions"), ("Constraints", "constraints")):
        rows = body.get(key) or []
        if rows:
            lines += [f"**{title}:**", *[f"- {row}" for row in rows], ""]
    assumptions = body.get("accepted_assumptions") or []
    if assumptions:
        lines += ["**Assumptions:**", *[f"- {row.get('text', row) if isinstance(row, dict) else row}"
                                        for row in assumptions], ""]
    lines.append("**Milestones:**")
    for row in body.get("milestones") or []:
        lines.append(f"- {row.get('id')}: {row.get('objective')} (after {row.get('depends_on') or 'nothing'}; "
                     f"paths {', '.join(row.get('affected_paths') or [])})")
    lines += ["", "**Acceptance criteria:**"]
    for row in body.get("acceptance_criteria") or []:
        lines.append(f"- {row.get('id')}: {row.get('criterion')} — verified by: {row.get('verification_method')}")
    task = body.get("initial_task") or {}
    if task:
        lines += ["", f"**First task ({task.get('kind')}, {task.get('milestone_id')}):** {task.get('objective')}",
                  *[f"- validate: {row}" for row in task.get("validation_plan") or []]]
    questions = body.get("open_blocking_questions") or []
    if questions:
        lines += ["", "**Still open:**", *[f"- {row.get('question')}" for row in questions]]
    return "\n".join(lines) + "\n"


def write_blind(out: Path, cases: list[Case], records: dict) -> None:
    """Each case's two plans under neutral labels; the order is fixed by a hash of the case id."""
    blind = out / "blind"
    blind.mkdir(exist_ok=True)
    key = {}
    for case in cases:
        pair = [records.get((case.id, variant)) for variant in VARIANTS]
        if not all(pair):
            continue
        if int(hashlib.sha256(case.id.encode()).hexdigest(), 16) % 2:
            pair.reverse()
        key[case.id] = {"A": pair[0]["variant"], "B": pair[1]["variant"]}
        text = [f"# {case.id}", "", "## Request", "", case.brief, ""]
        for label, record in zip("AB", pair):
            text += [f"## Plan {label}", "", render_plan(record["contract"])]
        (blind / f"{case.id}.md").write_text("\n".join(text))
    (blind / "key.json").write_text(json.dumps(key, indent=2))


def table(cases: list[Case], records: dict) -> str:
    """The comparison as Markdown: one row per request and variant, then totals per variant."""
    lines = ["| Request | Variant | Ended | Clarity | Size | Model calls | Stages | Reviews (blocking) | "
             "Questions | Tokens in/out | Model min | Milestones | Criteria |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for case in cases:
        for variant in VARIANTS:
            row = records.get((case.id, variant))
            if not row:
                continue
            reviews = ", ".join(f"{r['stage'].split('_')[1]}:{r['concerns']}({r['blocking']})" for r in row["reviews"])
            stages = " → ".join(short(name) for name in row["model_stages"])
            lines.append(
                f"| {case.id} | {variant} | {row['ended']}{' ⚠ ' + row['error'][:60] if row['error'] else ''} | "
                f"{row['clarity'] or '–'} | {(row['adaptive'] or {}).get('size', '–')} | {row['model_calls']} | {stages} | "
                f"{reviews or '–'} | {len(row['answers'])} | {row['tokens']['input']:,}/{row['tokens']['output']:,} | "
                f"{row['model_seconds'] / 60:.1f} | {row['plan']['milestones']} | {row['plan']['acceptance_criteria']} |")
    lines += ["", "| Variant | Plans reached | Model calls | Review calls | Questions | Tokens in | Tokens out | Model min |",
              "|---|---|---|---|---|---|---|---|"]
    for variant in VARIANTS:
        rows = [records[(case.id, variant)] for case in cases if (case.id, variant) in records]
        if rows:
            lines.append(f"| {variant} | {sum(r['final_plan'] for r in rows)}/{len(rows)} | "
                         f"{sum(r['model_calls'] for r in rows)} | {sum(r['review_calls'] for r in rows)} | "
                         f"{sum(len(r['answers']) for r in rows)} | {sum(r['tokens']['input'] for r in rows):,} | "
                         f"{sum(r['tokens']['output'] for r in rows):,} | "
                         f"{sum(r['model_seconds'] for r in rows) / 60:.1f} |")
    return "\n".join(lines) + "\n"


SHORT = {"recognize_workflow": "recognize", "requirements_gather": "requirements", "astra_discovery": "plan",
         "astra_challenge": "review", "glm_revise": "revise", "astra_finalize": "finalize"}


def short(name: str) -> str:
    base = name.removesuffix("_report_repair")
    return SHORT.get(base, base) + ("(repair)" if name.endswith("_report_repair") else "")


def report(cases: list[Case], out: Path, records: dict) -> None:
    (out / "records.json").write_text(json.dumps({f"{c}/{v}": r for (c, v), r in records.items()}, indent=2))
    (out / "comparison.md").write_text(table(cases, records))
    write_blind(out, cases, records)


def rebuild(cases: list[Case], out: Path) -> dict:
    """Records from each run's own record.json, for a comparison that was cut short."""
    records = {}
    for path in sorted(out.glob("*/record.json")):
        record = json.loads(path.read_text())
        records[(record["case"], record["variant"])] = record
    report([case for case in cases if any(key[0] == case.id for key in records)], out, records)
    return records


def run(cases: list[Case], out: Path, *, jobs: int, **options) -> dict:
    """Plan every case with every variant, ``jobs`` at a time; return records keyed by (case, variant)."""
    work = [(case, variant) for case in cases for variant in VARIANTS]
    records = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(plan_one, case, variant, out, **options): (case.id, variant)
                   for case, variant in work}
        for future, key in futures.items():
            record = future.result()
            records[key] = record
            print(f"  {key[0]:<26} {key[1]:<9} {record['ended']!s:<14} calls={record['model_calls']:<3} "
                  f"reviews={record['review_calls']} questions={len(record['answers'])}"
                  + (f"  ERROR {record['error'][:80]}" if record["error"] else ""), flush=True)
    report(cases, out, records)
    return records
