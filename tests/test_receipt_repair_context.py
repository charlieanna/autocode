"""Proposed synthetic tests; authored only, not executed for this proposal.

These use the public evidence-verification function, local synthetic receipt
bytes and pure projection functions. No process/provider or run-state fixture.
"""

import copy
import hashlib
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_receipt_repair_context as context
import autocode_support as support


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class ReceiptContextTests(unittest.TestCase):
    def test_snapshot_rejects_actual_oversize_without_using_stat(self):
        with self.assertRaisesRegex(ValueError, "byte bound"):
            context.receipt_snapshot(b" " * (context.MAX_RECEIPT_BYTES + 1))

    def test_snapshot_hashes_the_same_exact_bytes_it_parses(self):
        data = b'{"command": ["python", "-V"], "exit_code": 0}\n  '
        receipt, digest = context.receipt_snapshot(data)
        self.assertEqual(["python", "-V"], receipt["command"])
        self.assertEqual(sha256(data), digest)
        self.assertNotEqual(sha256(json.dumps(receipt).encode()), digest)

    def project(self, check=None, receipt=None):
        receipt = receipt or {
            "command": ["python", "-c", 'print("\\n")'],
            "exit_code": 7,
            "full_output_sha256": "b" * 64,
        }
        check = check or {
            "command": "python -c placeholder",
            "exit_code": 0,
            "evidence_ref": ".autocode/evidence/check.json",
        }
        return context.authenticated(
            check,
            receipt,
            authority="attempt_bound_capture",
            receipt_path="/project/.autocode/evidence/check.json",
            receipt_sha256="a" * 64,
            output_path="/project/.autocode/evidence/check.log",
            output_bytes=11,
        )

    def test_typed_authority_round_trips_literal_arguments_once(self):
        argv = ["python", "-c", 'print("\\n")', "literal\\backslash", "actual\nnewline", 'a "quote"']
        receipt = {"command": argv, "exit_code": 7, "full_output_sha256": "b" * 64}
        value = json.loads(json.dumps(self.project(receipt=receipt)))
        trusted = value["authenticated_receipt"]
        self.assertEqual(argv, trusted["argv"])
        self.assertEqual(argv, shlex.split(trusted["command"]))
        self.assertEqual(7, trusted["exit_code"])
        self.assertEqual({"path", "sha256", "bytes"}, set(trusted["full_output"]))
        self.assertNotIn("capture_context", trusted)
        self.assertNotIn("summary", trusted)

    def test_category_distinguishes_command_and_exit_without_claiming_live_cause(self):
        value = self.project()
        self.assertEqual("COMMAND_RECEIPT_MISMATCH", value["category"])
        self.assertEqual(["command_arguments", "exit_code"], value["mismatch_fields"])
        receipt = {"command": ["python", "-V"], "exit_code": 7}
        check = {"command": shlex.join(receipt["command"]), "exit_code": 0}
        self.assertEqual(["exit_code"], context.diagnostic(check, receipt)["mismatch_fields"])

    def test_large_command_is_omitted_whole_not_truncated(self):
        receipt = {
            "command": ["python", "x" * context.MAX_CONTEXT_BYTES],
            "exit_code": 0,
            "full_output_sha256": "b" * 64,
        }
        original = copy.deepcopy(receipt)
        value = self.project(receipt=receipt)
        self.assertIsNone(value["authenticated_receipt"])
        self.assertEqual("context_size_limit", value["omitted"])
        self.assertEqual(original, receipt)

    def test_argument_count_limit_is_honest_omission(self):
        receipt = {"command": ["x"] * (context.MAX_ARGUMENTS + 1), "exit_code": 0, "full_output_sha256": "b" * 64}
        value = self.project(receipt=receipt)
        self.assertIsNone(value["authenticated_receipt"])
        self.assertEqual("argument_count_limit", value["omitted"])

    def test_value_error_type_and_message_are_preserved(self):
        error = ValueError("original message")
        self.assertIs(error, context.attach(error, self.project()))
        self.assertIs(ValueError, type(error))
        self.assertEqual("original message", str(error))

    def test_retained_context_is_pinned_and_latest_nonreceipt_error_clears_it(self):
        pending = {"pins": {}}
        context.retain(pending, context.attach(ValueError("mismatch"), self.project()))
        value = context.handoff(pending)
        self.assertEqual("a" * 64, pending["pins"]["/project/.autocode/evidence/check.json"])
        self.assertEqual("b" * 64, pending["pins"]["/project/.autocode/evidence/check.log"])
        value["authenticated_receipt"]["argv"].append("cannot mutate retained context")
        self.assertNotEqual(value, context.handoff(pending))
        context.retain(pending, ValueError("new unrelated validation failure"))
        self.assertIsNone(context.handoff(pending))

    def test_conflicting_or_removed_pin_cannot_be_presented_as_authority(self):
        pending = {"pins": {"/project/.autocode/evidence/check.json": "c" * 64}}
        context.retain(pending, context.attach(ValueError("mismatch"), self.project()))
        self.assertIsNone(context.handoff(pending)["authenticated_receipt"])
        self.assertEqual("saved_pin_conflict", context.handoff(pending)["omitted"])
        fresh = {"pins": {}}
        context.retain(fresh, context.attach(ValueError("mismatch"), self.project()))
        fresh["pins"].pop("/project/.autocode/evidence/check.log")
        self.assertIsNone(context.handoff(fresh))

    def test_loaded_malformed_or_summary_context_is_not_handed_off(self):
        value = self.project()
        value["authenticated_receipt"]["summary"] = "invented PASS text"
        self.assertIsNone(context.handoff({"receipt_repair_context": value, "pins": {}}))
        self.assertIsNone(
            context.handoff(
                {
                    "receipt_repair_context": {
                        "category": context.CATEGORY,
                        "mismatch_fields": [{}],
                        "authenticated_receipt": None,
                    },
                    "pins": {},
                }
            )
        )

    def test_prompt_parts_without_authority_preserve_legacy_payload(self):
        diagnostic = context.diagnostic(
            {"command": "python -V"},
            {"command": ["python", "-V"], "exit_code": 0},
            omitted="receipt_context_size_limit",
        )
        for pending in ({}, {"receipt_repair_context": diagnostic, "pins": {}}):
            with self.subTest(pending=pending):
                self.assertEqual(("", {}), context.prompt_parts(pending))

    def test_prompt_parts_expose_only_pinned_authenticated_context(self):
        pending = {"pins": {}}
        context.retain(pending, context.attach(ValueError("mismatch"), self.project()))
        before = copy.deepcopy(pending)
        instruction, fields = context.prompt_parts(pending)
        self.assertEqual(context.INSTRUCTION, instruction)
        self.assertEqual({"command_receipt_repair_context"}, set(fields))
        trusted = fields["command_receipt_repair_context"]["authenticated_receipt"]
        self.assertEqual("attempt_bound_capture", trusted["authority"])
        self.assertEqual(7, trusted["exit_code"])
        self.assertEqual("a" * 64, trusted["receipt_sha256"])
        self.assertEqual("b" * 64, trusted["full_output"]["sha256"])
        self.assertEqual(before, pending)
        trusted["argv"].append("must not change retained context")
        self.assertEqual(before, pending)

    def test_prompt_parts_omit_conflicting_or_missing_authority_pins(self):
        conflicting = {"pins": {"/project/.autocode/evidence/check.json": "c" * 64}}
        context.retain(conflicting, context.attach(ValueError("mismatch"), self.project()))
        missing = {"pins": {}}
        context.retain(missing, context.attach(ValueError("mismatch"), self.project()))
        missing["pins"].pop("/project/.autocode/evidence/check.log")
        for pending in (conflicting, missing):
            with self.subTest(pending=pending):
                self.assertEqual(("", {}), context.prompt_parts(pending))


class VerifiedReceiptRepairContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        evidence = self.workspace / ".autocode" / "evidence"
        evidence.mkdir(parents=True)
        self.raw = evidence / "check.log"
        self.raw.write_bytes(b"synthetic output\n")
        self.path = evidence / "check.json"
        self.events = evidence / "events.jsonl"
        self.events.write_text("")
        self.capture_context = {"attempt": "synthetic", "source_revision": "synthetic-source", "nonce": "synthetic"}
        self.receipt = {
            "command": ["python", "-c", 'print("\\n")'],
            "exit_code": 7,
            "full_output": str(self.raw),
            "full_output_sha256": sha256(self.raw.read_bytes()),
            "capture_context": self.capture_context,
        }
        self.write_receipt()

    def write_receipt(self):
        self.path.write_text(json.dumps(self.receipt))

    def check(self, *, command=None, exit_code=0):
        return {
            "command": command or shlex.join(self.receipt["command"]),
            "exit_code": exit_code,
            "evidence_ref": str(self.path),
        }

    def reject(self, check, *, receipt_only=True, capture_context=None):
        checks = [check]
        original_checks = copy.deepcopy(checks)
        receipt_bytes = self.path.read_bytes()
        output_bytes = self.raw.read_bytes()
        with self.assertRaises(ValueError) as raised:
            support.verify_checks(
                checks,
                self.workspace,
                self.events,
                receipt_only=receipt_only,
                capture_context=self.capture_context if capture_context is None else capture_context,
            )
        self.assertIs(ValueError, type(raised.exception))
        self.assertEqual(original_checks, checks)
        self.assertEqual(receipt_bytes, self.path.read_bytes())
        self.assertEqual(output_bytes, self.raw.read_bytes())
        pending = {"pins": {}}
        context.retain(pending, raised.exception)
        return context.handoff(pending)

    def test_wrong_exit_still_rejects_and_exposes_authenticated_exact_exit(self):
        value = self.reject(self.check())
        self.assertEqual(["exit_code"], value["mismatch_fields"])
        trusted = value["authenticated_receipt"]
        self.assertEqual("attempt_bound_capture", trusted["authority"])
        self.assertEqual(self.receipt["command"], trusted["argv"])
        self.assertEqual(7, trusted["exit_code"])
        self.assertEqual(sha256(self.path.read_bytes()), trusted["receipt_sha256"])

    def test_equivalent_quoting_and_missing_exit_keep_existing_acceptance(self):
        argv = ["python", "-m", "unittest", "-p", "test_*.py"]
        self.receipt["command"] = argv
        self.write_receipt()
        checks = [self.check(command='python -m unittest -p "test_*.py"', exit_code=None)]
        before = self.path.read_bytes()
        support.verify_checks(
            checks, self.workspace, self.events, receipt_only=True, capture_context=self.capture_context
        )
        self.assertEqual(shlex.join(argv), checks[0]["command"])
        self.assertEqual(7, checks[0]["exit_code"])
        self.assertEqual(before, self.path.read_bytes())

    def test_changed_argument_or_placeholder_is_still_rejected(self):
        for command in ("python -c placeholder", "python -c '<combined assertions>'", "python -V"):
            with self.subTest(command=command):
                value = self.reject(self.check(command=command, exit_code=7))
                self.assertEqual(["command_arguments"], value["mismatch_fields"])
                self.assertEqual(self.receipt["command"], value["authenticated_receipt"]["argv"])

    def test_backslash_and_newline_arguments_are_not_unescaped_again(self):
        argv = ["python", "-c", 'print("\\n")', "actual\nnewline"]
        self.receipt["command"] = argv
        self.write_receipt()
        checks = [self.check(command=shlex.join(argv), exit_code=7)]
        support.verify_checks(
            checks, self.workspace, self.events, receipt_only=True, capture_context=self.capture_context
        )
        altered = ["python", "-c", 'print("\n")', "actual\nnewline"]
        value = self.reject(self.check(command=shlex.join(altered), exit_code=7))
        self.assertEqual(["command_arguments"], value["mismatch_fields"])

    def test_failed_output_hash_or_attempt_binding_never_exposes_receipt_authority(self):
        self.raw.write_bytes(b"changed output")
        value = self.reject(self.check())
        self.assertIsNone(value["authenticated_receipt"])
        self.assertEqual("receipt_not_authenticated", value["omitted"])
        self.raw.write_bytes(b"synthetic output\n")
        value = self.reject(self.check(), capture_context={"nonce": "different-attempt"})
        self.assertIsNone(value["authenticated_receipt"])

    def test_event_authority_requires_matching_completed_capture_output(self):
        value = self.reject(self.check(), receipt_only=False)
        self.assertIsNone(value["authenticated_receipt"])
        event = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "id": "synthetic-event",
                "command": "synthetic capture invocation",
                "exit_code": 7,
                "aggregated_output": json.dumps(self.receipt),
            },
        }
        self.events.write_text(json.dumps(event) + "\n")
        value = self.reject(self.check(), receipt_only=False)
        self.assertEqual("executed_capture_event", value["authenticated_receipt"]["authority"])

    def test_large_receipt_omits_context_without_changing_rejection(self):
        self.receipt["summary"] = "x" * context.MAX_RECEIPT_BYTES
        self.write_receipt()
        value = self.reject(self.check())
        self.assertIsNone(value["authenticated_receipt"])
        self.assertEqual("receipt_context_size_limit", value["omitted"])

    def test_growth_during_either_optional_read_is_bounded_and_omits_authority(self):
        # Synthetic receipt growth at the old stat/hash race boundary: appended
        # JSON whitespace preserves receipt semantics while exceeding the limit.
        # The second case grows between initial snapshot and post-auth snapshot.
        real_open = Path.open
        for grow_on in (1, 2):
            with self.subTest(grow_on=grow_on):
                self.write_receipt()
                binary_reads = []
                opened_receipt = 0

                class ReadProbe:
                    def __init__(probe, handle):
                        probe.handle = handle

                    def __enter__(probe):
                        probe.handle.__enter__()
                        return probe

                    def __exit__(probe, *args):
                        return probe.handle.__exit__(*args)

                    def read(probe, size=-1):
                        binary_reads.append(size)
                        return probe.handle.read(size)

                def open_with_growth(path, *args, **kwargs):
                    nonlocal opened_receipt
                    mode = args[0] if args else kwargs.get("mode", "r")
                    if path == self.path and mode == "rb":
                        opened_receipt += 1
                        if opened_receipt == grow_on:
                            with real_open(path, "ab") as writer:
                                writer.write(b" " * (context.MAX_RECEIPT_BYTES + 1))
                        return ReadProbe(real_open(path, *args, **kwargs))
                    return real_open(path, *args, **kwargs)

                checks = [self.check()]
                original = copy.deepcopy(checks)
                with patch.object(Path, "open", open_with_growth), self.assertRaises(ValueError) as raised:
                    support.verify_checks(
                        checks, self.workspace, self.events, receipt_only=True, capture_context=self.capture_context
                    )
                self.assertIs(ValueError, type(raised.exception))
                self.assertEqual(original, checks)
                self.assertEqual([context.MAX_RECEIPT_BYTES + 1] * grow_on, binary_reads)
                pending = {"pins": {}}
                context.retain(pending, raised.exception)
                self.assertIsNone(context.handoff(pending)["authenticated_receipt"])


if __name__ == "__main__":
    unittest.main()
