"""Script the faults "escalation_converts" and "escalation_exhausted" (#612): only the model is fake.

The greeting fix of completion-rework-direct: the seed's greet.py greets an empty name, and its immutable
test_greet.py catches that. Every Builder attempt on the route the Builder started on keeps the defect, so
the Tester's real check fails and the Builder's retry policy charges each failure: one ordinary retry, then
the stronger attempt on another route, then the safety pause.

- escalation_converts: an attempt on any other route (the stronger attempt) applies the configured
  solution, so the escalation converts only because the Builder climbed.
- escalation_exhausted: the stronger attempt keeps the defect too, so the run reaches the ladder's end,
  and the stuck-stage Investigator explains that pause (``investigate``) from the delivered greet.py.

The Tester, Completion Reviewer and Resolver are recovery_novelty_provider's: from the second failed build
the Completion Reviewer proposes a bounded correction, which AutoCode admits without another diagnosis.
Each later failure proposes a different one (ALTERNATIVES), as a reviewer reading a new failure would, so
the runner's novelty check never holds a repeat and the retry policy charges every failure.
"""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

# The guard each failed build's correction proposes, by the number of builds so far (the second build's first).
ALTERNATIVES = (
    "if len(sys.argv) != 2 or not sys.argv[1].strip():",
    "if len(sys.argv) != 2 or sys.argv[1].strip() == '':",
)
# The seed's guard: it checks the argument count and nothing else.
COUNT_ONLY = "if len(sys.argv) != 2:"


def _model() -> str | None:
    return sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv[:-1] else None


def _routes(trace: Path) -> list[str | None]:
    """The model of each Builder attempt so far, oldest first."""
    return [json.loads(line)["model"] for line in trace.read_text().splitlines()] if trace.exists() else []


def report_for(stage, data, common, config, run_check, requirements):
    here = Path(__file__).with_name
    trace = Path(os.environ["SCENARIO_FAKE_CONFIG"]).parent / "escalation-trace.jsonl"
    if stage == "terra":
        routes, model = _routes(trace), _model()
        stronger = bool(routes) and model != routes[0]
        with trace.open("a") as handle:
            handle.write(json.dumps({"model": model, "stronger": stronger}) + "\n")
        rework = runpy.run_path(str(here("completion_rework_provider.py")))
        broken = config["fault"] == "escalation_exhausted" or not stronger
        return rework["report_for"](stage, data, common, {**config, "builder_broken": broken}, run_check, requirements)
    novelty = runpy.run_path(str(here("recovery_novelty_provider.py")))
    result = novelty["report_for"](
        stage, data, common, {**config, "fault": "recovery_novelty_fix"}, run_check, requirements
    )
    change = result.get("recovery_change")
    if change:
        change["after"] = ALTERNATIVES[min(len(_routes(trace)) - 2, len(ALTERNATIVES) - 1)]
    return result


def investigate(data):
    """The stuck-stage Investigator's report on the retry-limit pause: a diagnosis read from the delivered
    greet.py. That pause is the person's to resume, so it recommends a pause and the runner keeps it, with
    this diagnosis added to its reason."""
    source = Path("greet.py").read_text() if Path("greet.py").is_file() else ""
    if COUNT_ONLY in source:
        diagnosis = (
            "greet.py still guards only the argument count (`if len(sys.argv) != 2:`): after the ordinary "
            "retry and the stronger attempt it still greets an empty or whitespace-only name, which "
            "test_greet.py's exact-stream cases require to print usage and exit 2. The Builder's attempts "
            "for this milestone are spent; a person decides whether to replan or retry it."
        )
    else:
        diagnosis = "The Builder's attempts for this milestone are spent; a person decides what comes next."
    return {
        "diagnosis": diagnosis,
        "cause": "stage_output",
        "guidance": "",
        "recommendation": "pause",
        "user_question": "",
        "evidence_refs": ["greet.py", "test_greet.py"],
        "example": "Given the name '', greet.py prints 'Hello, ' and exits 0 instead of usage and exit 2.",
        "probe": "",
        "untestable": "",
    }
