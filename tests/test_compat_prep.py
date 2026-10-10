"""Maintained compatibility-copy preparation (autocode_compat_prep).

Issue #223 follow-up: during the Headroom #3913 pilot the validation copy was
assembled from a detached HEAD alone, the candidate's new regression tests
never reached it, and the compatibility result looked green while the
candidate tests had never run. These tests build synthetic Git repositories,
prepare copies through the maintained helper and execute real suites inside
the prepared copies. Every negative control is a way a compatibility proof
could look complete without being so, and each must be rejected by execution,
not by reading a report. All credentials are synthetic strings; no network or
vendor call is ever made.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import autocode_compat_prep as compat  # noqa: E402
import autocode_verify as verify  # noqa: E402


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def git_bytes(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_files(files, root):
    for relative, body in files.items():
        target = Path(root) / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)


# --- pilot-shaped fixture: the credential flow in miniature ------------------
#
# tracker.py is the production file. The candidate's dirty change adds
# request_count(); the candidate's untracked tests/test_tracker_ops.py adds the
# T3/T5-shaped regressions. The overlay patch changes the notify_active API so
# only an explicitly qualified local operator's active credential is adopted.

TRACKER_HEAD = '''\
"""Synthetic subscription tracker: the pilot's credential flow in miniature.

Every credential below is a synthetic string; no real vendor call is ever made.
"""
import json
from pathlib import Path

CONFIG_FILE = Path("configured-credential.json")
SYNTHETIC = {"configured": "SYNTHETIC-CONFIGURED-TOKEN",
             "active": "SYNTHETIC-ACTIVE-TOKEN",
             "learned": "SYNTHETIC-LEARNED-TOKEN"}

state = {"requests": [], "adopted": []}


def reset():
    state["requests"] = []
    state["adopted"] = []


'''

REQUEST_COUNT = '''\
def request_count():
    """Candidate change: how many usage requests were recorded so far."""
    return len(state["requests"])


'''

TRACKER_FUNCTIONS = '''\
def configured_credential():
    """The configured credential, or None when no credential file exists."""
    if not CONFIG_FILE.is_file():
        return None
    return json.loads(CONFIG_FILE.read_text())["token"]


def _record(token):
    state["requests"].append(token)


'''

NOTIFY_ACTIVE_BASE = '''\
def notify_active(token):
    """Pre-overlay API: the active bearer credential is adopted."""
    credential = configured_credential()
    if credential is None:
        state["adopted"].append(token)
        _record(token)
        return token
    _record(credential)  # configured-first; rejected in this fixture
    state["adopted"].append(token)
    _record(token)
    return token
'''

NOTIFY_ACTIVE_OVERLAY = '''\
def notify_active(token, *, from_local_operator=False):
    """Overlaid API: only the local operator's active credential is adopted."""
    credential = configured_credential()
    if credential is None:
        if not from_local_operator:
            return None
        state["adopted"].append(token)
        _record(token)
        return token
    _record(credential)  # configured-first; rejected in this fixture
    if from_local_operator:
        state["adopted"].append(token)
        _record(token)
        return token
    return credential
'''

PILOT_TRACKER = TRACKER_HEAD + TRACKER_FUNCTIONS + NOTIFY_ACTIVE_BASE
CANDIDATE_PILOT_TRACKER = TRACKER_HEAD + REQUEST_COUNT + TRACKER_FUNCTIONS + NOTIFY_ACTIVE_BASE

PILOT_TESTS = '''\
"""Original guard tests for the synthetic tracker (committed at base)."""
import json
import unittest

import tracker
from tracker import SYNTHETIC, notify_active, reset, state


class TrackerGuards(unittest.TestCase):
    def setUp(self):
        tracker.CONFIG_FILE.unlink(missing_ok=True)
        reset()

    def tearDown(self):
        tracker.CONFIG_FILE.unlink(missing_ok=True)

    def test_reset_clears_recorded_state(self):
        notify_active("Bearer " + SYNTHETIC["active"])
        reset()
        self.assertEqual([], state["requests"])
        self.assertEqual([], state["adopted"])

    def test_configured_credential_reads_the_credential_file(self):
        self.assertIsNone(tracker.configured_credential())
        tracker.CONFIG_FILE.write_text(json.dumps({"token": SYNTHETIC["configured"]}))
        self.assertEqual(SYNTHETIC["configured"], tracker.configured_credential())

    def test_first_request_is_the_configured_credential(self):
        tracker.CONFIG_FILE.write_text(json.dumps({"token": SYNTHETIC["configured"]}))
        notify_active("Bearer " + SYNTHETIC["active"])
        self.assertEqual(SYNTHETIC["configured"], state["requests"][0])


