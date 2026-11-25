#!/usr/bin/env python3
"""Choose how deep one tests-workflow run goes: the required gate or the full nightly run.

CI tooling only. ``gate`` is what ci-required waits on for a pull request, a merge-queue group
and a master push: every fast test module plus the slow ones the change touches, compared with
the event's pinned base, spread over ``--suite-shards`` runners, and the scenarios it selects over
``--gate-scenario-shards``. ``full`` (the nightly schedule,
a manual dispatch, or a push whose base cannot be read) runs every module and every scenario.

Outputs, for $GITHUB_OUTPUT: ``mode`` (gate or full), ``base`` (the gate's pinned base commit,
empty for full) and the JSON shard lists ``suite_shards`` and ``scenario_shards`` the jobs'
matrices read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

GATE_EVENTS = {"pull_request": "pr_base", "merge_group": "group_base", "push": "push_base"}
COMMIT = re.compile(r"[0-9a-f]{40}")


def depth(
    event,
    *,
    pr_base="",
    group_base="",
    push_base="",
    suite_shards=8,
    nightly_suite_shards=8,
    scenario_shards=6,
    gate_scenario_shards=4,
):
    """The run's mode, base and shard lists for one event."""
    bases = {"pr_base": pr_base, "group_base": group_base, "push_base": push_base}
    base = bases.get(GATE_EVENTS.get(event, ""), "") or ""
    # A push that created the branch reports an all-zero base: nothing to compare, so run everything.
    gate = event in GATE_EVENTS and bool(COMMIT.fullmatch(base)) and set(base) != {"0"}
    if event == "pull_request" and not gate:
        raise ValueError("a pull request needs its pinned 40-character base commit")
    suite = suite_shards if gate else nightly_suite_shards
    # A change to the shared scenario harness selects every scenario test, about 6.5 hours of them; on one
    # runner that held a pull request's gate for four hours. A shard with nothing selected finishes in seconds.
    scenarios = gate_scenario_shards if gate else scenario_shards
    if min(suite, scenarios) < 1:
        raise ValueError("shard counts must be at least 1")
    return {
        "mode": "gate" if gate else "full",
        "base": base if gate else "",
        "suite_shards": json.dumps(list(range(1, suite + 1))),
        "scenario_shards": json.dumps(list(range(1, scenarios + 1))),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event", required=True)
    parser.add_argument("--pr-base", default="")
    parser.add_argument("--group-base", default="")
    parser.add_argument("--push-base", default="")
    parser.add_argument("--suite-shards", type=int, default=8)
    parser.add_argument("--nightly-suite-shards", type=int, default=8)
    parser.add_argument("--scenario-shards", type=int, default=6)
    parser.add_argument("--gate-scenario-shards", type=int, default=4)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        outputs = depth(
            args.event,
            pr_base=args.pr_base,
            group_base=args.group_base,
            push_base=args.push_base,
            suite_shards=args.suite_shards,
            nightly_suite_shards=args.nightly_suite_shards,
            scenario_shards=args.scenario_shards,
            gate_scenario_shards=args.gate_scenario_shards,
        )
    except ValueError as error:
        print(f"ci_depth: {error}", file=sys.stderr)
        return 2
    print(json.dumps(outputs))
    if args.github_output:
        with args.github_output.open("a") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in outputs.items())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
