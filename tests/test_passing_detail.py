"""A review handoff on a repository with thousands of tests counts its passing tests instead of listing them (#3095)."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_context as context
import autocode_passing_detail as passing_detail
import autocode_regression as regression
import autocode_stage_context as stage_context

from . import test_handoff_dedup as fixtures


def names(prefix, count):
    return [f"example.com/fulfillment-rules/{prefix}/TestRule{index:05d}/case_{index % 7}" for index in range(count)]


def proof():
    return {
        "verdict": "PASS",
        "fail_to_pass": ["example.com/fulfillment-rules/rule/TestSplitShipment"],
        "pass_to_pass": names("rule", 3000),
        "not_run_on_base": [],
        "unstable": [],
        "source_revision": "current-source",
    }


def replayed(packet):
    """The Validator's checks as the runner replayed them: two with long passing lists, one short."""
    checks = packet["validation"]["check_replay"]["checks"]
    checks[0]["results"] = {"passed": ["pkg.TestOne"], "failed": ["pkg.TestBroken"], "skipped": []}
    checks[1]["results"] = {
        "passed": names("rule/preflight", 5000),
        "failed": [],
        "skipped": ["example.com/fulfillment-rules/rule/preflight/TestNeedsDocker"],
        "collection_errors": [],
    }
    checks.append(
        {
            "command": "go test -json ./...",
            "exit_code": 0,
            "reported_exit_code": 0,
            "timed_out": False,
            "output": "/runs/r/check-replay/check-03/output.log",
            "output_sha256": "c" * 64,
            "results": {"passed": names("all", 12000), "failed": [], "skipped": []},
        }
    )
    return packet


def review_packet(stage="review_completion"):
    packet = replayed(fixtures.packet(stage))
    packet["regression_proof"] = proof()
    return packet


class PassingDetailTests(unittest.TestCase):
    def test_review_handoff_counts_long_passing_lists_and_keeps_them_whole_in_the_artifact(self):
        for stage in passing_detail.STAGES:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                original = review_packet(stage)
                before = copy.deepcopy(original)
                path = Path(tmp) / "state.json"
                small, moved = context.compact(original, path)
                self.assertEqual(before, original)
                self.assertEqual({passing_detail.PASS_TO_PASS, passing_detail.REPLAY_PASSED}, set(moved))
                self.assertLess(len(json.dumps(small)), len(json.dumps(original)) // 20)

                artifact = small["context_artifact"]
                raw = Path(artifact["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])
                saved = json.loads(raw)
                whole = before["validation"]["check_replay"]["checks"]
                self.assertEqual(before["regression_proof"]["pass_to_pass"], saved[passing_detail.PASS_TO_PASS])
                self.assertEqual(
                    {"1": whole[1]["results"]["passed"], "2": whole[2]["results"]["passed"]},
                    saved[passing_detail.REPLAY_PASSED],
                )

                inline = small["regression_proof"]
                self.assertEqual(
                    {"count": 3000, "details_artifact": {"field": passing_detail.PASS_TO_PASS}},
                    inline["pass_to_pass"],
                )
                for key in ("verdict", "fail_to_pass", "not_run_on_base", "unstable", "source_revision"):
                    self.assertEqual(before["regression_proof"][key], inline[key])
                self.assertIn(passing_detail.PASS_TO_PASS, inline["passing_detail_instruction"])

                replay = small["validation"]["check_replay"]
                rows = replay["checks"]
                # A short list stays as it was; a long one keeps everything but its passing names.
                self.assertEqual(whole[0]["results"], rows[0]["results"])
                for index, count in ((1, 5000), (2, 12000)):
                    self.assertEqual(
                        {
                            **whole[index]["results"],
                            "passed": {
                                "count": count,
                                "details_artifact": {"field": passing_detail.REPLAY_PASSED, "key": str(index)},
                            },
                        },
                        rows[index]["results"],
                    )
                self.assertEqual("/runs/r/check-replay/check-03/output.log", rows[2]["output"])
                self.assertEqual("c" * 64, rows[2]["output_sha256"])
                self.assertEqual(before["validation"]["check_replay"]["verdict"], replay["verdict"])
                instruction = replay["passing_detail_instruction"]
                self.assertIn("never permission to infer PASS", instruction)
                self.assertIn(passing_detail.REPLAY_PASSED, instruction)
                for key in ("goal_contract", "unresolved_findings", "human_reviews"):
                    self.assertEqual(before[key], small[key])
                self.assertEqual(small, context.compact(original, path)[0])
                self.assertEqual(small, context.compact(small, path)[0])

    def test_the_actual_completion_review_prompt_stays_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = replayed(fixtures.packet())
            state.update(
                version=3,
                task="Fix the split shipment rule",
                workspace=tmp,
                settings={
                    "roles": {r: {"engine": "codex", "model": "fake"} for r in ("task_planner", "builder", "tester")}
                },
                answers=state["saved_answers"],
            )
            whole = proof()
            with (
                patch.object(stage_context.support, "snapshot", return_value={"revision": "r", "head": "h"}),
                patch.object(regression, "handoff", return_value=whole),
            ):
                prompt, metrics = stage_context.context_packet(state, "review_completion", Path(tmp) / "state.json")
            self.assertLess(len(prompt.encode()), 200_000)
            self.assertEqual(
                {passing_detail.PASS_TO_PASS, passing_detail.REPLAY_PASSED}, set(metrics["externalized_fields"])
            )
            data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            saved = json.loads(Path(data["context_artifact"]["path"]).read_text())
            self.assertEqual(whole["pass_to_pass"], saved[passing_detail.PASS_TO_PASS])
            self.assertEqual(
                state["validation"]["check_replay"]["checks"][2]["results"]["passed"],
                saved[passing_detail.REPLAY_PASSED]["2"],
            )

    def test_tester_short_already_compacted_and_malformed_lists_stay_inline(self):
        tester = {"stage": "tester", "regression_proof": proof()}
        short = review_packet()
        short["regression_proof"]["pass_to_pass"] = names("rule", 20)
        for row in short["validation"]["check_replay"]["checks"]:
            row.pop("results", None)
        already = review_packet()
        already["context_artifact"] = {"path": "/saved/context.json", "sha256": "a" * 64}
        malformed = review_packet()
        malformed["regression_proof"]["pass_to_pass"] = "x" * 70000
        malformed["validation"]["check_replay"]["checks"] = [{"results": {"passed": "y" * 70000}}, "row", None]
        for original in (tester, short, already, malformed):
            with self.subTest(stage=original["stage"]), tempfile.TemporaryDirectory() as tmp:
                small, moved = context.compact(original, Path(tmp) / "state.json")
                self.assertNotIn(passing_detail.PASS_TO_PASS, moved)
                self.assertNotIn(passing_detail.REPLAY_PASSED, moved)
                self.assertEqual(
                    original["regression_proof"]["pass_to_pass"], small["regression_proof"]["pass_to_pass"]
                )
                for row, kept in zip(
                    original.get("validation", {}).get("check_replay", {}).get("checks", []),
                    small.get("validation", {}).get("check_replay", {}).get("checks", []),
                    strict=True,
                ):
                    self.assertEqual(
                        row.get("results") if isinstance(row, dict) else row,
                        kept.get("results") if isinstance(kept, dict) else kept,
                    )


if __name__ == "__main__":
    unittest.main()
