"""Capture consumer joins real original approval and stopped writer authority.

These controls do not capture a browser or inspect images. Planning and native
provider/kernel conformance use the existing explicitly controlled fixtures;
actual original journals, durable approval, held child/write/cleanup and source
replay are unchanged. No test method is invoked by another test.
"""

import json
import unittest
from copy import deepcopy

import autocode_contract_identity as contracts
import autocode_native_capture as capture
import autocode_source_scope as source_scope
import autocode_util as util
import autocode_visual_profile as profiles

from tests.test_native_check_execution import _ControlledNativeCheckFixture
from tests.test_native_writer import FirstWriterAuthorityTests
from tests.test_native_writer_execution import OriginalWriterTransportControls


def declare_profile(fixture):
    # Approved schema facts only; no SDK/transport/model validation is claimed.
    value = {
        "version": 1,
        "mode": "isolated-builtin-openai",
        "opencode_version": "1.18.33",
        "model": "openai/gpt-6-sol",
        "reasoning_effort": "high",
        "sdk_path": ".autocode/controlled-sdk",
        "sdk_manifest_sha256": "a" * 64,
        "transport_identity_sha256": "b" * 64,
        "executable_sha256": "c" * 64,
        "max_requests": 2,
        "image_limits": {"width": 320, "height": 240, "pixels": 76800, "bytes": 1048576},
    }
    goal = fixture.state["goal_contract"]
    goal["body"]["constraints"].append(profiles.MARKER + json.dumps(value))
    goal["hash"] = util.digest({key: goal[key] for key in ("task_id", "revision", "body")})
    fixture.token = contracts.token(goal)
    fixture.state["displayed_goal"] = fixture.state["planning"]["final_token"] = fixture.token
    fixture.owner._append(
        "planning-committed",
        {
            "contract": deepcopy(goal),
            "settings": deepcopy(fixture.state["settings"]),
            "source": deepcopy(fixture.raw),
            "token": fixture.token,
            "pins": {str(fixture.anchor): util.file_hash(fixture.anchor)},
        },
    )


class OriginalValidateCaptureAuthority(_ControlledNativeCheckFixture, unittest.TestCase):
    def test_original_validate_and_selected_source_freshness_remain_required(self):
        self.prepare()
        declare_profile(self)
        before = self.approve()
        execution = capture.NativeCaptureExecution(self.context, self.state, persist=self.sink.write)
        current, facts = execution._fresh()
        self.assertEqual(before, current)
        self.assertEqual(before, facts["binding"]["source"])
        (self.root / "unrelated.txt").write_text("unselected ignored file\n")
        self.assertEqual(before, execution._fresh()[0])
        (self.root / "ignored.txt").write_text("unapproved selected write\n")
        with self.assertRaises(util.Paused):
            execution._fresh()
        self.assertEqual(self.original_origin, self.original_origin_path.read_bytes())
        self.assertFalse(any(row["kind"] in ("check-prepared", "capture-published") for row in self.owner._rows()))


class OriginalStoppedWriterCaptureAuthority(unittest.TestCase):
    setUp = OriginalWriterTransportControls.setUp
    acknowledge = FirstWriterAuthorityTests.acknowledge
    commit = FirstWriterAuthorityTests.commit
    preparation = OriginalWriterTransportControls.preparation
    stopped = OriginalWriterTransportControls.stopped

    def prepare(self):
        declare_profile(self)
        return FirstWriterAuthorityTests.prepare(self)

    def test_capture_consumer_needs_successful_original_stopped_writer_and_exact_after(self):
        with self.assertRaisesRegex(util.Paused, "stopped first Builder"):
            capture.NativeCaptureExecution(self.context, self.state, persist=self.sink.write)
        protected = (self.root / "tracked.txt").read_bytes()
        _handle, after = self.stopped()
        self.assertNotEqual(self.before["revision"], after["revision"])
        execution = capture.NativeCaptureExecution(self.context, self.state, persist=self.sink.write)
        observed, facts = execution._fresh()
        self.assertEqual(after, observed)
        self.assertEqual(after, facts["binding"]["source"])
        self.assertEqual("bounded actual write\n", (self.root / "ignored.txt").read_text())
        self.assertEqual(protected, (self.root / "tracked.txt").read_bytes())
        self.assertEqual(self.origin_bytes, self.origin.read_bytes())
        self.assertEqual(1, sum(row["kind"] == "finished" for row in self.owner._rows()))
        (self.root / "ignored.txt").write_text("later unauthenticated write\n")
        self.assertNotEqual(after, source_scope.snapshot(self.root, self.state))
        with self.assertRaises(util.Paused):
            execution._fresh()
        self.assertFalse(any(row["kind"] in ("check-prepared", "capture-published") for row in self.owner._rows()))
