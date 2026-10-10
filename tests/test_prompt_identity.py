"""Public prompt identities stay historical and bind runtime/evidence bytes (#713)."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import autocode_arena as arena
import autocode_evidence_document as document
import autocode_evidence_export as export
import autocode_provider_launch as launch
import autocode_run_view as run_view
import autocode_usage as usage
import autocode_verify as verify
import reliability_table


def report(view):
    return document.build(
        view,
        run_identity="legacy",
        completed_at=None,
        provenance={"kind": "unknown", "basis": "unavailable", "declaration": "No declaration"},
        binding="legacy-binding",
    )


class PromptIdentityTests(unittest.TestCase):
    def test_status_uses_saved_identity_and_legacy_stays_unknown(self):
        state = {"status": "RUNNING", "prompts_hash": "a" * 64}
        before = deepcopy(state)
        with patch.object(launch.prompts, "HASH", "b" * 64):
            self.assertEqual("a" * 64, run_view.view(state)["prompts_hash"])
            self.assertIsNone(run_view.view({"status": "RUNNING"})["prompts_hash"])
        self.assertEqual(before, state)

    def test_new_attempts_record_current_set_without_rebinding_old_attempts(self):
        old = {"stage": "terra", "model": "old", "iteration": 1, "output": "old.json"}
        with patch.object(launch.prompts, "HASH", "b" * 64):
            current = {**old, "output": "new.json", **launch.stage_record({"engine": "codex"})}
        attempts = usage.accounting({"stages": [old, current]})["attempts"]
        self.assertNotIn("prompts_hash", attempts[0])
        self.assertEqual("b" * 64, attempts[1]["prompts_hash"])
        self.assertNotIn("prompts_hash", old)

    def test_report_binds_launch_and_actual_attempt_sets(self):
        view = run_view.view({"status": "TASK_COMPLETE", "prompts_hash": "a" * 64})
        view["usage"]["accounting"]["attempts"] = [{"stage": "sol", "model": "model", "prompts_hash": "b" * 64}]
        value = report(view)
        self.assertEqual("a" * 64, value["run"]["prompts_hash"])
        self.assertEqual("b" * 64, value["attempts"][0]["prompts_hash"])
        self.assertIn("a" * 64, document.render(value))
        self.assertIn("b" * 64, document.render(value))
        state, accounting = {"prompts_hash": "a" * 64}, view["usage"]["accounting"]
        bound = export.binding(state, accounting)
        self.assertNotEqual(bound, export.binding({"prompts_hash": "c" * 64}, accounting))
        changed = deepcopy(accounting)
        changed["attempts"][0]["prompts_hash"] = "c" * 64
        self.assertNotEqual(bound, export.binding(state, changed))
        with tempfile.TemporaryDirectory() as temporary:
            anchor = export.write(temporary, value)
            found = export.read(temporary, anchor, include=True)
            self.assertEqual("recorded", found["availability"])
            self.assertEqual(value, found["document"])

    def test_pre_refactor_reports_keep_exact_render_and_binding(self):
        # Captured from e18cf660 before extraction; older version-1 reports remain readable.
        value = report({"status": "TASK_COMPLETE", "workflow": "discuss"})
        del value["run"]["prompts_hash"]
        self.assertEqual(
            "cec625c179e2391313e8febb98ee36bfa1237f1da74b4dbac68df61d965b0c8f",
            hashlib.sha256(document.render(value).encode()).hexdigest(),
        )
        self.assertEqual(
            "90f5cf2babfe2da5796f5773eee656dfe0cbc42aa343abf8001f2917513b74a1",
            export.binding({"status": "TASK_COMPLETE", "settings": {}}, {}),
        )
        with tempfile.TemporaryDirectory() as temporary:
            anchor = export.write(temporary, value)
            found = export.read(temporary, anchor, current=True, include=True)
            self.assertEqual("current", found["availability"])
            self.assertNotIn("prompts_hash", found["document"]["run"])

    def test_report_rejects_invalid_prompt_identities(self):
        for bad in ("", "current", "a" * 63, "A" * 64, False):
            value = report({"status": "TASK_COMPLETE"})
            value["run"]["prompts_hash"] = bad
            with self.subTest(hash=bad), self.assertRaises(ValueError):
                document.validate(value)

    def test_arena_version_binds_resources_but_not_unrelated_markdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tools/prompts").mkdir(parents=True)
            (root / "tools/autocode.py").write_text("# runtime")
            (root / "pyproject.toml").write_text("# package")
            prompt = root / "tools/prompts/builder.md"
            prompt.write_text("original")
            original = arena.version_identity(root)
            (root / "tools/README.md").write_text("unrelated")
            self.assertEqual(original, arena.version_identity(root))
            prompt.write_text("changed")
            changed = arena.version_identity(root)
            self.assertNotEqual(original, changed)
            prompt.rename(root / "tools/prompts/tester.md")
            self.assertNotEqual(changed, arena.version_identity(root))

    def test_verification_runtime_binds_imported_prompt_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=T",
                    "-c",
                    "user.email=t@example.invalid",
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    "base",
                ],
                check=True,
            )
            with patch.object(verify.prompts, "CONTENTS", {"builder.md": b"original"}):
                original = verify.execution_identity(root, full=False)["runtime_sources"]
            with patch.object(verify.prompts, "CONTENTS", {"builder.md": b"changed"}):
                changed = verify.execution_identity(root, full=False)["runtime_sources"]
            with patch.object(verify.prompts, "CONTENTS", {"tester.md": b"original"}):
                renamed = verify.execution_identity(root, full=False)["runtime_sources"]
            self.assertNotEqual(original, changed)
            self.assertNotEqual(original, renamed)

    def test_sweep_rows_keep_hash_or_explicit_unknown(self):
        row = {
            "date": "date",
            "profile": "profile",
            "master": "master",
            "mode": "live",
            "runs": 1,
            "passed": 1,
            "false_completions": 0,
            "note": "note",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rows.json"
            path.write_text(json.dumps([row, {**row, "prompts_hash": "a" * 64}]))
            rows = reliability_table.load_rows(path)
            self.assertIsNone(rows[0]["prompts_hash"])
            self.assertEqual("a" * 64, rows[1]["prompts_hash"])
            shown = reliability_table.markdown(rows)
            self.assertIn("not recorded", shown)
            self.assertIn("a" * 64, shown)
            path.write_text(json.dumps([{**row, "prompts_hash": "bad"}]))
            with self.assertRaises(ValueError):
                reliability_table.load_rows(path)
