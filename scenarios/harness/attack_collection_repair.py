"""Script a Builder that repairs its test only after the runner explains collection."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def loadable_greeting_tests(text):
    """Keep every assertion while deferring the two test-only imports to their tests."""
    anchors = ("from greet import greet\n", "    def test_greet(self):\n", "    def test_unicode(self):\n")
    if any(text.count(anchor) != 1 for anchor in anchors):
        raise ValueError("greeting test reference has unexpected structure")
    text = text.replace(anchors[0], "")
    text = text.replace(anchors[1], anchors[1] + "        from greet import greet\n")
    return text.replace(anchors[2], anchors[2] + "        from greet import greet\n")


def install(fake, attack, trace):
    if attack.get("case") != "repair_after_collection":
        raise ValueError("unknown collection repair scenario")
    original = fake.builder_copy_source
    original_report = fake.report_for
    active_stage = None

    def report(stage, data):
        nonlocal active_stage
        active_stage = stage
        try:
            return original_report(stage, data)
        finally:
            active_stage = None

    fake.report_for = report

    def source(data):
        findings = fake.runner_findings(data)
        if any("base test run could not collect" in row.get("finding", "").lower() for row in findings):
            if active_stage == "builder":
                trace("collection_feedback_observed", stage="builder")
            return os.environ["SCENARIO_FAKE_REWORK_SOLUTION"]
        # Do not apply the alternative on any other report or first attempt.
        saved = os.environ.pop("SCENARIO_FAKE_REWORK_SOLUTION", None)
        try:
            return original(data)
        finally:
            if saved is not None:
                os.environ["SCENARIO_FAKE_REWORK_SOLUTION"] = saved

    fake.builder_copy_source = source
    # This fixture writes only the two exact reference test forms below. Reuse
    # the existing scripted Tester's reasons only for the repaired bytes.
    import fake_changed_code

    reference = Path(os.environ["SCENARIO_FAKE_REWORK_SOLUTION"])
    expected = (
        "4611627c9ef9c3b8ae32bb1b4946ae4fa3c735aff704dd160a92ff2dbbdc1e70",
        "e4299f6a691ccd2cd73917b3f61b7175445b18cffe3f3e886a555078ab4dff52",
    )

    def hashes(root):
        return tuple(hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("greet.py", "test_greet.py"))

    if hashes(reference) != expected:
        raise ValueError("repaired collection fixture has unexpected bytes")
    if Path("greet.py").is_file() and Path("test_greet.py").is_file() and hashes(Path.cwd()) == expected:
        fake_changed_code.GREETING_REFERENCE = expected
