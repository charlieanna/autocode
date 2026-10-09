"""A review follow-up is proven against the change the review judged (#51).

"Review change.patch", then "Fix them.": the fix lands the patch with the review's findings
fixed. The findings exist only with the patch applied, so the regression proof's "original
code" is the base with the patch applied. Against the code before the patch (live
review-then-fix runs, 2026-09-29), a test for a finding passed (the behavior was fine before
the change) and was rejected, while every test the patch rewrote counted as a flip.

These run real unittest suites in scratch Git worktrees; no provider is launched.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_regression as regression
import autocode_verify as verify
from tests.test_verify import isolated_python

SEED = {
    "rates.py": 'POLICIES = {"de": 1}\n\n\ndef attempts(tld):\n    return POLICIES.get(tld, 3)\n',
    "test_rates.py": ("import unittest\n\nfrom rates import attempts\n\n\n"
                      "class RateTests(unittest.TestCase):\n"
                      "    def test_default(self):\n        self.assertEqual(3, attempts('xyz'))\n"),
}
# The reviewed change: per-registry attempts, a rewritten test file, and .de silently dropped.
PATCHED = {
    "rates.py": ('REGISTRIES = {"com": 4}\n\n\ndef attempts(tld, table=None):\n'
                 '    return (table or REGISTRIES).get(tld, 3)\n'),
    "test_rates.py": ("import unittest\n\nfrom rates import attempts\n\n\n"
                      "class RateTests(unittest.TestCase):\n"
                      "    def test_default(self):\n        self.assertEqual(3, attempts('xyz'))\n\n"
                      "    def test_com(self):\n        self.assertEqual(4, attempts('com'))\n"),
}
# The follow-up: the patch landed with .de restored, and a regression test for the finding.
FIXED = {
    "rates.py": PATCHED["rates.py"].replace('{"com": 4}', '{"com": 4, "de": 1}'),
    "test_rates.py": PATCHED["test_rates.py"] + "\n    def test_de_is_never_retried(self):\n"
                                                 "        self.assertEqual(1, attempts('de'))\n",
}
DE_TEST = "test_rates.RateTests.test_de_is_never_retried"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def write(root, files):
    for name, text in files.items():
        (root / name).write_text(text)


class ReviewedPatchProofTests(unittest.TestCase):
    def setUp(self):
        self.python = isolated_python(self)
        temp = tempfile.TemporaryDirectory(prefix="reviewed-patch-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "project"
        self.root.mkdir()
        self.evidence = Path(temp.name) / "evidence"
        write(self.root, SEED)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD").strip()
        write(self.root, PATCHED)
        patch = git(self.root, "diff")
        git(self.root, "checkout", "-q", "--", ".")
        (self.root / "change.patch").write_text(patch)
        git(self.root, "add", "change.patch")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "the PR")
        self.base = git(self.root, "rev-parse", "HEAD").strip()
        write(self.root, FIXED)

    def verify(self, base_patch=None):
        framework = verify.detect_framework(self.root, python=self.python)
        base_suite = verify.baseline(self.root, self.base, self.evidence, framework=framework,
                                     suite_command=framework.suite, timeout=120, base_patch=base_patch)
        return verify.verify(self.root, self.base, self.evidence, framework=framework, base_suite=base_suite,
                             timeout=120, base_patch=base_patch)

    def test_against_the_code_before_the_patch_the_finding_s_test_proves_nothing(self):
        result = self.verify()
        self.assertNotIn(DE_TEST, result["fail_to_pass"] or [])
        self.assertIn("test_rates.RateTests.test_com", result["fail_to_pass"])  # a test the patch rewrote

    def test_against_the_reviewed_change_only_the_finding_s_test_flips(self):
        result = self.verify(base_patch=self.root / "change.patch")
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([DE_TEST], result["fail_to_pass"])
        self.assertIn("test_rates.RateTests.test_com", result["pass_to_pass"])
        self.assertEqual(str(self.root / "change.patch"), result["base_patch"])
        self.assertEqual("", git(self.root, "status", "--porcelain", "--", "change.patch"))  # workspace untouched

    def test_a_patch_is_checked_against_the_base_without_a_worktree(self):
        self.assertEqual("", verify.patch_applies(self.root, self.base, self.root / "change.patch"))
        (self.root / "other.patch").write_text((self.root / "change.patch").read_text().replace(
            "POLICIES", "NOT_THERE"))
        self.assertIn("patch", verify.patch_applies(self.root, self.base, self.root / "other.patch").lower())

    def test_the_proof_uses_the_patch_of_the_review_the_run_follows_up(self):
        state = {"workflow": {"kind": "build"}, "turns": [{"say": "Fix them.", "previous": {
            "workflow": "review", "review": {"report_path": "review/findings.json", "change_under_review": "",
                                             "change_patch": "change.patch", "blocking": [{"id": "F1"}],
                                             "advisory": []}}}]}
        self.assertEqual(self.root / "change.patch", regression.reviewed_patch(state, self.root))
        self.assertIsNone(regression.reviewed_patch({**state, "workflow": {"kind": "discuss"}}, self.root))
        self.assertIsNone(regression.reviewed_patch({"workflow": {"kind": "build"}}, self.root))

    def test_a_reviewed_patch_that_no_longer_applies_leaves_the_proof_unverified(self):
        (self.root / "change.patch").write_text((self.root / "change.patch").read_text().replace("POLICIES", "GONE"))
        state = {"base_commit": self.base, "settings": {}, "stages": [], "workflow": {"kind": "build"},
                 "turns": [{"say": "Fix them.", "previous": {"workflow": "review", "review": {
                     "report_path": "review/findings.json", "change_under_review": "", "change_patch": "change.patch",
                     "blocking": [{"id": "F1"}], "advisory": []}}}]}
        proof = regression.prove(state, self.root, self.evidence)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"])
        [reason] = proof["unverified"]
        self.assertIn("The reviewed change change.patch cannot be applied to the base revision", reason)

    def test_replacing_patch_bytes_at_same_ignored_path_invalidates_baseline(self):
        import autocode_util as util
        patch = self.root / '.autocode' / 'review.patch'
        patch.parent.mkdir()
        patch.write_text((self.root / 'change.patch').read_text())
        state = {'base_commit': self.base, 'settings': {'regression': {'python': self.python}},
                 'goal_contract': {'body': {'task_kind': 'bugfix'}}, 'workflow': {'kind': 'build'},
                 'turns': [{'say': 'Fix them.', 'previous': {'workflow': 'review', 'review': {
                     'report_path': 'review/findings.json', 'change_under_review': '',
                     'change_patch': '.autocode/review.patch', 'blocking': [{'id': 'F1'}], 'advisory': []}}}]}
        first = regression.prove(state, self.root, self.evidence)
        self.assertEqual(verify.PASS, first['verdict'], first)
        original_revision = util.snapshot(self.root)['revision']
        old_baseline = state['regression_baseline']['path']
        patch.write_text(patch.read_text().replace('+REGISTRIES = {"com": 4}',
                                                 '+REGISTRIES = {"com": 4, "de": 1}'))
        self.assertEqual(original_revision, util.snapshot(self.root)['revision'])
        second = regression.prove(state, self.root, self.evidence)
        self.assertEqual(verify.FAIL, second['verdict'], second)
        self.assertNotEqual(old_baseline, state['regression_baseline']['path'])
        self.assertTrue(Path(old_baseline).is_file())
        self.assertIn('do not reproduce the bug', ' '.join(second['failures']))


if __name__ == "__main__":
    unittest.main()
