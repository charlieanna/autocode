"""The scenario catalog: one directory per scenario under ``scenarios/catalog/``.

    <id>/scenario.toml   title, category, requirements, fake-mode check, budgets, expected outcome,
                         and optional [[turn]] follow-up messages
    <id>/brief.md        the request given to AutoCode, verbatim
    <id>/seed/           starting project, committed before the run (optional)
    <id>/oracle.py       check(project, scenario[, run]) -> list[Check], and optionally
                         diagnosis(project, run) -> dict, scored apart from the run verdict
    <id>/reference/      overlay that makes a correct solution (optional)
    <id>/broken/<name>/  overlays that look plausible but are wrong (optional)
    <id>/hidden/         files only the oracle sees (optional)
    <id>/tests/          fixtures for scenarios/test_harness.py, such as a live run's record (optional)

A scenario.toml may also declare a ``[hybrid]`` route (harness/hybrid.py): the stages a hybrid run scripts with
the fake provider and its fault while every other stage runs live.

A ``program`` scenario (category "program") is driven through ``autocode program`` by harness/program_driver.py
instead of as one run; its ``[program]`` table holds what the person does along the way (README, "Programs").
"""
from __future__ import annotations

import importlib.util
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .project import overlay_paths

CATALOG = Path(__file__).resolve().parent.parent / "catalog"
# The kind of engineering job. The first eight change or create code; the next
# four are read-only jobs whose deliverable is a report (README, "Workflows");
# a conversation moves one run between several of them, turn by turn; a program
# splits one request into workstreams, each its own run (README, "Programs").
CATEGORIES = ("bugfix", "feature", "greenfield", "port", "parallel", "architecture", "figma", "system",
              "review", "design", "discuss", "investigate", "conversation", "program")
# How a correct run ends: with completion, with a stop (a blocker or a question
# the user must answer), or either.
EXPECTED = ("complete", "stop", "any")
KEYS = {"title", "category", "requires", "fake", "run", "turn", "hybrid", "program"}
RUN_KEYS = {"max_steps", "timeout_minutes", "expected", "known_failure", "requires_stages"}
FAKE_KEYS = {"check", "flags", "fault", "live_investigator", "probe", "milestones", "turn_paths", "answers"}
# [hybrid] scripted: stages whose every call the fake provider answers in a hybrid run; first_attempt: stages
# whose first call it answers (later attempts, such as a repair Builder, are live).
HYBRID_KEYS = {"scripted", "first_attempt"}
# [program] max_parallel: `program run --max-parallel`; revise: the person's own edits, merged into the derived
# manifest before they first approve it (tables merge, anything else replaces); change: [[program.change]] steps.
PROGRAM_KEYS = {"max_parallel", "revise", "change"}
# A change request the person raises once ``after`` ("merged:<workstream>") holds, then decides: reject it with a
# resolution, or accept it by publishing the interface anew (publish holds the new fields, version included).
CHANGE_KEYS = {"after", "interface", "by", "reason", "decide", "resolution", "publish"}
# A follow-up turn is said to the same run once it completed: ``--follow-up``
# continues only a finished run (docs/cli.md, "Waiting or finished"). A waiting,
# paused or blocked run refuses it, so a turn after "stop" or a "needs:<kind>"
# could never be said; a job whose report asks questions completes, and the
# next turn is the reply.
TURN_AFTER = ("complete",)


@dataclass(frozen=True)
class Turn:
    after: str
    say: str


