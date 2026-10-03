"""Run a scenario on the Claude models (Haiku builds, Sonnet plans and validates, Opus reviews).

Registers a `claude-tiers` profile in memory and calls the scenario harness, so the repository's
profiles stay unchanged. Spends real model money: it needs --i-authorize-live-model-spend.

    python3 examples/claude-provider/trial.py run bugfix-trivial --profile claude-tiers \
        --i-authorize-live-model-spend --out /path/to/results --timeout-minutes 45
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scenarios"))
import run  # noqa: E402
from harness import profiles  # noqa: E402

profiles.PROFILES["claude-tiers"] = {
    "provider": "claude",
    "models": {"requirements": "claude-sonnet-5-5", "planner": "claude-sonnet-5-5",
               "reviewer": "claude-opus-5-5", "builder": "claude-haiku-4-5-20251001",
               "validator": "claude-sonnet-5-5", "resolver": "claude-opus-5-5",
               "completion": "claude-opus-5-5"},
    "effort": {role: "medium" for role in profiles.ROLES},
    # A trial's guard rail is time: 20 minutes per stage here, and the harness's timeout per run. No token cap:
    # Claude reports every cache read as input, and a Builder re-reads its conversation on each tool call, so
    # a 6M cap stopped runs that had cost $4 (4 of 24 ladder rungs, 2026-09-30). Spend is in cost_usd.
    "extra": ["--max-stage-seconds", "1200"],
}
sys.exit(run.main(sys.argv[1:]))
