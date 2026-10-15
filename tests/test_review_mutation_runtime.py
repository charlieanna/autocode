"""Pure review-mutation custody, input selection and budget controls (#699).

Commands return synthetic, pinned native-shaped receipts; no project interpreter,
Git process, keeper, provider or wall-clock wait is started by this module.
"""

import difflib
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_command_receipt as receipts
import autocode_review_mutation as review
import autocode_util as util
import autocode_verification_preparation as preparation
import autocode_verification_prepare_worker as prepare_worker
import autocode_verify as verify

from .test_verification_preparation import PublicationFixture

BEFORE = b"def value(n):\n    untouched = n + 2\n    return n >= 1\n"
AFTER = b"def value(n):\n    untouched = n + 2\n    return n > 1\n"
TEST = b"import unittest\nclass Authored(unittest.TestCase):\n    def test_value(self): pass\n"
SELECTED = ["tests.test_added.Authored.test_value"]


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def no_run(reason="no_tests"):
    return {
        "schema": 1,
        "outcome": "not_run",
        "reason_code": reason,
        "project_commands_started": 0,
        "check": {
            "subject": "reviewed_patch_tests",
            "collected": False,
            "command_receipts": [],
        },
    }


def owned_receipt(out, index, *, passed=SELECTED, exit_code=0):
    """Write bounded receipt inputs; no process with these synthetic IDs is used."""
    directory = out / "command-supervision" / f"{index:032x}"
    directory.mkdir(parents=True)
    metadata = {
        "schema": 1,
        "nonce": f"{index + 100:032x}",
        "receipt": str(directory / "supervision.json"),
        "owner": {"pid": 2147480001, "birth_identity": 1},
        "keeper": {"pid": 2147480002, "birth_identity": 2},
        "provider": {"pid": 2147480003, "birth_identity": 3},
    }
    terminal = {
        **metadata,
        "phase": "stopped",
        "cause": "provider_stopped",
        "cleanup_error": None,
        "observed_at": "2026-10-15T00:00:00Z",
        "processes": [metadata["provider"]],
    }
    util.atomic_json(metadata["receipt"], terminal)
    output = out / f"command-{index}.log"
    output.write_text("synthetic collected test output\n")
    command = "python -m unittest -v tests/test_added.py"
    util.atomic_json(
        directory / "admission.json",
        {"schema": 1, "command": command, "cwd": str(out / "tree"), "output": str(output), "supervision": metadata},
    )
    return {
        "command": command,
        "exit_code": exit_code,
        "timed_out": False,
        "error": "",
        "duration_seconds": 0.0,
        "output": str(output),
        "output_sha256": util.file_hash(output),
        "supervision": metadata,
        "supervision_sha256": util.file_hash(metadata["receipt"]),
        "supervision_errors": [],
        "results": {"complete": True, "passed": list(passed), "failed": [], "skipped": []},
    }