if __name__ == "__main__":
    unittest.main()
'''

CANDIDATE_OPS_TESTS = '''\
"""Candidate regression tests: fallback and learned-token use (synthetic only).

T3 asserts configured-first behavior with a distinct active fallback; T5
asserts the active-no-file behavior: exactly one learned-token request and no
configured-token request.
"""
import json
import unittest

import tracker
from tracker import SYNTHETIC, notify_active, request_count, reset, state


class ActiveCredentialPolicy(unittest.TestCase):
    def setUp(self):
        tracker.CONFIG_FILE.unlink(missing_ok=True)
        reset()

    def tearDown(self):
        tracker.CONFIG_FILE.unlink(missing_ok=True)

    def test_t3_active_distinct_learned_fallback(self):
        tracker.CONFIG_FILE.write_text(json.dumps({"token": SYNTHETIC["configured"]}))
        notify_active("Bearer " + SYNTHETIC["active"])
        self.assertEqual(2, request_count())
        requests = list(state["requests"])
        self.assertEqual(SYNTHETIC["configured"], requests[0])
        self.assertNotEqual(requests[0], requests[1])
        self.assertEqual("Bearer " + SYNTHETIC["active"], requests[1])

    def test_t5_active_learned_credential_without_configured_token(self):
        notify_active("Bearer " + SYNTHETIC["learned"])
        self.assertEqual(["Bearer " + SYNTHETIC["learned"]], state["requests"])


if __name__ == "__main__":
    unittest.main()
'''


def qualify_local_operator(text):
    """Qualify the synthetic caller as the local operator; assertions untouched.

    The overlay's API is ``notify_active(token, *, from_local_operator=False)``:
    a foreign or forwarded caller stays unqualified and its credential is not
    adopted. The candidate tests represent the LOCAL operator, so this adapter
    makes that intent explicit by adding ``from_local_operator=True`` to the
    ``notify_active(...)`` call sites — the synthetic equivalent of the
    production overlay's local-operator qualification. It changes only how the
    caller is qualified; every assertion is left byte-identical.
    """
    return re.sub(r"notify_active\(([^()\n]*)\)", r"notify_active(\1, from_local_operator=True)", text)


LOCAL_OPERATOR_ADAPTER = {
    "path": "tests/test_tracker_ops.py",
    "rationale": "Local-operator adapter: the candidate tests represent the local operator, but the "
    "overlay adopts an active credential only when from_local_operator is explicit. The "
    "adapter qualifies the notify_active call sites (caller qualification only; every "
    "assertion is unchanged), keeping foreign/forwarded callers unqualified.",
    "transform": qualify_local_operator,
}


def mutate_pilot_overlay(tree):
    """The complete pilot overlay: qualify notify_active and mark the revision."""
    path = Path(tree) / "tracker.py"
    text = path.read_text()
    assert NOTIFY_ACTIVE_BASE in text
    text = text.replace(NOTIFY_ACTIVE_BASE, NOTIFY_ACTIVE_OVERLAY)
    text = text.replace(
        '             "learned": "SYNTHETIC-LEARNED-TOKEN"}',
        '             "learned": "SYNTHETIC-LEARNED-TOKEN"}\nOVERLAY_REVISION = "SYNTHETIC-OVERLAY-1"',
    )
    path.write_text(text)


def mutate_state_line_overlay(tree):
    """An overlay whose hunk the candidate's dirty edit of the same line breaks."""
    path = Path(tree) / "tracker.py"
    path.write_text(
        path.read_text().replace(
            'state = {"requests": [], "adopted": []}', 'state = {"requests": [], "adopted": [], "overlay": []}'
        )
    )


CANDIDATE_CONFLICT_TRACKER = PILOT_TRACKER.replace(
    'state = {"requests": [], "adopted": []}', 'state = {"requests": [], "adopted": [], "candidate": []}'
)

PILOT_NODEIDS = [
    "tests.test_tracker.TrackerGuards.test_reset_clears_recorded_state",
    "tests.test_tracker.TrackerGuards.test_configured_credential_reads_the_credential_file",
    "tests.test_tracker.TrackerGuards.test_first_request_is_the_configured_credential",
    "tests.test_tracker_ops.ActiveCredentialPolicy.test_t3_active_distinct_learned_fallback",
    "tests.test_tracker_ops.ActiveCredentialPolicy.test_t5_active_learned_credential_without_configured_token",
]

PILOT_FILES = {"tracker.py": PILOT_TRACKER, "tests/__init__.py": "", "tests/test_tracker.py": PILOT_TESTS}


# --- composition fixture: two independent behaviors in one file ---------------

WINDOW_TRACKER = '''\
"""Synthetic window tracker (compatibility-composition fixture)."""


