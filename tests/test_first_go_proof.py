"""The first-Go allowance is bound to real captured baseline and candidate facts."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_first_go_proof as first_go
import autocode_go_tests as go_tests
import autocode_verify as verify
import autocode_verify_judgment as judgment


@unittest.skipUnless(
    all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")) and os.open in os.supports_dir_fd,
    "descriptor-bound evidence reads are unavailable",
)
class FirstGoBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="first-go-binding-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.framework = verify.Framework("go", "go test ./...")
        self.baseline = self.receipt(
            "baseline.log", "go test -json ./...", 'go: warning: "./..." matched no packages\nno packages to test\n', 1
        )
        self.candidate = self.receipt("candidate.log", "go test -json ./policy", self.events("pass"), 0)
        self.suite = {"base": "pinned", "command": "go test ./...", "receipt": self.baseline}

    @staticmethod
    def events(outcome):
        return "".join(
            json.dumps(row) + "\n"
            for row in (
                {"Action": "run", "Package": "policy", "Test": "TestNew"},
                {"Action": outcome, "Package": "policy", "Test": "TestNew"},
                {"Action": "pass", "Package": "policy"},
            )
        )

    def receipt(self, name, command, text, exit_code):
        data = text.encode()
        output = self.root / name
        output.write_bytes(data)
        return {
            "command": command,
            "exit_code": exit_code,
            "timed_out": False,
            "results_expected": True,
            "results": go_tests.results(text),
            "tail": text,
            "output": str(output),
            "output_sha256": hashlib.sha256(data).hexdigest(),
            "execution_root": str(self.root / "scratch" / name),
            "supervision": {"receipt": str(self.root / (name + ".supervision"))},
            "supervision_sha256": "0" * 64,
            "supervision_errors": [],
        }

    def eligible(self, **changes):
        args = {
            "base_suite": self.suite,
            "base": "pinned",
            "suite_command": "go test ./...",
            "candidate": self.candidate,
            "regression_command": "go test ./policy",
            "framework": self.framework,
            "root": self.root,
            "no_go_project": True,
        }
        args.update(changes)

        # Actual terminal process custody is independently exercised by the real-Go test.
        def bound(_out, check, **_kwargs):
            return {
                "pins": {check["supervision"]["receipt"]: check["supervision_sha256"]},
                "output_sha256": check["output_sha256"],
            }

        with (
            mock.patch.object(first_go.schedule, "intact", return_value=True),
            mock.patch.object(first_go.recovery, "bind_command", side_effect=bound),
        ):
            return first_go.absent(**args)

    def test_matching_pinned_captures_only(self):
        self.assertTrue(self.eligible())
        for change in (
            {"base": "other"},
            {"base": None},
            {"suite_command": "go test ./other"},
            {"regression_command": "go test ./other"},
            {"no_go_project": False},
            {"no_go_project": None},
            {"base_suite": None},
            {"candidate": None},
        ):
            with self.subTest(change=change):
                self.assertFalse(self.eligible(**change))
        with mock.patch.object(first_go.schedule, "intact", return_value=False):
            self.assertFalse(
                first_go.absent(
                    self.suite,
                    "pinned",
                    "go test ./...",
                    self.candidate,
                    "go test ./policy",
                    self.framework,
                    self.root,
                    no_go_project=True,
                )
            )

    def test_unknown_or_linked_inputs_cannot_establish_first_project(self):
        tree = self.root / "tree"
        tree.mkdir()
        (tree / "new_test.go").write_text("package sample\n")
        self.assertFalse(first_go.inputs_safe(tree, None))
        self.assertFalse(first_go.inputs_safe(None, None))
        clean = SimpleNamespace(recorded=True, unverified=[], generated={}, vendored={})
        self.assertTrue(first_go.inputs_safe(tree, clean))
        for supply in (
            SimpleNamespace(recorded=False, unverified=[], generated={}, vendored={}),
            SimpleNamespace(recorded=True, unverified=["missing"], generated={}, vendored={}),
            SimpleNamespace(recorded=True, unverified=[], generated={"extra.go": []}, vendored={}),
            SimpleNamespace(recorded=True, unverified=[], generated={}, vendored={"vendor/x.go": []}),
            SimpleNamespace(recorded=True, unverified=[]),
            SimpleNamespace(recorded=True, unverified=[], generated={}, vendored=None),
        ):
            with self.subTest(supply=supply):
                self.assertFalse(first_go.inputs_safe(tree, supply))
        link = tree / "alias"
        link.symlink_to(tree / "new_test.go")
        self.assertFalse(first_go.inputs_safe(tree, clean))
        link.unlink()
        for name in (".venv", "venv", "node_modules", "vendor"):
            directory = tree / "package" / name
            directory.mkdir(parents=True)
            with self.subTest(name=name):
                self.assertFalse(first_go.inputs_safe(tree, clean))
            directory.rmdir()

    def test_separate_worktree_requires_exact_prepared_inputs(self):
        tree = self.root / "tree"
        tree.mkdir()
        expected = {"new_test.go"}
        (tree / ".git").write_text("gitdir: original checkout metadata\n")
        (tree / "new_test.go").write_text("package sample\n")
        self.assertTrue(first_go.inputs_safe(tree, None, expected_files=expected))
        (tree / "generated.json").write_text("{}")
        self.assertFalse(first_go.inputs_safe(tree, None, expected_files=expected))

    def test_missing_tampered_or_unrelated_baseline_is_never_absence(self):
        for changes in (
            {"exit_code": 0},
            {"timed_out": True},
            {"output_truncated": True},
            {"tail": "no packages to test"},
            {"output_sha256": "f" * 64},
            {"results_expected": False},
            {"results": {"passed": ["forged"], "failed": [], "skipped": [], "complete": True}},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.eligible(base_suite={**self.suite, "receipt": {**self.baseline, **changes}}))
        incomplete = {key: value for key, value in self.baseline.items() if key != "supervision"}
        self.assertFalse(self.eligible(base_suite={**self.suite, "receipt": incomplete}))
        self.baseline["output"] = str(self.root.parent / "outside.log")
        self.assertFalse(self.eligible())

    def test_a_no_project_phrase_cannot_hide_an_unrelated_failure(self):
        unrelated = self.receipt(
            "unrelated.log", "go test -json ./...", "fatal toolchain error\nno packages to test\n", 1
        )
        self.assertFalse(self.eligible(base_suite={**self.suite, "receipt": unrelated}))

    def test_current_go_json_no_module_shape_is_accepted_but_mixed_errors_hold(self):
        rows = (
            {"ImportPath": "./...", "Action": "build-output", "Output": "# ./...\n"},
            {
                "ImportPath": "./...",
                "Action": "build-output",
                "Output": "pattern ./...: directory prefix . does not contain main module or its selected dependencies\n",
            },
            {"ImportPath": "./...", "Action": "build-fail"},
            {"Action": "start", "Package": "./..."},
            {"Action": "output", "Package": "./...", "Output": "FAIL\t./... [setup failed]\n"},
            {"Action": "fail", "Package": "./...", "FailedBuild": "./..."},
        )
        text = "".join(json.dumps(row) + "\n" for row in rows)
        observed = self.receipt("go-json.log", "go test -json ./...", text, 1)
        self.assertTrue(self.eligible(base_suite={**self.suite, "receipt": observed}))
        mixed = self.receipt("go-mixed.log", "go test -json ./...", text + "fatal compiler error\n", 1)
        self.assertFalse(self.eligible(base_suite={**self.suite, "receipt": mixed}))

    def test_candidate_must_run_complete_passing_named_tests_without_skips(self):
        for outcome, code in (("fail", 1), ("skip", 0)):
            with self.subTest(outcome=outcome):
                self.assertFalse(
                    self.eligible(
                        candidate=self.receipt("other.log", "go test -json ./policy", self.events(outcome), code)
                    )
                )
        for changes in ({"exit_code": 1}, {"timed_out": True}, {"output_truncated": True}, {"results": None}):
            with self.subTest(changes=changes):
                self.assertFalse(self.eligible(candidate={**self.candidate, **changes}))
        self.assertFalse(
            self.eligible(
                candidate={key: value for key, value in self.candidate.items() if key != "supervision_sha256"}
            )
        )
        without_run = "\n".join(self.events("pass").splitlines()[1:]) + "\n"
        self.assertFalse(self.eligible(candidate=self.receipt("no-run.log", "go test -json ./policy", without_run, 0)))

    def test_capture_rejects_unbound_admission_and_wrong_terminal_pin(self):
        with mock.patch.object(first_go.schedule, "intact", return_value=True):
            with mock.patch.object(
                first_go.recovery, "bind_command", side_effect=ValueError("wrong working directory")
            ):
                self.assertIsNone(first_go._capture(self.candidate, "go test -json ./policy", self.root))
            with mock.patch.object(
                first_go.recovery, "bind_command", return_value={"pins": {}, "output_sha256": "f" * 64}
            ) as bind:
                self.assertIsNone(first_go._capture(self.candidate, "go test -json ./policy", self.root))
                self.assertEqual(Path(self.candidate["execution_root"]), bind.call_args.kwargs["preparation_cwd"])

    def test_only_changed_passing_names_are_credited_without_base_rows(self):
        failures, unverified, notes, proof = [], [], [], {}
        with mock.patch.object(judgment.command_receipt, "completed", return_value=True):
            judgment.judge_regression(
                self.candidate,
                None,
                failures,
                unverified,
                notes,
                proof,
                [],
                known_failures=lambda: set(),
                new_behavior=True,
                absent_first_go=True,
                changed_tests=lambda name: name.endswith("::TestNew"),
            )
        self.assertEqual([], failures + unverified)
        self.assertEqual(["policy::TestNew"], proof["fail_to_pass"])
        self.assertEqual(proof["fail_to_pass"], proof["not_run_on_base"])
        self.assertEqual([], proof["pass_to_pass"])
        self.assertTrue(any("did not run on base" in note for note in notes))

    def test_missing_change_identity_or_candidate_skip_never_grants_first_go_credit(self):
        for candidate, changed in (
            (self.candidate, None),
            (self.candidate, lambda _: False),
            (
                {**self.candidate, "results": {**self.candidate["results"], "skipped": ["other::TestSkip"]}},
                lambda _: True,
            ),
            ({**self.candidate, "results": {**self.candidate["results"], "complete": False}}, lambda _: True),
        ):
            with self.subTest(candidate=candidate, changed=changed):
                failures, unverified, proof = [], [], {}
                with mock.patch.object(judgment.command_receipt, "completed", return_value=True):
                    judgment.judge_regression(
                        candidate,
                        None,
                        failures,
                        unverified,
                        [],
                        proof,
                        [],
                        known_failures=lambda: set(),
                        new_behavior=True,
                        absent_first_go=True,
                        changed_tests=changed,
                    )
                self.assertTrue(failures or unverified)
                self.assertFalse(proof.get("fail_to_pass"))


if __name__ == "__main__":
    unittest.main()