class ScratchFixture:
    def __init__(self, root, changes=None):
        self.workspace, self.out, self.tree = root / "project", root / "evidence", root / "copy"
        self.workspace.mkdir(parents=True)
        self.out.mkdir()
        self.clock, self.calls, self.removed = Clock(), [], []
        self.changes = changes or {"app.py": (BEFORE, AFTER), "tests/test_added.py": (None, TEST)}
        sections = []
        for path, (before, after) in self.changes.items():
            if before is not None:
                self.write(self.workspace, path, before)
            old_name = "/dev/null" if before is None else "a/" + path
            new_name = "/dev/null" if after is None else "b/" + path
            body = "".join(
                difflib.unified_diff(
                    (before or b"").decode().splitlines(keepends=True),
                    (after or b"").decode().splitlines(keepends=True),
                    fromfile=old_name,
                    tofile=new_name,
                    n=3,
                )
            )
            # A context-only section is deliberately allowed in this fixture:
            # selection must follow effective bytes, not an asserted patch hunk.
            if not body:
                body = f"--- {old_name}\n+++ {new_name}\n@@ -1 +1 @@\n " + (before or b"").decode()
            sections.append(f"diff --git a/{path} b/{path}\n" + body)
        self.patch = self.workspace / "change.patch"
        self.patch.write_text("".join(sections))
        self.framework = verify.Framework("unittest", "python -m unittest discover -v", python="python")
        self.response = None
        self.prepared = None

    @staticmethod
    def write(root, relative, data):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def prepare(self, captured, before_patch):
        shutil.copytree(self.workspace, self.tree)
        self.prepared = captured.read_bytes()
        before_patch(self.tree)
        for path, (_, after) in self.changes.items():
            if after is None:
                (self.tree / path).unlink()
            else:
                self.write(self.tree, path, after)
        return self.tree

    def execute(self, framework, command, tree, out, label, *, timeout):
        call = {
            "command": command,
            "label": label,
            "timeout": timeout,
            "source": (tree / "app.py").read_bytes() if (tree / "app.py").exists() else None,
        }
        self.calls.append(call)
        answer = owned_receipt(out, len(self.calls))
        if self.response:
            changed = self.response(call, answer)
            if changed is not None:
                return changed
        return answer

    def remove(self, workspace, tree):
        self.removed.append((workspace, tree, (tree / "app.py").read_bytes() if (tree / "app.py").exists() else None))
        shutil.rmtree(tree)

    def run(self, **overrides):
        arguments = dict(
            patch=self.patch,
            deadline=900,
            prepare=self.prepare,
            detect=lambda tree: self.framework,
            execute=self.execute,
            remove=self.remove,
            is_test=verify.is_test_path,
            clock=self.clock,
        )
        arguments.update(overrides)
        return review.run_scratch(self.workspace, self.out, **arguments)


class ReviewMutationRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="review-mutation-runtime-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def fixture(self, changes=None):
        root = self.root / str(len(list(self.root.iterdir())))
        root.mkdir()
        return ScratchFixture(root, changes)

    def test_authored_patch_tests_only_and_effective_product_lines_reach_real_mutation_core(self):
        fixture = self.fixture(
            {
                "app.py": (BEFORE, AFTER),
                "neighbor.py": (b"return_value = 2\n", b"return_value = 2\n"),
                "tests/test_added.py": (None, TEST),
                "tests/test_deleted.py": (TEST, None),
                "review/test_F1.py": (None, TEST),
            }
        )
        fixture.write(fixture.workspace, "tests/test_ambient.py", TEST)
        envelope = fixture.run()
        check = review.validate(envelope, fixture.out)
        self.assertEqual("collected", envelope["outcome"])
        self.assertTrue(check["collected"], check)
        self.assertEqual(["tests/test_added.py"], check["selected_files"])
        self.assertEqual(SELECTED, check["selected_tests"])
        self.assertEqual(["app.py"], [row["path"] for row in check["files"]])
        mutants = check["files"][0]["mutants"]
        self.assertTrue(mutants)
        self.assertEqual({3}, {item["line"] for item in mutants})
        self.assertTrue(all(item["outcome"] == "survived" for item in mutants))
        self.assertEqual(set(SELECTED), set(check["survivors"]))
        self.assertEqual(len(fixture.calls), envelope["project_commands_started"])
        self.assertEqual(len(fixture.calls), len(check["command_receipts"]))
        self.assertIn("tests/test_added.py", fixture.calls[0]["command"])
        for call in fixture.calls:
            self.assertNotIn("test_ambient", call["command"])
            self.assertNotIn("test_F1", call["command"])
            self.assertNotIn("test_deleted", call["command"])
        self.assertEqual(["review-selection", "unmutated"], [call["label"] for call in fixture.calls[:2]])
        self.assertEqual(fixture.patch.read_bytes(), fixture.prepared)
        self.assertEqual([(fixture.workspace, fixture.tree, AFTER)], fixture.removed)
        self.assertEqual(BEFORE, (fixture.workspace / "app.py").read_bytes())
        self.assertFalse((fixture.workspace / "tests/test_added.py").exists())

    def test_no_run_is_an_explicit_absence_without_inner_exit_or_project_command(self):
        cases = (
            ({"patch": None}, "no_patch"),
            ({"deadline": 0}, "budget"),
            ({"detect": lambda tree: None}, "no_framework"),
            (
                {
                    "detect": lambda tree: SimpleNamespace(
                        name="pytest", targeted=lambda files: "pytest --unknown tests"
                    )
                },
                "unselectable",
            ),
        )
        for options, reason in cases:
            with self.subTest(reason=reason):
                fixture = self.fixture()
                envelope = fixture.run(**options)
                check = review.validate(envelope, fixture.out)
                self.assertEqual(
                    ("not_run", reason, 0),
                    (envelope["outcome"], envelope["reason_code"], envelope["project_commands_started"]),
                )
                self.assertFalse(check["collected"])
                self.assertEqual([], check["command_receipts"])
                self.assertNotIn("exit_code", check)
                self.assertEqual([], fixture.calls)

    def test_missing_authored_tests_product_or_mutation_sites_never_run_ambient_suite(self):
        cases = (
            ({"app.py": (BEFORE, AFTER)}, "no_tests"),
            ({"tests/test_added.py": (None, TEST)}, "no_product"),
            ({"app.py": (b"# before\n", b"# after\n"), "tests/test_added.py": (None, TEST)}, "no_sites"),
            (
                {"app.py": (BEFORE, AFTER), "review/test_F1.py": (None, TEST), "tests/test_deleted.py": (TEST, None)},
                "no_tests",
            ),
        )
        for changes, reason in cases:
            with self.subTest(reason=reason, changes=list(changes)):
                fixture = self.fixture(changes)
                fixture.write(fixture.workspace, "tests/test_ambient.py", TEST)
                envelope = fixture.run()
                self.assertEqual(("not_run", reason), (envelope["outcome"], envelope["reason_code"]))
                self.assertEqual([], fixture.calls)
                self.assertFalse(review.validate(envelope, fixture.out)["collected"])
                self.assertFalse(fixture.tree.exists())

    def test_nonpositive_file_discovery_has_no_mutant_or_fabricated_named_proof(self):
        for change in (
            {"results": {"complete": False, "passed": SELECTED}},
            {"results": {"complete": True, "passed": [], "skipped": SELECTED}},
            {"results": {"complete": True, "passed": ["tests.test_ambient.Case.test_other"]}},
            {"exit_code": 1},
        ):
            with self.subTest(change=change):
                fixture = self.fixture()
                fixture.response = lambda call, answer: {**answer, **change}
                envelope = fixture.run()
                self.assertEqual("selection_not_passed", envelope["reason_code"])
                self.assertEqual(1, len(fixture.calls))
                self.assertFalse(envelope["check"]["collected"])
                self.assertEqual([], envelope["check"]["files"])
                self.assertEqual({}, envelope["check"]["survivors"])
                self.assertEqual(AFTER, fixture.removed[0][2])

    def test_preparation_discovery_and_own_baseline_share_one_absolute_deadline(self):
        fixture = self.fixture()
        fixture.clock.now = 300

        def prepare(captured, before):
            tree = fixture.prepare(captured, before)
            fixture.clock.now += 100
            return tree

        def respond(call, answer):
            fixture.clock.now += 495 if call["label"] == "review-selection" else 5
            return answer

        fixture.response = respond
        envelope = fixture.run(prepare=prepare)
        self.assertEqual([500, 5], [call["timeout"] for call in fixture.calls])
        self.assertEqual(["review-selection", "unmutated"], [call["label"] for call in fixture.calls])
        self.assertEqual(900, fixture.clock.now)
        self.assertFalse(
            any(item["outcome"] == "survived" for row in envelope["check"]["files"] for item in row["mutants"])
        )
        self.assertEqual(AFTER, fixture.removed[0][2])

    def test_unknown_mutant_custody_retains_actual_mutant_and_holds_across_boundary(self):
        fixture = self.fixture()

        def respond(call, answer):
            if call["label"] not in ("review-selection", "unmutated"):
                raise receipts.OwnershipUncertain("keeper did not collect")
            return answer

        fixture.response = respond
        envelope = fixture.run()
        self.assertEqual("held", envelope["outcome"])
        self.assertFalse(envelope["check"]["collected"])
        self.assertEqual({}, envelope["check"]["survivors"])
        self.assertEqual([], fixture.removed)
        self.assertTrue(fixture.tree.is_dir())
        self.assertNotEqual(AFTER, (fixture.tree / "app.py").read_bytes())
        self.assertEqual(BEFORE, (fixture.workspace / "app.py").read_bytes())
        with self.assertRaises(receipts.OwnershipUncertain):
            review.validate(envelope, fixture.out)

    def test_project_commands_cannot_change_candidate_tests_or_product_and_gain_survivors(self):
        for stage in ("review-selection", "unmutated", "mutant"):
            for path in ("app.py", "tests/test_added.py"):
                with self.subTest(stage=stage, path=path):
                    fixture = self.fixture()
                    changed_at = []

                    def respond(call, answer):
                        reached = (
                            call["label"] == stage
                            if stage != "mutant"
                            else call["label"] not in ("review-selection", "unmutated")
                        )
                        if reached and not changed_at:
                            target = fixture.tree / path
                            target.write_bytes(target.read_bytes() + b"# command changed candidate\n")
                            changed_at.append(len(fixture.calls))
                        return answer

                    fixture.response = respond
                    envelope = fixture.run()
                    self.assertTrue(changed_at, fixture.calls)
                    self.assertFalse(envelope["check"]["collected"], envelope)
                    self.assertEqual({}, envelope["check"]["survivors"])
                    if stage != "mutant":
                        self.assertEqual(changed_at[0], len(fixture.calls), fixture.calls)
                    self.assertEqual(BEFORE, (fixture.workspace / "app.py").read_bytes())

    def test_captured_raw_patch_and_preimages_cannot_drift_before_copy_admission(self):
        for changed in ("raw_patch", "captured_patch", "preimage"):
            with self.subTest(changed=changed):
                fixture = self.fixture()
                binding = review.capture_inputs(fixture.workspace, fixture.patch, fixture.out)
                target = {
                    "raw_patch": fixture.patch,
                    "captured_patch": binding["captured_patch"],
                    "preimage": fixture.workspace / "app.py",
                }[changed]
                target.write_bytes(target.read_bytes() + b"# drift\n")
                with self.assertRaises(ValueError):
                    review.verify_inputs(binding, fixture.workspace)
                self.assertEqual([], fixture.calls)

    def test_unsafe_or_symlink_patch_inputs_cannot_reach_preparation(self):
        for path in ("../app.py", "/app.py", ".autocode/app.py", ".git/config", "tests/../app.py"):
            with self.subTest(path=path):
                fixture = self.fixture()
                fixture.patch.write_text(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n")
                prepare = Mock(side_effect=AssertionError("unsafe input reached preparation"))
                envelope = fixture.run(prepare=prepare)
                self.assertEqual("not_run", envelope["outcome"])
                self.assertFalse(prepare.called)
                self.assertEqual([], fixture.calls)
        fixture = self.fixture()
        target = fixture.workspace / "app.py"
        target.unlink()
        target.symlink_to(fixture.workspace / "change.patch")
        prepare = Mock()
        self.assertEqual("not_run", fixture.run(prepare=prepare)["outcome"])
        self.assertFalse(prepare.called)

    def test_no_run_and_all_executed_receipts_are_validated_not_only_the_last(self):
        fixture = self.fixture()
        envelope = no_run()
        self.assertFalse(review.validate(envelope, fixture.out)["collected"])
        for changed in (
            {"project_commands_started": 1},
            {"check": {**envelope["check"], "collected": True}},
            {
                "outcome": "collected",
                "project_commands_started": 1,
                "check": {**envelope["check"], "command_receipts": [{"exit_code": 0}]},
            },
        ):
            with self.subTest(changed=changed), self.assertRaises(receipts.OwnershipUncertain):
                review.validate({**envelope, **changed}, fixture.out)
        first, last = owned_receipt(fixture.out, 1), owned_receipt(fixture.out, 2)
        envelope.update(outcome="collected", project_commands_started=2)
        envelope["check"]["command_receipts"] = [first, last]
        self.assertIs(envelope["check"], review.validate(envelope, fixture.out))
        Path(first["output"]).write_text("changed earlier output; latest receipt remains valid\n")
        self.assertTrue(review.known_command(last, fixture.out))
        with self.assertRaises(receipts.OwnershipUncertain):
            review.validate(envelope, fixture.out)

    def test_only_collected_exits_and_closed_deadline_are_safe_nonpositive_observations(self):
        fixture = self.fixture()
        receipt = owned_receipt(fixture.out, 1, exit_code=1)
        self.assertTrue(review.known_command(receipt, fixture.out))
        terminal_path = Path(receipt["supervision"]["receipt"])
        terminal = util.read(terminal_path)
        util.atomic_json(terminal_path, {**terminal, "cause": "stage_deadline"})
        receipt.update(exit_code=None, timed_out=True, supervision_sha256=util.file_hash(terminal_path))
        self.assertTrue(review.known_command(receipt, fixture.out))
        for changed in ({"phase": "running"}, {"cleanup_error": "still alive"}, {"cause": "controller_interrupted"}):
            with self.subTest(changed=changed):
                util.atomic_json(terminal_path, {**terminal, "cause": "stage_deadline", **changed})
                receipt["supervision_sha256"] = util.file_hash(terminal_path)
                with self.assertRaises(receipts.OwnershipUncertain):
                    review.known_command(receipt, fixture.out)

    def test_public_wrapper_budget_includes_admission_and_propagates_held(self):
        fixture = self.fixture()
        clock = Clock(100)

        def allocate(out, *args):
            out.mkdir()
            clock.now += 300
            return {"path": "synthetic-only", "sha256": "0" * 64}

        def clean(workspace, out, **options):
            self.assertTrue(preparation.active())
            self.assertEqual(fixture.patch, options["patch"])
            self.assertEqual(
                (600, 1000, True), (options["timeout"], options["review_mutation_deadline"], options["review_mutation"])
            )
            return {"review_mutation": no_run()}

        with patch.object(preparation, "allocate", side_effect=allocate):
            check = review.collect(fixture.workspace, fixture.out, fixture.patch, clean, clock=clock)
        self.assertFalse(check["collected"])
        with patch.object(preparation, "allocate", side_effect=allocate):
            with self.assertRaises(receipts.OwnershipUncertain):
                review.collect(
                    fixture.workspace,
                    fixture.out,
                    fixture.patch,
                    Mock(return_value={"review_mutation": {**no_run(), "outcome": "held"}}),
                    clock=clock,
                )
        with patch.object(preparation, "allocate", side_effect=allocate):
            check = review.collect(
                fixture.workspace,
                fixture.out,
                fixture.patch,
                Mock(side_effect=ValueError("ordinary invalid input")),
                clock=clock,
            )
        self.assertFalse(check["collected"])
        self.assertIn("ordinary invalid input", check["note"])

    def test_shared_prepare_pins_one_base_and_checks_preimages_before_patch(self):
        fixture = self.fixture()
        fixture.tree.mkdir()
        supply = object()
        before = Mock(side_effect=ValueError("copied preimage drift"))
        with (
            patch.object(verify, "_git", return_value="base-pinned\n") as git,
            patch.object(verify, "changed_files", return_value={"app.py": "modified"}) as changes,
            patch.object(verify, "make_tree", return_value=fixture.tree) as make,
            patch.object(verify, "remove_tree") as remove,
            patch.object(verify.subprocess, "run") as apply,
        ):
            with self.assertRaisesRegex(ValueError, "copied preimage drift"):
                verify.prepare_scratch_tree(
                    fixture.workspace, fixture.out, patch=fixture.patch, ignored_inputs=supply, _before_patch=before
                )
        git.assert_called_once_with(fixture.workspace, "rev-parse", "HEAD")
        changes.assert_called_once_with(fixture.workspace, "base-pinned", source_paths=())
        self.assertEqual("base-pinned", make.call_args.args[1])
        self.assertIs(supply, make.call_args.kwargs["ignored_inputs"])
        before.assert_called_once_with(fixture.tree)
        remove.assert_called_once_with(fixture.workspace, fixture.tree)
        self.assertFalse(apply.called)


class ReviewMutationTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="review-mutation-transport-")
        self.addCleanup(temporary.cleanup)
        self.fixture = PublicationFixture(Path(temporary.name).resolve() / "obligations")
        self.fixture.prep.update(
            worker=str(Path(prepare_worker.__file__).resolve()), worker_sha256=util.file_hash(prepare_worker.__file__)
        )
        util.atomic_json(self.fixture.prep_path, self.fixture.prep)
        self.reference = {"path": str(self.fixture.prep_path), "sha256": util.file_hash(self.fixture.prep_path)}
        self.clock = Clock(100)
        self.envelope = no_run()
        self.outer = None
        self.calls = []

    def run_worker(self, command, cwd, output, *, timeout, env):
        """Run only the Python worker's request decoder against mocked execution."""
        self.calls.append((command, timeout))
        words = shlex.split(command)
        with (
            patch.object(sys, "argv", words[1:]),
            preparation.admitted(None),
            patch.object(verify, "scratch_run", return_value={"review_mutation": self.envelope}) as scratch,
        ):
            prepare_worker.main()
        request = util.read(words[3])
        self.assertEqual(
            (True, 900),
            (scratch.call_args.kwargs["review_mutation"], scratch.call_args.kwargs["review_mutation_deadline"]),
        )
        self.assertEqual(request["parameters"]["patch"], scratch.call_args.kwargs["patch"])
        answer = util.read(request["result"])
        self.assertIn("review_mutation", answer)
        self.assertNotIn("receipt", answer)
        output.write_text("synthetic outer worker exited after a typed result\n")
        result = self.fixture.original_receipt()["result"]
        result.update(command=command, output=str(output), output_sha256=util.file_hash(output))
        self.fixture.admission.update(command=command, cwd=str(cwd), output=str(output))
        util.atomic_json(self.fixture.admission_path, self.fixture.admission)
        return self.outer if self.outer is not None else result

    def launch(self):
        with (
            preparation.admitted(self.reference),
            patch.object(preparation.commands, "run", side_effect=self.run_worker),
            patch.object(preparation.time, "monotonic", self.clock),
        ):
            return preparation.launch(
                "execute",
                self.fixture.root.parent,
                self.fixture.out,
                {"patch": "change.patch", "review_mutation": True, "review_mutation_deadline": 900},
                timeout=900,
                env={},
            )

    def test_authenticated_outer_no_run_uses_typed_result_without_inner_fake_exit(self):
        answer = self.launch()
        self.assertEqual(
            self.envelope, {key: value for key, value in answer["review_mutation"].items() if key != "preparation"}
        )
        self.assertIn("preparation", answer["review_mutation"])
        review.validate(answer["review_mutation"], self.fixture.out)
        self.assertEqual(800, self.calls[0][1])
        self.assertNotIn("exit_code", self.envelope["check"])

    def test_zero_exit_outer_without_native_custody_cannot_authenticate_no_run(self):
        self.outer = {"exit_code": 0, "timed_out": False, "error": ""}
        with self.assertRaises(receipts.OwnershipUncertain):
            self.launch()

    def test_held_worker_result_crosses_transport_without_cleanup_or_retry(self):
        self.envelope = {**no_run(), "outcome": "held", "reason_code": "ownership_uncertain"}
        with patch.object(verify, "remove_tree") as remove:
            with self.assertRaises(receipts.OwnershipUncertain):
                self.launch()
        self.assertEqual(1, len(self.calls))
        self.assertFalse(remove.called)
        self.assertTrue((self.fixture.out / "scratch/tree").is_dir())

    def bound_inner(self, index):
        inner = owned_receipt(self.fixture.out, index)
        inner["supervision"]["owner"] = self.fixture.inner_metadata["owner"]
        terminal_path = Path(inner["supervision"]["receipt"])
        terminal = util.read(terminal_path)
        util.atomic_json(terminal_path, {**terminal, **inner["supervision"]})
        inner["supervision_sha256"] = util.file_hash(terminal_path)
        admission_path = terminal_path.with_name("admission.json")
        admission = util.read(admission_path)
        util.atomic_json(
            admission_path,
            {**admission, "cwd": str(self.fixture.out / "scratch/tree"), "supervision": inner["supervision"]},
        )
        return inner

    def test_parent_authenticates_every_inner_admission_and_owner_against_outer_worker(self):
        first, last = self.bound_inner(1), self.bound_inner(2)
        self.envelope.update(outcome="collected", project_commands_started=2)
        self.envelope["check"]["command_receipts"] = [first, last]
        self.launch()
        first_admission = Path(first["supervision"]["receipt"]).with_name("admission.json")
        admitted = util.read(first_admission)
        util.atomic_json(first_admission, {**admitted, "command": "different earlier command"})
        with self.assertRaises(receipts.OwnershipUncertain):
            self.launch()
        util.atomic_json(first_admission, admitted)
        first["supervision"]["owner"] = {"pid": 2147480010, "birth_identity": 10}
        terminal_path = Path(first["supervision"]["receipt"])
        util.atomic_json(terminal_path, {**util.read(terminal_path), **first["supervision"]})
        first["supervision_sha256"] = util.file_hash(terminal_path)
        util.atomic_json(first_admission, {**admitted, "supervision": first["supervision"]})
        with self.assertRaises(receipts.OwnershipUncertain):
            self.launch()

    def test_returned_preparation_provenance_cannot_be_rebound_after_collection(self):
        envelope = self.launch()["review_mutation"]
        review.validate(envelope, self.fixture.out)
        request = Path(envelope["preparation"]["request"])
        request.write_bytes(request.read_bytes() + b" \n")
        with self.assertRaises(receipts.OwnershipUncertain):
            review.validate(envelope, self.fixture.out)

    def test_ignored_supply_requires_explicit_transport_and_is_not_dropped(self):
        with preparation.admitted(self.reference), patch.object(preparation, "launch") as launch:
            with self.assertRaisesRegex(ValueError, "explicit launch-input Supply"):
                verify.scratch_run(
                    self.fixture.root.parent,
                    self.fixture.out,
                    patch="change.patch",
                    review_mutation=True,
                    review_mutation_deadline=900,
                    ignored_inputs=object(),
                )
        self.assertFalse(launch.called)
        encoded = {"schema": 1, "synthetic_supply": "must be decoded by worker"}
        supply = SimpleNamespace(to_transport=lambda: encoded)
        with patch.object(preparation, "launch", return_value={"review_mutation": no_run()}) as launch:
            preparation.execute(
                self.fixture.root.parent,
                self.fixture.out,
                {"ignored_inputs": supply},
                tests=(),
                command=None,
                timeout=600,
                review_mutation=True,
                review_mutation_deadline=900,
            )
        self.assertEqual(encoded, launch.call_args.args[3]["ignored_inputs"])
        self.assertEqual(
            (True, 900),
            (launch.call_args.args[3]["review_mutation"], launch.call_args.args[3]["review_mutation_deadline"]),
        )


if __name__ == "__main__":
    unittest.main()