@dataclass(frozen=True)
class Scenario:
    id: str
    dir: Path
    title: str
    category: str
    brief: str
    requires: tuple[str, ...]
    fake_check: str | None
    max_steps: int
    timeout_minutes: int
    expected: str = "complete"
    # Why AutoCode is known not to pass this scenario yet. A verdict other than
    # PASS is reported but does not fail the suite; a PASS says to remove the key.
    known_failure: str = ""
    # [fake] extras: CLI flags added to the scripted run, a named scripted fault
    # (scenarios/harness/fake_codex.py), and whether the scripted run still makes a
    # real model call (then it needs --i-authorize-live-model-spend, or is skipped).
    fake_flags: tuple[str, ...] = ()
    fake_fault: str = ""
    fake_live_calls: bool = False
    fake_probe: str = ""  # exits 0 while the seed's bug is present: the runner checks the scripted reproduction
    # Follow-up messages, in order, each said to the same run (issue #51).
    turns: tuple[Turn, ...] = ()
    # Model stages the scenario exists to exercise. A run that never reaches one is
    # NOT_EXERCISED rather than passed: it says nothing about that stage.
    requires_stages: tuple[str, ...] = ()
    # [fake] milestones: multi-milestone scenarios (parallel-diamond) declare
    # each milestone's id, dependencies, owned paths and verify command so the
    # scripted provider can rehearse parallel scheduling without model spend.
    fake_milestones: tuple[dict, ...] = ()
    # [fake] turn_paths: in a conversation the solution is the end state of every turn; entry i lists
    # the solution path prefixes the scripted model delivers in turn i (0 = the brief).
    fake_turn_paths: tuple[tuple[str, ...], ...] = ()
    # [fake] answers: {question_id = answer} the person gives explicitly (README, "the driver answers").
    # The driver gives the explicit answer to each question that has one and AutoCode's proposed default to
    # the others: a question it never answers by default (an AutoResolver stop such as a quota question,
    # ``route-sol``), or an ordinary clarifying question whose answer the scenario tests
    # (design-alerting-outcomes). A gate the driver otherwise leaves to the person is served only when every
    # question on it has an explicit answer. A live run never gives them.
    fake_answers: tuple[tuple[str, str], ...] = ()
    # [hybrid]: the stages a hybrid run (run --hybrid) scripts; empty when the scenario declares no route.
    hybrid_scripted: tuple[str, ...] = ()
    hybrid_first_attempt: tuple[str, ...] = ()
    # [program] (category "program" only): the program run's parallelism, the person's edits to the derived
    # manifest, and the change requests they raise and decide.
    program_max_parallel: int = 2
    program_revise: dict = field(default_factory=dict)
    program_changes: tuple[dict, ...] = ()

    @property
    def seed(self) -> Path:
        return self.dir / "seed"

    @property
    def reference(self) -> Path | None:
        path = self.dir / "reference"
        return path if path.is_dir() else None

    @property
    def broken(self) -> list[Path]:
        root = self.dir / "broken"
        return sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []

    def missing_tools(self) -> list[str]:
        return [tool for tool in self.requires if shutil.which(tool) is None]

    def oracle(self):
        return self._oracle_module().check

    def diagnosis(self):
        """The oracle's optional ``diagnosis(project, run)``, or None (scenarios/README.md, "Diagnosis")."""
        return getattr(self._oracle_module(), "diagnosis", None)

    def _oracle_module(self):
        spec = importlib.util.spec_from_file_location(f"oracle_{self.id.replace('-', '_')}", self.dir / "oracle.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def load(scenario_id: str) -> Scenario:
    root = CATALOG / scenario_id
    if not (root / "scenario.toml").is_file():
        known = ", ".join(path.name for path in _dirs())
        raise ValueError(f"unknown scenario {scenario_id!r}; known: {known}")
    meta = tomllib.loads((root / "scenario.toml").read_text())
    unknown = set(meta) - KEYS
    if unknown:
        raise ValueError(f"{scenario_id}/scenario.toml: unknown keys {sorted(unknown)}")
    if meta.get("category") not in CATEGORIES:
        raise ValueError(f"{scenario_id}: category must be one of {CATEGORIES}")
    run = meta.get("run", {})
    unknown = set(run) - RUN_KEYS
    if unknown:
        raise ValueError(f"{scenario_id}/scenario.toml: unknown [run] keys {sorted(unknown)}")
    if run.get("expected", "complete") not in EXPECTED:
        raise ValueError(f"{scenario_id}: [run] expected must be one of {EXPECTED}")
    fake = meta.get("fake", {})
    unknown = set(fake) - FAKE_KEYS
    if unknown:
        raise ValueError(f"{scenario_id}/scenario.toml: unknown [fake] keys {sorted(unknown)}")
    hybrid = meta.get("hybrid", {})
    unknown = set(hybrid) - HYBRID_KEYS
    if unknown:
        raise ValueError(f"{scenario_id}/scenario.toml: unknown [hybrid] keys {sorted(unknown)}")
    stages = [hybrid.get(key, []) for key in sorted(HYBRID_KEYS)]
    if hybrid and (not all(isinstance(row, list) and all(isinstance(s, str) and s for s in row) for row in stages)
                   or not any(stages) or set(stages[0]) & set(stages[1])):
        raise ValueError(f"{scenario_id}: [hybrid] scripted and first_attempt are lists of stage names, together "
                         "not empty, with no stage in both")
    answers = fake.get("answers", {})
    if not isinstance(answers, dict) or not all(isinstance(value, str) and value.strip() for value in answers.values()):
        raise ValueError(f"{scenario_id}: [fake] answers maps question ids to nonempty answers")
    turns = []
    for number, turn in enumerate(meta.get("turn", []), start=1):
        if set(turn) != {"after", "say"} or not str(turn["say"]).strip():
            raise ValueError(f"{scenario_id}: [[turn]] {number} needs exactly `after` and a nonempty `say`")
        if turn["after"] not in TURN_AFTER:
            raise ValueError(f"{scenario_id}: [[turn]] {number} after must be \"complete\": a follow-up "
                             "continues only a finished run (docs/cli.md)")
        turns.append(Turn(turn["after"], turn["say"].strip()))
    turn_paths = fake.get("turn_paths", [])
    if turn_paths and (len(turn_paths) != len(turns) + 1
                       or not all(isinstance(row, list) and row and all(
                           isinstance(p, str) and p and not p.startswith("/") and ".." not in p.split("/")
                           for p in row) for row in turn_paths)):
        raise ValueError(f"{scenario_id}: [fake] turn_paths needs one list of relative path prefixes per turn, "
                         f"the brief included ({len(turns) + 1})")
    # The scripted model tells turns apart by the message the handoff's task starts with (it serves
    # turn_paths and per-turn reports by it), so a message may not begin another (an identical one
    # does) or the brief, which is turn 0's task.
    brief = (root / "brief.md").read_text().strip()
    says = [turn.say for turn in turns]
    if any(i != j and says[j].startswith(says[i]) for i in range(len(says)) for j in range(len(says))) \
            or any(brief.startswith(say) for say in says):
        raise ValueError(f"{scenario_id}: no turn's message may begin another's or the brief")
    program = _program(scenario_id, meta, fake, root)
    return Scenario(
        id=scenario_id, dir=root, title=meta["title"], category=meta["category"],
        brief=brief, requires=tuple(meta.get("requires", ())),
        fake_check=meta.get("fake", {}).get("check"), max_steps=run.get("max_steps", 40),
        timeout_minutes=run.get("timeout_minutes", 60), expected=run.get("expected", "complete"),
        known_failure=run.get("known_failure", ""), fake_flags=tuple(fake.get("flags", ())),
        fake_fault=fake.get("fault", ""), fake_live_calls=bool(fake.get("live_investigator", False)),
        fake_probe=fake.get("probe", ""), turns=tuple(turns), requires_stages=tuple(run.get("requires_stages", ())),
        fake_milestones=tuple(fake.get("milestones", ())),
        fake_turn_paths=tuple(tuple(row) for row in turn_paths), fake_answers=tuple(sorted(answers.items())),
        hybrid_scripted=tuple(hybrid.get("scripted", ())), hybrid_first_attempt=tuple(hybrid.get("first_attempt", ())),
        program_max_parallel=program.get("max_parallel", 2), program_revise=program.get("revise", {}),
        program_changes=tuple(program.get("change", ())))


def _program(scenario_id: str, meta: dict, fake: dict, root: Path) -> dict:
    """The validated [program] table ({} for any other category).

    The scripted model plans a program from [fake] milestones and each workstream delivers its milestone's
    paths, so the paths must be disjoint. A run that changes nothing stops for want of progress, so the
    reference and every broken overlay must be complete: every milestone path, and a file no milestone owns
    (the integration workstream's own delivery)."""
    program = meta.get("program", {})
    if meta["category"] != "program":
        if program:
            raise ValueError(f"{scenario_id}: a [program] table needs category = \"program\"")
        return {}
    unknown = set(program) - PROGRAM_KEYS
    if unknown:
        raise ValueError(f"{scenario_id}/scenario.toml: unknown [program] keys {sorted(unknown)}")
    if type(program.get("max_parallel", 2)) is not int or program.get("max_parallel", 2) < 1:
        raise ValueError(f"{scenario_id}: [program] max_parallel is a positive integer")
    if not isinstance(program.get("revise", {}), dict):
        raise ValueError(f"{scenario_id}: [program] revise is a table merged into the derived manifest")
    milestones = fake.get("milestones") or []
    ids = [row.get("id") for row in milestones]
    owned = [path for row in milestones for path in row.get("paths", [])]
    if not milestones or len(owned) != len(set(owned)):
        raise ValueError(f"{scenario_id}: a program scenario needs [fake] milestones whose paths are disjoint")
    broken = root / "broken"
    overlays = [root / "reference", *(sorted(broken.iterdir()) if broken.is_dir() else ())]
    for overlay in (path for path in overlays if path.is_dir()):
        name, files = overlay.relative_to(root).as_posix(), set(overlay_paths(overlay))
        missing = sorted(set(owned) - files)
        if missing:
            raise ValueError(f"{scenario_id}: {name}/ is not a complete overlay: it lacks milestone paths {missing}")
        if not files - set(owned):
            raise ValueError(f"{scenario_id}: {name}/ needs a file no milestone owns, which the integration "
                             "workstream delivers")
    for number, step in enumerate(program.get("change", []), start=1):
        where = f"{scenario_id}: [[program.change]] {number}"
        if not isinstance(step, dict) or set(step) - CHANGE_KEYS:
            raise ValueError(f"{where} takes only {sorted(CHANGE_KEYS)}")
        after = str(step.get("after", ""))
        if not after.startswith("merged:") or after.removeprefix("merged:") not in ids:
            raise ValueError(f"{where}: after is \"merged:<milestone id>\", one of {ids}")
        if not all(isinstance(step.get(key), str) and step[key].strip() for key in ("interface", "by", "reason")):
            raise ValueError(f"{where} needs a nonempty interface, by and reason")
        if step["by"] not in ids:
            raise ValueError(f"{where}: by names the workstream that found the problem, one of {ids}")
        if step.get("decide") == "reject":
            if not (isinstance(step.get("resolution"), str) and step["resolution"].strip()) or "publish" in step:
                raise ValueError(f"{where}: a rejection needs a resolution and publishes nothing")
        elif step.get("decide") == "accept":
            publish = step.get("publish")
            if (not isinstance(publish, dict) or type(publish.get("version")) is not int or publish["version"] < 2
                    or "resolution" in step):
                raise ValueError(f"{where}: an acceptance publishes the interface anew: publish = {{version = N, ...}}")
        else:
            raise ValueError(f"{where}: decide is \"accept\" or \"reject\"")
    return program


def load_all() -> list[Scenario]:
    return [load(path.name) for path in _dirs()]


def _dirs() -> list[Path]:
    return sorted(path for path in CATALOG.iterdir() if (path / "scenario.toml").is_file())
