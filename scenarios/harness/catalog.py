"""The scenario catalog: one directory per scenario under ``scenarios/catalog/``.

    <id>/scenario.toml   title, category, requirements, fake-mode check, budgets, expected outcome,
                         and optional [[turn]] follow-up messages
    <id>/brief.md        the request given to AutoCode, verbatim
    <id>/seed/           starting project, committed before the run (optional)
    <id>/oracle.py       check(project, scenario[, run]) -> list[Check]
    <id>/reference/      overlay that makes a correct solution (optional)
    <id>/broken/<name>/  overlays that look plausible but are wrong (optional)
    <id>/hidden/         files only the oracle sees (optional)
"""
from __future__ import annotations

import importlib.util
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

CATALOG = Path(__file__).resolve().parent.parent / "catalog"
# The kind of engineering job. The first eight change or create code; the next
# four are read-only jobs whose deliverable is a report (README, "Workflows");
# a conversation moves one run between several of them, turn by turn.
CATEGORIES = ("bugfix", "feature", "greenfield", "port", "parallel", "architecture", "figma", "system",
              "review", "design", "discuss", "investigate", "conversation")
# How a correct run ends: with completion, with a stop (a blocker or a question
# the user must answer), or either.
EXPECTED = ("complete", "stop", "any")
KEYS = {"title", "category", "requires", "fake", "run", "turn"}
RUN_KEYS = {"max_steps", "timeout_minutes", "expected", "known_failure", "requires_stages"}
FAKE_KEYS = {"check", "flags", "fault", "live_investigator", "probe", "milestones", "turn_paths", "answers"}
# A follow-up turn is said to the same run once it reaches the state ``after``
# names: it completed, it stopped, or it is waiting on a particular need
# (``needs:answer``), in which case the message is said instead of the driver
# serving that need itself.
TURN_AFTER = ("complete", "stop")


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
    # [fake] answers: {question_id = answer} the person gives explicitly, for a question the driver
    # never answers by default (an AutoResolver stop such as a quota question, ``route-sol``).
    fake_answers: tuple[tuple[str, str], ...] = ()

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
        spec = importlib.util.spec_from_file_location(f"oracle_{self.id.replace('-', '_')}", self.dir / "oracle.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.check


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
    answers = fake.get("answers", {})
    if not isinstance(answers, dict) or not all(isinstance(value, str) and value.strip() for value in answers.values()):
        raise ValueError(f"{scenario_id}: [fake] answers maps question ids to nonempty answers")
    turns = []
    for number, turn in enumerate(meta.get("turn", []), start=1):
        if set(turn) != {"after", "say"} or not str(turn["say"]).strip():
            raise ValueError(f"{scenario_id}: [[turn]] {number} needs exactly `after` and a nonempty `say`")
        if turn["after"] not in TURN_AFTER and not str(turn["after"]).startswith("needs:"):
            raise ValueError(f"{scenario_id}: [[turn]] {number} after must be one of {TURN_AFTER} or needs:<kind>")
        turns.append(Turn(turn["after"], turn["say"].strip()))
    turn_paths = fake.get("turn_paths", [])
    if turn_paths and (len(turn_paths) != len(turns) + 1
                       or not all(isinstance(row, list) and row and all(
                           isinstance(p, str) and p and not p.startswith("/") and ".." not in p.split("/")
                           for p in row) for row in turn_paths)):
        raise ValueError(f"{scenario_id}: [fake] turn_paths needs one list of relative path prefixes per turn, "
                         f"the brief included ({len(turns) + 1})")
    # The scripted model tells turns apart by the message the handoff's task starts with, so a
    # message may not begin another (an identical one does) or the brief, which is turn 0's task.
    brief = (root / "brief.md").read_text().strip()
    says = [turn.say for turn in turns]
    if turn_paths and (any(i != j and says[j].startswith(says[i]) for i in range(len(says)) for j in range(len(says)))
                       or any(brief.startswith(say) for say in says)):
        raise ValueError(f"{scenario_id}: with [fake] turn_paths no turn's message may begin another's or the brief")
    return Scenario(
        id=scenario_id, dir=root, title=meta["title"], category=meta["category"],
        brief=brief, requires=tuple(meta.get("requires", ())),
        fake_check=meta.get("fake", {}).get("check"), max_steps=run.get("max_steps", 40),
        timeout_minutes=run.get("timeout_minutes", 60), expected=run.get("expected", "complete"),
        known_failure=run.get("known_failure", ""), fake_flags=tuple(fake.get("flags", ())),
        fake_fault=fake.get("fault", ""), fake_live_calls=bool(fake.get("live_investigator", False)),
        fake_probe=fake.get("probe", ""), turns=tuple(turns), requires_stages=tuple(run.get("requires_stages", ())),
        fake_milestones=tuple(fake.get("milestones", ())),
        fake_turn_paths=tuple(tuple(row) for row in turn_paths), fake_answers=tuple(sorted(answers.items())))


def load_all() -> list[Scenario]:
    return [load(path.name) for path in _dirs()]


def _dirs() -> list[Path]:
    return sorted(path for path in CATALOG.iterdir() if (path / "scenario.toml").is_file())