def configured_window():
    return 10


def fallback_floor():
    return 5
'''

WINDOW_TESTS = '''\
"""Original guard tests for the synthetic window tracker (committed at base)."""
import unittest

import tracker


class WindowTracker(unittest.TestCase):
    def test_configured_window_is_positive(self):
        self.assertGreater(tracker.configured_window(), 0)

    def test_fallback_floor_default(self):
        self.assertEqual(5, tracker.fallback_floor())

    def test_window_stays_above_floor(self):
        self.assertGreater(tracker.configured_window(), tracker.fallback_floor())


if __name__ == "__main__":
    unittest.main()
'''

CANDIDATE_WINDOW_TRACKER = WINDOW_TRACKER.replace(
    "def configured_window():\n    return 10", "def configured_window():\n    return 20"
)

CANDIDATE_WINDOW_TESTS = '''\
"""Candidate regression test for the widened configured window."""
import unittest

import tracker


class CandidateWindow(unittest.TestCase):
    def test_candidate_window_is_widened(self):
        self.assertEqual(20, tracker.configured_window())


if __name__ == "__main__":
    unittest.main()
'''

WINDOW_NODEIDS = [
    "tests.test_tracker.WindowTracker.test_configured_window_is_positive",
    "tests.test_tracker.WindowTracker.test_fallback_floor_default",
    "tests.test_tracker.WindowTracker.test_window_stays_above_floor",
    "tests.test_tracker_ops.CandidateWindow.test_candidate_window_is_widened",
]

WINDOW_FILES = {"tracker.py": WINDOW_TRACKER, "tests/__init__.py": "", "tests/test_tracker.py": WINDOW_TESTS}


def mutate_window_floor_and_guard(tree):
    """The AC13 overlay: fallback_floor 5 -> 7 and the original guard rewritten to 7."""
    tracker_path = Path(tree) / "tracker.py"
    tracker_path.write_text(
        tracker_path.read_text().replace("def fallback_floor():\n    return 5", "def fallback_floor():\n    return 7")
    )
    tests_path = Path(tree) / "tests" / "test_tracker.py"
    tests_path.write_text(
        tests_path.read_text().replace(
            "self.assertEqual(5, tracker.fallback_floor())", "self.assertEqual(7, tracker.fallback_floor())"
        )
    )


def mutate_window_floor_only(tree):
    """The AC14 overlay: fallback_floor 5 -> 7, no test file touched."""
    tracker_path = Path(tree) / "tracker.py"
    tracker_path.write_text(
        tracker_path.read_text().replace("def fallback_floor():\n    return 5", "def fallback_floor():\n    return 7")
    )


def hunk_boundary_truncation(text):
    """The patch cut at the second hunk header; the remaining hunks still apply."""
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.startswith("@@")]
    assert len(starts) >= 2, "fixture patch needs at least two hunks"
    return "".join(lines[: starts[1]])


class Repo:
    """A committed synthetic project; candidate files are overlaid on the working tree."""

    def __init__(self, files):
        self.temp = tempfile.TemporaryDirectory(prefix="compat-prep-")
        self.root = Path(self.temp.name).resolve() / "project"
        self.root.mkdir()
        write_files(files, self.root)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.evidence = Path(self.temp.name) / "evidence"

    def write(self, files):
        write_files(files, self.root)

    def overlay(self, name, *mutate):
        """An immutable overlay patch: git diff of base after ``mutate`` edits a scratch worktree."""
        source = Path(self.temp.name) / "overlays" / name
        source.mkdir(parents=True, exist_ok=True)
        tree = source / "tree"
        git(self.root, "worktree", "add", "-q", "--detach", str(tree), self.base)
        try:
            for change in mutate:
                change(tree)
            patch = source / f"{name}.patch"
            patch.write_bytes(git_bytes(tree, "diff", self.base))
        finally:
            git(self.root, "worktree", "remove", "--force", str(tree))
            git(self.root, "worktree", "prune")
        return patch

    def close(self):
        self.temp.cleanup()


class CompatPrepCase(unittest.TestCase):
    def repo(self, files):
        project = Repo(files)
        self.addCleanup(project.close)
        return project

    def pilot(self):
        return self.repo(PILOT_FILES)

    def window(self):
        return self.repo(WINDOW_FILES)

    def candidate_pilot(self):
        repo = self.pilot()
        repo.write({"tracker.py": CANDIDATE_PILOT_TRACKER, "tests/test_tracker_ops.py": CANDIDATE_OPS_TESTS})
        return repo

    def candidate_window(self):
        repo = self.window()
        repo.write({"tracker.py": CANDIDATE_WINDOW_TRACKER, "tests/test_tracker_ops.py": CANDIDATE_WINDOW_TESTS})
        return repo

    def prepare(self, repo, patch, **options):
        options.setdefault("overlay_sha256", sha(patch))
        result = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / "copy", patch, **options)
        if result.get("copy"):
            self.addCleanup(verify.remove_tree, repo.root, result["copy"])
        return result

    def prepare_pilot(self, repo, patch, **options):
        options.setdefault("expected_nodeids", PILOT_NODEIDS)
        options.setdefault("suite_files", ["tests/test_tracker.py", "tests/test_tracker_ops.py"])
        options.setdefault("python", sys.executable)
        options.setdefault("timeout", 120)
        options.setdefault("log_dir", repo.evidence)
        return self.prepare(repo, patch, **options)

    def prepare_window(self, repo, patch, **options):
        options.setdefault("expected_nodeids", WINDOW_NODEIDS)
        options.setdefault("suite_files", ["tests/test_tracker.py", "tests/test_tracker_ops.py"])
        options.setdefault("python", sys.executable)
        options.setdefault("timeout", 120)
        options.setdefault("log_dir", repo.evidence)
        return self.prepare(repo, patch, **options)

    def test_ac1_preparation_copy_binds_candidate_and_overlay(self):
        repo = self.candidate_pilot()
        patch = repo.overlay("pilot-api", mutate_pilot_overlay)
        candidate_tracker = sha(repo.root / "tracker.py")
        candidate_ops = sha(repo.root / "tests" / "test_tracker_ops.py")
        before = git(repo.root, "rev-parse", "HEAD")
        result = self.prepare_pilot(repo, patch)
        self.assertEqual([], result["reasons"], result)
        # The copy is a detached worktree of the base revision.
        copy = Path(result["copy"])
        self.assertEqual(repo.base, git(copy, "rev-parse", "HEAD"))
        # Pre-overlay candidate hashes are the candidate working-tree files, recorded in a
        # section separate from the post-overlay copy hashes.
        self.assertEqual(
            {
                "sha256": candidate_tracker,
                "mode": stat.S_IMODE((repo.root / "tracker.py").stat().st_mode),
                "status": "modified",
            },
            result["candidate_inputs"]["pre_overlay"]["tracker.py"],
        )
        self.assertEqual(
            {
                "sha256": candidate_ops,
                "mode": stat.S_IMODE((repo.root / "tests/test_tracker_ops.py").stat().st_mode),
                "status": "added",
            },
            result["candidate_inputs"]["pre_overlay"]["tests/test_tracker_ops.py"],
        )
        post = result["candidate_inputs"]["post_overlay"]
        self.assertEqual(candidate_ops, post["tests/test_tracker_ops.py"])
        self.assertNotEqual(candidate_tracker, post["tracker.py"])
        # The overlay is pinned, recorded and applied to the candidate copy.
        self.assertEqual(sha(patch), result["overlay"]["expected_sha256"])
        self.assertEqual(sha(patch), result["overlay"]["patch_sha256"])
        self.assertEqual(["tracker.py"], result["overlay"]["touched"])
        # The selected suite executed with the copy as its working directory.
        self.assertEqual(str(copy), result["suite"]["cwd"])
        self.assertEqual(5, result["suite"]["results"]["total"])
        self.assertEqual(sorted(PILOT_NODEIDS[:3]), sorted(result["accounting"]["passed"]))
        self.assertEqual(sorted(PILOT_NODEIDS[3:]), sorted(result["accounting"]["failed"]))
        # The candidate workspace's revision is the same before and after.
        self.assertEqual(before, git(repo.root, "rev-parse", "HEAD"))
        self.assertEqual(result["workspace_revision_before"], result["workspace_revision_after"])

    def test_ac2_receipt_binds_hashes_and_rejects_head_only(self):
        repo = self.candidate_pilot()
        patch = repo.overlay("pilot-api", mutate_pilot_overlay)
        result = self.prepare_pilot(repo, patch)
        self.assertEqual(
            {
                "sha256": sha(repo.root / "tracker.py"),
                "mode": stat.S_IMODE((repo.root / "tracker.py").stat().st_mode),
                "status": "modified",
            },
            result["candidate_inputs"]["pre_overlay"]["tracker.py"],
        )
        self.assertEqual(
            {
                "sha256": sha(repo.root / "tests" / "test_tracker_ops.py"),
                "mode": stat.S_IMODE((repo.root / "tests/test_tracker_ops.py").stat().st_mode),
                "status": "added",
            },
            result["candidate_inputs"]["pre_overlay"]["tests/test_tracker_ops.py"],
        )
        self.assertEqual(sha(patch), result["overlay"]["patch_sha256"])
        # A detached HEAD copy alone (empty candidate change set) is insufficient proof.
        clean = self.pilot()
        clean_patch = clean.overlay("pilot-api", mutate_pilot_overlay)
        empty = self.prepare_pilot(clean, clean_patch)
        self.assertNotEqual(compat.PASS, empty["verdict"])
        self.assertTrue(
            any("candidate production and test inputs" in reason for reason in empty["reasons"]), empty["reasons"]
        )
        self.assertFalse((Path(clean.temp.name) / "copy").exists())

    def test_ac6_transformations_recorded_with_identity_and_rationale(self):
        repo = self.candidate_pilot()
        patch = repo.overlay("pilot-api", mutate_pilot_overlay)
        result = self.prepare_pilot(repo, patch, transformations=(LOCAL_OPERATOR_ADAPTER,))
        self.assertEqual(compat.PASS, result["verdict"], result["reasons"])
        self.assertEqual(sorted(PILOT_NODEIDS), sorted(result["accounting"]["passed"]))
        # The original test file runs with unmodified code under the overlay.
        copy = Path(result["copy"])
        original = git_bytes(repo.root, "show", f"{repo.base}:tests/test_tracker.py")
        self.assertEqual(hashlib.sha256(original).hexdigest(), sha(copy / "tests" / "test_tracker.py"))
        self.assertEqual(1, len(result["transformations"]))
        record = result["transformations"][0]
        self.assertEqual("tests/test_tracker_ops.py", record["path"])
        self.assertEqual(
            result["candidate_inputs"]["post_overlay"]["tests/test_tracker_ops.py"], record["before_sha256"]
        )
        adapted = copy / "tests" / "test_tracker_ops.py"
        self.assertEqual(sha(adapted), record["after_sha256"])
        self.assertNotEqual(record["before_sha256"], record["after_sha256"])
        self.assertTrue(record["rationale"].strip())
        self.assertIn("from_local_operator=True", adapted.read_text())
        self.assertIn("self.assertEqual(2, request_count())", adapted.read_text())
        # The record is kept in a section distinct from the candidate inputs and the overlay.
        receipt = compat.write_receipt(Path(repo.temp.name) / "receipts", result)
        data = json.loads(receipt.read_text())
        self.assertEqual(record, data["transformations"][0])
        self.assertIn("candidate_inputs", data)
        self.assertIn("overlay", data)
        self.assertNotEqual(data["transformations"], data["candidate_inputs"])
        self.assertNotIn("rationale", data["overlay"])
        self.assertNotIn("rationale", data["candidate_inputs"])

    def test_ac7_accounting_rejects_skips_deselection_and_partial_overlay(self):
        # A run that deselects one of the five expected nodeids accounts for four only.
        repo = self.candidate_pilot()
        patch = repo.overlay("pilot-api", mutate_pilot_overlay)
        command = (
            f"{shlex.quote(sys.executable)} -m unittest -v tests.test_tracker "
            "tests.test_tracker_ops.ActiveCredentialPolicy."
            "test_t5_active_learned_credential_without_configured_token"
        )
        deselected = self.prepare_pilot(repo, patch, suite_command=command)
        self.assertEqual(compat.INCOMPLETE, deselected["verdict"], deselected["reasons"])
        self.assertEqual([PILOT_NODEIDS[3]], deselected["accounting"]["unaccounted"])
        self.assertTrue(
            any("test_t3_active_distinct_learned_fallback" in reason for reason in deselected["reasons"]),
            deselected["reasons"],
        )
        # A truncated patch file (pin = the complete patch's sha256) is a partial overlay,
        # even though its remaining hunks apply cleanly.
        other = self.candidate_pilot()
        complete = other.overlay("pilot-api", mutate_pilot_overlay)
        truncated = Path(other.temp.name) / "overlays" / "truncated.patch"
        truncated.write_text(hunk_boundary_truncation(complete.read_text()))
        self.assertNotEqual(sha(complete), sha(truncated))
        self.assertEqual("", verify.patch_applies(other.root, other.base, truncated))
        partial = self.prepare(
            other,
            truncated,
            expected_nodeids=PILOT_NODEIDS,
            suite_files=["tests/test_tracker.py", "tests/test_tracker_ops.py"],
            python=sys.executable,
            timeout=120,
            log_dir=other.evidence,
            overlay_sha256=sha(complete),
        )
        self.assertEqual(compat.INCOMPLETE, partial["verdict"])
        self.assertTrue(any("partial overlay" in reason for reason in partial["reasons"]), partial["reasons"])
        self.assertFalse((Path(other.temp.name) / "copy").exists())
        self.assertIsNone(partial["copy"])

    def test_ac12_overlay_failure_cleans_up_and_preserves_workspace_revision(self):
        repo = self.pilot()
        repo.write({"tracker.py": CANDIDATE_CONFLICT_TRACKER, "tests/test_tracker_ops.py": CANDIDATE_OPS_TESTS})
        patch = repo.overlay("state-line", mutate_state_line_overlay)
        self.assertEqual("", verify.patch_applies(repo.root, repo.base, patch))
        before = git(repo.root, "rev-parse", "HEAD")
        result = self.prepare_pilot(repo, patch)
        self.assertNotEqual(compat.PASS, result["verdict"])
        self.assertTrue(any(reason.startswith("overlay failure") for reason in result["reasons"]), result["reasons"])
        self.assertFalse((Path(repo.temp.name) / "copy").exists())
        self.assertEqual(before, git(repo.root, "rev-parse", "HEAD"))
        self.assertEqual(1, len(git(repo.root, "worktree", "list").splitlines()))

    def test_ac13_overlapping_candidate_and_overlay_changes_composed(self):
        repo = self.candidate_window()
        patch = repo.overlay("window-floor", mutate_window_floor_and_guard)
        result = self.prepare_window(repo, patch)
        self.assertEqual(compat.PASS, result["verdict"], result["reasons"])
        copy = Path(result["copy"])
        # Both changes are present and exercised in the copy.
        probe = subprocess.run(
            [sys.executable, "-c", "import tracker; print(tracker.configured_window(), tracker.fallback_floor())"],
            cwd=copy,
            capture_output=True,
            text=True,
        )
        self.assertEqual("20 7", probe.stdout.strip(), probe.stderr)
        self.assertEqual(0, result["suite"]["exit_code"])
        self.assertEqual(4, len(result["suite"]["results"]["passed"]))
        self.assertEqual([], result["suite"]["results"]["failed"])
        # The receipt binds the pre-overlay candidate hash separately from the post-overlay hash.
        pre = result["candidate_inputs"]["pre_overlay"]["tracker.py"]
        self.assertEqual(
            {
                "sha256": sha(repo.root / "tracker.py"),
                "mode": stat.S_IMODE((repo.root / "tracker.py").stat().st_mode),
                "status": "modified",
            },
            pre,
        )
        base_tracker = hashlib.sha256(git_bytes(repo.root, "show", f"{repo.base}:tracker.py")).hexdigest()
        post = result["candidate_inputs"]["post_overlay"]["tracker.py"]
        self.assertNotEqual(pre["sha256"], post)
        self.assertNotEqual(base_tracker, post)
        self.assertEqual(
            result["candidate_inputs"]["pre_overlay"]["tests/test_tracker_ops.py"]["sha256"],
            result["candidate_inputs"]["post_overlay"]["tests/test_tracker_ops.py"],
        )

    def test_ac14_patch_first_erasure_rejected_as_invalid_proof(self):
        repo = self.candidate_window()
        patch = repo.overlay("floor-only", mutate_window_floor_only)
        # The wrong composition: overlay applied to the detached base worktree first,
        # the candidate's dirty tracker.py copied over it last.
        wrong = Path(repo.temp.name) / "wrong-copy"
        changes = verify.changed_files(repo.root, repo.base)
        tree = verify.make_tree(
            repo.root,
            repo.base,
            wrong,
            repo.root,
            {"tests/test_tracker_ops.py": changes["tests/test_tracker_ops.py"]},
            patch=patch,
        )
        self.addCleanup(verify.remove_tree, repo.root, tree)
        shutil.copy2(repo.root / "tracker.py", tree / "tracker.py")
        command = f"{shlex.quote(sys.executable)} -m unittest -v tests.test_tracker tests.test_tracker_ops"
        receipt = verify.run_suite(
            verify.detect_framework(tree, python=sys.executable),
            command,
            tree,
            repo.evidence,
            "wrong-copy-suite",
            timeout=120,
        )
        # The suite is green there, but the overlay is gone.
        self.assertEqual(0, receipt["exit_code"], receipt["tail"])
        self.assertEqual([], receipt["results"]["failed"])
        self.assertEqual(4, len(receipt["results"]["passed"]))
        self.assertEqual(sha(repo.root / "tracker.py"), sha(tree / "tracker.py"))
        probe = subprocess.run(
            [sys.executable, "-c", "import tracker; print(tracker.fallback_floor())"],
            cwd=tree,
            capture_output=True,
            text=True,
        )
        self.assertEqual("5", probe.stdout.strip(), probe.stderr)
        # The preparation contract's completeness verification rejects the composition.
        problems = compat.composition_problems(tree, changes, compat.candidate_hashes(repo.root, changes), patch)
        self.assertTrue(problems)
        self.assertTrue(any("erased overlay" in problem and "tracker.py" in problem for problem in problems), problems)

    def test_ac15_copy_step_failure_cleans_up_and_preserves_workspace_revision(self):
        repo = self.candidate_pilot()
        patch = repo.overlay("pilot-api", mutate_pilot_overlay)
        changes = verify.changed_files(repo.root, repo.base)
        before = git(repo.root, "rev-parse", "HEAD")
        (repo.root / "tracker.py").unlink()  # deleted after the change set is computed
        result = self.prepare_pilot(repo, patch, changes=changes)
        self.assertNotEqual(compat.PASS, result["verdict"])
        self.assertTrue(
            any(reason.startswith("copy failure") and "tracker.py" in reason for reason in result["reasons"]),
            result["reasons"],
        )
        self.assertFalse((Path(repo.temp.name) / "copy").exists())
        self.assertEqual(before, git(repo.root, "rev-parse", "HEAD"))
        self.assertEqual(1, len(git(repo.root, "worktree", "list").splitlines()))


if __name__ == "__main__":
    unittest.main()
