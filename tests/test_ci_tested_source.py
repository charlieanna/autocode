"""Attribution follows the tested Git parents, never a mutable master or stale event base."""

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import attribute_failure
import ci_failure_signatures
import ci_tested_source as source

HEAD, EVENT, TESTED, ACTUAL = (letter * 40 for letter in "abcd")


class SourceContextTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.spawn = self.enterContext(patch.object(subprocess, "Popen", side_effect=AssertionError("no process")))

    def tearDown(self):
        self.spawn.assert_not_called()

    def context(self, revision=TESTED, parents=(ACTUAL, HEAD), clean=True):
        return source.SourceContext(HEAD, EVENT, revision, parents, clean)

    def informational(self, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.fail(f"Unusable evidence must remain an informational refusal: {type(error).__name__}")

    def test_only_the_actual_ordered_source_pair_resolves(self):
        for revision, parents, clean, expected in (
            (TESTED, (ACTUAL, HEAD), True, ACTUAL),
            (HEAD, (ACTUAL,), True, EVENT),
            (HEAD, (), True, EVENT),
            (TESTED, (HEAD, ACTUAL), True, None),
            (TESTED, (ACTUAL,), True, None),
            (TESTED, (ACTUAL, HEAD, EVENT), True, None),
            (HEAD, (ACTUAL, EVENT, TESTED), True, None),
            (TESTED, (ACTUAL, EVENT), True, None),
            (TESTED, (ACTUAL, HEAD), False, None),
        ):
            with self.subTest(revision=revision, parents=parents, clean=clean):
                self.assertEqual(expected, self.context(revision, parents, clean).resolution()[0])

    def test_the_record_is_strict_and_bound_to_both_explicit_source_arguments(self):
        path = self.root / "source.json"
        original = self.context().as_dict()
        path.write_text(json.dumps(original))
        self.assertEqual(self.context(), source.SourceContext.load(path, expected_head=HEAD, event_base=EVENT))
        for field, value in (
            ("version", True),
            ("tracked_clean", 1),
            ("parents", [ACTUAL, True]),
            ("comparison_base", EVENT),
            ("reason", "untrusted arbitrary text"),
            ("error", "untrusted arbitrary text"),
        ):
            with self.subTest(field=field):
                path.write_text(json.dumps({**original, field: value}))
                with self.assertRaises((TypeError, ValueError)):
                    source.SourceContext.load(path, expected_head=HEAD, event_base=EVENT)
        path.write_text(json.dumps(original))
        for head, base in ((ACTUAL, EVENT), (HEAD, ACTUAL)):
            with self.subTest(head=head, base=base), self.assertRaises(ValueError):
                source.SourceContext.load(path, expected_head=head, event_base=base)

    def test_changed_or_dirty_source_cannot_be_repaired_into_a_resolved_capture(self):
        original = self.context()
        for current in (self.context(HEAD, (ACTUAL,)), self.context(clean=False)):
            with self.subTest(current=current), patch.object(source, "capture", return_value=current):
                checked = original.check_current(self.root)
                self.assertIsNone(checked.resolution()[0])
                self.assertEqual(source.CHANGED, checked.error)
        with patch.object(source, "capture", side_effect=AssertionError("do not repair an unresolved capture")):
            dirty = self.context(clean=False)
            self.assertIs(dirty, dirty.check_current(self.root))

    def test_capture_failure_is_informational_and_does_not_print_git_error_or_secret_text(self):
        output = self.root / "source.json"
        with (
            patch.object(source.subprocess, "run", side_effect=OSError("private-token-fixture")),
            contextlib.redirect_stdout(io.StringIO()) as shown,
        ):
            self.assertEqual(0, source.main(["--expected-head", HEAD, "--event-base", EVENT, "--out", str(output)]))
        self.assertNotIn("private-token-fixture", shown.getvalue())
        record = source.SourceContext.load(output, expected_head=HEAD, event_base=EVENT)
        self.assertIsNone(record.resolution()[0])

    def test_public_attribution_refuses_mismatched_or_changed_source_before_any_base_probe(self):
        context_path = self.root / "source.json"
        context_path.write_text(json.dumps(self.context().as_dict()))
        failed = self.root / "failed.json"
        failed.write_text(json.dumps({"version": 1, "failures": [{"module": "tests.test_observed", "failures": []}]}))
        for head, current in ((ACTUAL, self.context()), (HEAD, self.context(clean=False))):
            with (
                self.subTest(head=head),
                patch.object(source, "capture", return_value=current),
                patch.object(attribute_failure.subprocess, "run", side_effect=AssertionError("no base probe")) as probe,
                contextlib.redirect_stdout(io.StringIO()) as shown,
            ):
                self.assertEqual(
                    0,
                    attribute_failure.main(
                        [
                            "--base",
                            EVENT,
                            "--expected-head",
                            head,
                            "--tested-source",
                            str(context_path),
                            "--failed",
                            str(failed),
                        ]
                    ),
                )
                self.assertIn(attribute_failure.SOURCE_UNRESOLVED, shown.getvalue())
                probe.assert_not_called()

    def test_public_attribution_missing_or_invalid_context_is_unresolved_without_a_base_probe(self):
        path = self.root / "source.json"
        failed = self.root / "failed.json"
        failed.write_text(json.dumps({"version": 1, "failures": [{"module": "tests.test_observed", "failures": []}]}))
        for content in (None, "{partial", json.dumps({"version": True})):
            with self.subTest(content=content):
                if content is not None:
                    path.write_text(content)
                with (
                    patch.object(
                        attribute_failure.subprocess, "run", side_effect=AssertionError("no base probe")
                    ) as probe,
                    contextlib.redirect_stdout(io.StringIO()) as shown,
                ):
                    self.assertEqual(
                        0,
                        attribute_failure.main(
                            [
                                "--base",
                                EVENT,
                                "--expected-head",
                                HEAD,
                                "--tested-source",
                                str(path),
                                "--failed",
                                str(failed),
                            ]
                        ),
                    )
                self.assertIn(attribute_failure.SOURCE_UNRESOLVED, shown.getvalue())
                probe.assert_not_called()

    def test_missing_revision_requires_an_explicit_unavailable_observation(self):
        for parents, clean, error in (
            ((ACTUAL,), False, source.UNAVAILABLE),
            ((), True, source.UNAVAILABLE),
            ((), False, None),
        ):
            with self.subTest(parents=parents, clean=clean, error=error):
                with self.assertRaisesRegex(ValueError, "missing tested revision"):
                    source.SourceContext(HEAD, EVENT, None, parents, clean, error)
        unavailable = source.SourceContext(HEAD, EVENT, None, (), False, source.UNAVAILABLE)
        self.assertIsNone(unavailable.resolution()[0])

    def test_source_parent_inventory_must_be_a_bounded_list(self):
        path = self.root / "source.json"
        original = self.context().as_dict()
        for parents in (None, ACTUAL, {"parent": ACTUAL}, [ACTUAL] * 65):
            with self.subTest(parents=parents):
                path.write_text(json.dumps({**original, "parents": parents}))
                with self.assertRaisesRegex(ValueError, "invalid source parents"):
                    source.SourceContext.load(path, expected_head=HEAD, event_base=EVENT)

    def test_capture_rejects_oversized_or_failed_tracked_source_observation(self):
        valid_row = TESTED + " " + ACTUAL + " " + HEAD + "\n"
        cases = (
            [subprocess.CompletedProcess([], 0, stdout="private-token-fixture" * 500)],
            [subprocess.CompletedProcess([], 0, stdout=valid_row), subprocess.CompletedProcess([], 2)],
            [subprocess.CompletedProcess([], 0, stdout=valid_row), subprocess.CompletedProcess([], -15)],
        )
        for answers in cases:
            with self.subTest(answers=answers), patch.object(source.subprocess, "run", side_effect=answers) as command:
                record = self.informational(source.capture, HEAD, EVENT, repo=self.root)
                self.assertIsNone(record.tested_revision)
                self.assertFalse(record.tracked_clean)
                self.assertEqual((), record.parents)
                self.assertEqual(source.UNAVAILABLE, record.error)
                self.assertIsNone(record.resolution()[0])
                self.assertNotIn("private-token-fixture", json.dumps(record.as_dict()))
                self.assertEqual(len(answers), command.call_count)

    def test_capture_cli_errors_are_informational_and_do_not_expose_private_details(self):
        path = self.root / "private-output-token"
        path.mkdir()
        for capture_error in (None, ValueError("private-capture-token")):
            with (
                self.subTest(capture_error=capture_error),
                patch.object(source, "capture", return_value=self.context(), side_effect=capture_error) as capture,
                contextlib.redirect_stdout(io.StringIO()) as shown,
                contextlib.redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(
                    0,
                    self.informational(
                        source.main, ["--expected-head", HEAD, "--event-base", EVENT, "--out", str(path)]
                    ),
                )
                capture.assert_called_once_with(HEAD, EVENT)
                self.assertEqual("", shown.getvalue())
                self.assertEqual(
                    "Tested-source capture is unavailable; failure attribution will remain unresolved.\n",
                    errors.getvalue(),
                )
                self.assertNotIn("private-output-token", errors.getvalue())
                self.assertNotIn("private-capture-token", errors.getvalue())
                self.assertTrue(path.is_dir())

    def test_base_checkout_errors_keep_unfinished_head_and_base_uncertainty_separate(self):
        context = self.root / "source.json"
        context.write_text(json.dumps(self.context().as_dict()))
        failed, output = self.root / "failed.json", self.root / "result.json"
        failed.write_text(
            json.dumps(
                {
                    "version": 1,
                    "failures": [
                        {
                            "module": "tests.test_observed",
                            "timed_out": False,
                            "failures": [
                                {"test": "Observed.test_value", "kind": "failure", "signature": "observed-signature"}
                            ],
                        },
                        {"module": "tests.test_unfinished", "timed_out": True, "failures": [{"test": "partial"}]},
                    ],
                }
            )
        )
        for failed_call in (0, 1):
            for error in (
                OSError("private-checkout-token"),
                subprocess.CalledProcessError(1, ["git"], stderr=b"private-checkout-token"),
            ):
                answers = [subprocess.CompletedProcess([], 0)] * failed_call + [error]
                with (
                    self.subTest(failed_call=failed_call, error=error),
                    patch.object(source, "capture", return_value=self.context()),
                    patch.object(attribute_failure.subprocess, "run", side_effect=answers) as command,
                    patch.object(
                        attribute_failure, "classify", side_effect=AssertionError("no base test after Git failure")
                    ) as classify,
                    contextlib.chdir(self.root),
                    contextlib.redirect_stdout(io.StringIO()) as shown,
                ):
                    self.assertEqual(
                        0,
                        self.informational(
                            attribute_failure.main,
                            [
                                "--base",
                                EVENT,
                                "--expected-head",
                                HEAD,
                                "--tested-source",
                                str(context),
                                "--failed",
                                str(failed),
                                "--json",
                                str(output),
                            ],
                        ),
                    )
                    result = json.loads(output.read_text())
                    self.assertEqual("resolved", result["status"])
                    self.assertEqual(ACTUAL, result["source"]["comparison_base"])
                    self.assertEqual(
                        {
                            "tests.test_observed": attribute_failure.INCOMPLETE,
                            "tests.test_unfinished": attribute_failure.HEAD_INCOMPLETE,
                        },
                        result["modules"],
                    )
                    self.assertEqual(failed_call + 1, command.call_count)
                    self.assertNotIn("private-checkout-token", shown.getvalue())
                    self.assertNotIn("pre-existing", shown.getvalue())
                    classify.assert_not_called()


class TestedMergeGitTests(unittest.TestCase):
    """A tiny local repository and ordinary unittest children; no provider or Core process."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.enterContext(patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}))
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.test")
        self.git("config", "commit.gpgsign", "false")
        hooks = self.root / "empty-hooks"
        hooks.mkdir()
        self.git("config", "core.hooksPath", str(hooks))
        (self.root / "tests").mkdir()
        (self.root / "tests/__init__.py").touch()
        self.test_file = self.root / "tests/test_value.py"
        self.test_file.write_text(
            "import unittest\nclass ValueTests(unittest.TestCase):\n    def test_value(self):\n        pass\n"
        )
        self.git("add", "tests")
        self.git("commit", "-qm", "event base")
        self.event = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "fixture-pr")
        (self.root / "head.txt").write_text("PR-only file\n")
        self.git("add", "head.txt")
        self.git("commit", "-qm", "expected head")
        self.head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "fixture-advanced-base", self.event)
        self.test_file.write_text(
            "import unittest\nclass ValueTests(unittest.TestCase):\n    def test_value(self):\n        self.fail('base-only failure')\n"
        )
        self.git("add", "tests")
        self.git("commit", "-qm", "base advanced")
        self.actual_base = self.git("rev-parse", "HEAD")
        self.git("merge", "--no-ff", "-qm", "tested integration", "fixture-pr")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def observed_failure(self):
        record = source.capture(self.head, self.event, repo=self.root)
        self.context = self.root / "source.json"
        self.context.write_text(json.dumps(record.as_dict()))
        observed = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "tests.test_value"], cwd=self.root, capture_output=True, text=True
        )
        self.assertEqual(1, observed.returncode)
        self.failed = self.root / "head-failed.json"
        ci_failure_signatures.write(
            self.failed,
            [{"module": "tests.test_value", "ok": False, "output": observed.stdout + observed.stderr}],
            self.root,
        )
        return record

    def test_public_attribution_uses_the_tested_first_parent_even_when_event_base_passes(self):
        record = self.observed_failure()
        self.assertEqual(self.actual_base, record.resolution()[0])
        self.assertNotEqual(self.event, self.actual_base)
        # This is the old public API's actual stale-event-base outcome, not a
        # missing-helper/unsupported-flag control.
        with contextlib.chdir(self.root), contextlib.redirect_stdout(io.StringIO()) as old_api:
            self.assertEqual(0, attribute_failure.main(["--base", self.event, "--failed", str(self.failed)]))
        self.assertIn("introduced", old_api.getvalue())
        output = self.root / "attribution.json"
        with contextlib.chdir(self.root), contextlib.redirect_stdout(io.StringIO()) as contextual:
            self.assertEqual(
                0,
                attribute_failure.main(
                    [
                        "--base",
                        self.event,
                        "--expected-head",
                        self.head,
                        "--tested-source",
                        str(self.context),
                        "--failed",
                        str(self.failed),
                        "--json",
                        str(output),
                    ]
                ),
            )
        self.assertIn("pre-existing", contextual.getvalue())
        report = json.loads(output.read_text())
        self.assertEqual(self.actual_base, report["source"]["comparison_base"])
        self.assertEqual(self.event, report["source"]["event_base"])
        self.assertEqual(self.head, report["source"]["expected_head"])

    def test_tracked_source_changes_after_capture_leave_attribution_unresolved(self):
        self.observed_failure()
        self.test_file.write_text("# changed after the tested source was captured\n")
        with (
            contextlib.chdir(self.root),
            patch.object(attribute_failure, "classify", side_effect=AssertionError("no base tests")),
            contextlib.redirect_stdout(io.StringIO()) as shown,
        ):
            self.assertEqual(
                0,
                attribute_failure.main(
                    [
                        "--base",
                        self.event,
                        "--expected-head",
                        self.head,
                        "--tested-source",
                        str(self.context),
                        "--failed",
                        str(self.failed),
                    ]
                ),
            )
        self.assertIn(attribute_failure.SOURCE_UNRESOLVED, shown.getvalue())

    def test_capture_uses_its_explicit_checkout_despite_foreign_repository_variables(self):
        foreign = self.root / "foreign"
        subprocess.run(
            ["git", "clone", "--no-hardlinks", "-q", str(self.root), str(foreign)], check=True, capture_output=True
        )
        subprocess.run(["git", "-C", str(foreign), "checkout", "--detach", self.event], check=True, capture_output=True)
        tested = self.git("rev-parse", "HEAD")
        for name, value in (
            ("GIT_DIR", str(foreign / ".git")),
            ("GIT_WORK_TREE", str(foreign)),
            ("GIT_INDEX_FILE", str(foreign / ".git/index")),
        ):
            with self.subTest(variable=name), patch.dict(os.environ, {name: value}):
                ambient = dict(os.environ)
                captured = source.capture(self.head, self.event, repo=self.root)
                self.assertEqual(tested, captured.tested_revision)
                self.assertEqual(self.actual_base, captured.resolution()[0])
                self.assertEqual(
                    ambient, dict(os.environ), "explicit Git observation must not mutate caller configuration"
                )

    def test_public_base_probe_keeps_configuration_without_foreign_repository_ownership(self):
        self.git("checkout", "--detach", self.actual_base)
        self.test_file.write_text(
            "import os,subprocess,unittest\nclass ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual('present',os.environ.get('CI_ATTRIBUTION_FIXTURE'))\n"
            "        self.assertTrue(os.environ.get('PATH'))\n"
            "        self.assertEqual('kept',subprocess.check_output(['git','config','--get',"
            "'attribution.config-fixture'],text=True).strip())\n"
            "        subprocess.run(['git','config','attribution.probe-fixture','touched'],check=True)\n"
            "        self.fail('base-only failure')\n"
        )
        self.git("add", "tests")
        self.git("commit", "-qm", "base fixture uses its own Git configuration")
        self.actual_base = self.git("rev-parse", "HEAD")
        self.git("merge", "--no-ff", "-qm", "tested integration", "fixture-pr")
        configuration = {
            "CI_ATTRIBUTION_FIXTURE": "present",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "attribution.config-fixture",
            "GIT_CONFIG_VALUE_0": "kept",
        }
        with patch.dict(os.environ, configuration):
            self.observed_failure()
            foreign = self.root / "foreign"
            subprocess.run(
                ["git", "clone", "--no-hardlinks", "-q", str(self.root), str(foreign)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(foreign), "checkout", "--detach", self.event],
                check=True,
                capture_output=True,
            )
            original_head = subprocess.check_output(["git", "-C", str(foreign), "rev-parse", "HEAD"], text=True)
            original_config = hashlib.sha256((foreign / ".git/config").read_bytes()).hexdigest()
            redirects = {
                "GIT_DIR": str(foreign / ".git"),
                "GIT_WORK_TREE": str(foreign),
                "GIT_INDEX_FILE": str(foreign / ".git/index"),
            }
            with (
                patch.dict(os.environ, redirects),
                contextlib.chdir(self.root),
                contextlib.redirect_stdout(io.StringIO()) as shown,
            ):
                self.assertEqual(
                    0,
                    attribute_failure.main(
                        [
                            "--base",
                            self.event,
                            "--expected-head",
                            self.head,
                            "--tested-source",
                            str(self.context),
                            "--failed",
                            str(self.failed),
                        ]
                    ),
                )
            self.assertIn("pre-existing", shown.getvalue())
            self.assertEqual(
                original_head, subprocess.check_output(["git", "-C", str(foreign), "rev-parse", "HEAD"], text=True)
            )
            self.assertEqual(original_config, hashlib.sha256((foreign / ".git/config").read_bytes()).hexdigest())
