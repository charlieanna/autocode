"""Which files define a package suite, and what the original-definitions run receives (#528).

Pure rules over text, plus the Git reading of the original code (real git, no
package manager). The end-to-end proofs with real npm are in
tests/test_package_suite_proof.py.
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_original_scripts as original_scripts
import autocode_scratch_overlay as scratch_overlay
import autocode_verify as verify


def manifest(scripts=None, **fields):
    return json.dumps({"name": "calc", "version": "1.0.0", **fields,
                       **({"scripts": scripts} if scripts is not None else {})}, indent=2) + "\n"


def restored(original, candidate, path="package.json"):
    return original_scripts.restored(path, original, candidate)


class DefinitionFileTests(unittest.TestCase):
    def test_manifests_and_package_manager_configuration_anywhere_are_definitions(self):
        for path in ("package.json", "web/package.json", "packages/calc/package.json", ".npmrc", "web/.npmrc",
                     ".yarnrc", ".yarnrc.yml", ".pnpmfile.cjs", "pnpm-workspace.yaml", "bunfig.toml",
                     "turbo.json", "packages/calc/turbo.json", "nx.json", "lerna.json"):
            with self.subTest(path=path):
                self.assertTrue(original_scripts.is_definition(path, verify.is_test_path))

    def test_other_files_and_files_under_test_paths_are_not(self):
        # A package.json under a test path is test data or test setup: the protected-test gate covers it.
        for path in ("calc.js", "my-package.json", "package.json.bak", "packages/calc/index.js", "test/package.json",
                     "tests/fixtures/app/package.json", "__tests__/.npmrc", "jest.config.js", "tsconfig.json"):
            with self.subTest(path=path):
                self.assertFalse(original_scripts.is_definition(path, verify.is_test_path))


class RestoredManifestTests(unittest.TestCase):
    def test_suite_fields_come_from_base_and_code_fields_from_the_candidate(self):
        original = manifest({"test": "node --test", "pretest": "node lint.js"}, config={"tests": "test/*.js"},
                            workspaces=["packages/*"], jest={"testMatch": ["**/*.test.js"]}, testFiles=["a.js"],
                            files=["lib"], main="calc.js")
        candidate = manifest({"test": "node --test test/feature.test.js"}, config={"tests": "test/feature.js"},
                             workspaces=["packages/feature"], jest={"testMatch": ["**/feature.test.js"]},
                             testFiles=["feature.js"], files=["lib", "feature"], main="lib/calc.js", type="module",
                             version="1.1.0", dependencies={"left-pad": "1.3.0"}, exports={".": "./lib/calc.js"})
        result = json.loads(restored(original, candidate))
        self.assertEqual({"test": "node --test", "pretest": "node lint.js"}, result["scripts"])
        for field in ("config", "workspaces", "jest", "testFiles", "files"):
            self.assertEqual(json.loads(original)[field], result[field], field)
        for field in ("main", "type", "version", "dependencies", "exports"):
            self.assertEqual(json.loads(candidate)[field], result[field], field)

    def test_fields_only_one_side_has(self):
        original = manifest({"test": "node --test"}, config={"port": 1}, mocha={"spec": "test"}, main="calc.js")
        candidate = manifest({"test": "node --test"}, wireit={"test": {"command": "exit 0"}}, packageManager="pnpm@10")
        result = json.loads(restored(original, candidate))
        # Suite fields the candidate removed come back and ones it added go; a code field it removed stays removed.
        self.assertEqual({"port": 1}, result["config"])
        self.assertEqual({"spec": "test"}, result["mocha"])
        self.assertNotIn("wireit", result)
        self.assertNotIn("packageManager", result)
        self.assertNotIn("main", result)

    def test_the_candidate_field_order_is_kept(self):
        original = manifest({"test": "node --test"})
        candidate = json.dumps({"scripts": {"test": "exit 0"}, "version": "2.0.0", "name": "calc"}, indent=2) + "\n"
        self.assertEqual(["scripts", "version", "name"], list(json.loads(restored(original, candidate))))

    def test_a_change_to_code_and_metadata_fields_alone_restores_nothing(self):
        original = manifest({"test": "node --test"})
        for candidate in (manifest({"test": "node --test"}, dependencies={"left-pad": "1.3.0"}),
                          manifest({"test": "node --test"}, type="module", main="lib/index.js", description="x"),
                          json.dumps({"scripts": {"test": "node --test"}, "version": "1.0.1", "name": "calc"})):
            with self.subTest(candidate=candidate):
                self.assertIs(candidate, restored(original, candidate))

    def test_a_widened_or_added_script_is_restored_too(self):
        # Only the run under the original scripts can tell a widened script from a narrowed one.
        original = manifest({"test": "node --test test/calc.test.js"})
        for scripts in ({"test": "node --test test/calc.test.js test/feature.test.js"},
                        {"test": "node --test test/calc.test.js", "start": "node calc.js"}):
            with self.subTest(scripts=scripts):
                self.assertEqual(original, restored(original, manifest(scripts)))

    def test_an_added_manifest_stays_and_a_deleted_one_comes_back(self):
        self.assertEqual(manifest({"test": "node --test"}), restored(None, manifest({"test": "node --test"})))
        self.assertEqual(manifest({"test": "node --test"}), restored(manifest({"test": "node --test"}), None))

    def test_an_unreadable_manifest_is_put_back_whole(self):
        original = manifest({"test": "node --test"})
        for candidate in ('{"scripts": {"test": ', "[1, 2]", '"text"'):
            with self.subTest(candidate=candidate):
                self.assertEqual(original, restored(original, candidate))
        self.assertEqual("{broken", restored("{broken", manifest({"test": "exit 0"})))

    def test_a_byte_order_mark_is_read_like_npm_reads_it(self):
        original = "﻿" + manifest({"test": "node --test"})
        candidate = "﻿" + manifest({"test": "node --test"}, dependencies={"left-pad": "1.3.0"})
        self.assertIs(candidate, restored(original, candidate))
        self.assertEqual({"test": "node --test"}, json.loads(
            restored(original, "﻿" + manifest({"test": "exit 0"})))["scripts"])


class RestoredConfigurationTests(unittest.TestCase):
    def test_configuration_returns_to_base_whole(self):
        for path in (".npmrc", "web/.npmrc", "pnpm-workspace.yaml", "turbo.json"):
            with self.subTest(path=path):
                self.assertIsNone(restored(None, "script-shell=/usr/bin/true\n", path))
                self.assertEqual("a=1\n", restored("a=1\n", None, path))
                self.assertEqual("a=1\n", restored("a=1\n", "a=1\nnode-options=--test-name-pattern=mul\n", path))


class PlanTests(unittest.TestCase):
    def test_only_files_that_restoring_changes_are_planned(self):
        pairs = {
            "package.json": (manifest({"test": "node --test"}),
                             manifest({"test": "node --test"}, dependencies={"left-pad": "1.3.0"})),
            "packages/calc/package.json": (manifest({"test": "node --test"}), manifest({"test": "exit 0"})),
            ".npmrc": (None, "script-shell=/usr/bin/true\n"),
            "packages/new/package.json": (None, manifest({"test": "node --test"})),
            "web/.npmrc": ("a=1\n", "a=1\n"),
        }
        self.assertEqual({".npmrc": None, "packages/calc/package.json": manifest({"test": "node --test"})},
                         original_scripts.plan(pairs))
        self.assertEqual({}, original_scripts.plan({}))


def receipt(exit_code=0, **fields):
    return {"command": "npm test --silent", "exit_code": exit_code, "timed_out": False, "results": None,
            "restored": ["package.json"], **fields}


class JudgeTests(unittest.TestCase):
    def judged(self, run, base_exit=0):
        fail, unverified, notes = [], [], []
        original_scripts.judge(run, {"receipt": receipt(base_exit)}, fail, unverified, notes)
        return fail, unverified, notes

    def test_a_pass_under_the_original_definitions_is_noted(self):
        fail, unverified, notes = self.judged(receipt(0))
        self.assertEqual(([], []), (fail, unverified))
        self.assertIn("also passes on the candidate", notes[0])

    def test_a_failure_under_the_original_definitions_fails_when_base_passed(self):
        fail, unverified, notes = self.judged(receipt(1))
        self.assertEqual(([], []), (unverified, notes))
        self.assertIn("as originally defined passes on base but fails on the candidate", fail[0])
        self.assertIn("package.json", fail[0])
        fail, unverified, _ = self.judged(receipt(1), base_exit=1)
        self.assertEqual([], fail)
        self.assertIn("did not pass on base either", unverified[0])

    def test_a_run_that_did_not_finish_proves_nothing(self):
        for run in (receipt(None, timed_out=True), receipt(1, timed_out=True),
                    {"error": "git read-tree failed", "restored": ["package.json"]},
                    receipt(0, results_expected=True)):
            with self.subTest(run=run):
                fail, unverified, notes = self.judged(run)
                self.assertEqual(([], []), (fail, notes))
                self.assertEqual(1, len(unverified), unverified)


class DecidedByExitCodeTests(unittest.TestCase):
    RESULTS = {"passed": ["a"], "failed": [], "skipped": [], "total": 1, "complete": True}

    def test_only_a_passing_suite_without_a_per_test_comparison_needs_the_run(self):
        base = {"receipt": receipt(0)}
        decided = original_scripts.decided_by_exit_code
        self.assertTrue(decided(receipt(0), base))
        self.assertTrue(decided(receipt(0, results=self.RESULTS), base))
        self.assertTrue(decided(receipt(0), {"receipt": receipt(0, results=self.RESULTS)}))
        self.assertFalse(decided(receipt(0, results=self.RESULTS), {"receipt": receipt(0, results=self.RESULTS)}))
        self.assertFalse(decided(receipt(1), base))
        self.assertFalse(decided(receipt(0, timed_out=True), base))
        self.assertFalse(decided(receipt(0), None))


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class OriginalsTests(unittest.TestCase):
    """Reading the original code and the candidate's files (real git, no package manager)."""

    CALC_TEST = "test('add', () => assert.equal(add(2, 3), 5));\n"

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="original-scripts-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "project"
        self.root.mkdir()
        self.write({"package.json": manifest({"test": "npm test --workspaces"}, workspaces=["packages/*"]),
                    "packages/a/package.json": manifest({"test": "node --test test/a.test.js"}),
                    "packages/a/calc.js": "exports.add = (a, b) => a + b;\n",
                    "test/package.json": manifest({"test": "fixture"}), ".npmrc": "fund=false\n",
                    "test/calc.test.js": self.CALC_TEST, "test/run.sh": "exit 0\n"})
        (self.root / "test/run.sh").chmod(0o755)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")

    def write(self, files):
        for path, text in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text)

    def originals(self, patch=None):
        changes = verify.changed_files(self.root, self.base)
        return original_scripts.originals(self.root, self.base, patch, changes, verify.is_test_path)

    def objects(self):
        return git(self.root, "count-objects")

    def test_each_changed_definition_pairs_its_original_and_candidate_text(self):
        self.write({"packages/a/package.json": manifest({"test": "exit 0"}), "web/.npmrc": "script-shell=true\n",
                    "test/package.json": manifest({"test": "changed fixture"}),
                    "packages/a/calc.js": "exports.add = (a, b) => a - b;\n"})
        (self.root / ".npmrc").unlink()
        definitions, _tests = self.originals()
        self.assertEqual({
            ".npmrc": ("fund=false\n", None),
            "packages/a/package.json": (manifest({"test": "node --test test/a.test.js"}), manifest({"test": "exit 0"})),
            "web/.npmrc": (None, "script-shell=true\n"),
        }, definitions)

    def test_existing_tests_the_candidate_changed_come_back_with_the_definitions(self):
        edited = "test('add', () => assert.equal(add(2, 3), -1));\n"
        self.write({"test/calc.test.js": edited, "test/feature.test.js": "test('mul')\n",
                    "test/package.json": manifest({"test": "changed fixture"})})
        (self.root / "test/run.sh").unlink()
        # Edited, added and deleted tests alone change no definition: no run, nothing read.
        self.assertEqual(({}, {}), self.originals())
        self.write({".npmrc": "fund=true\n"})
        _definitions, tests = self.originals()
        self.assertEqual({"test/calc.test.js": (self.CALC_TEST.encode(), 0o644),
                          "test/package.json": (manifest({"test": "fixture"}).encode(), 0o644),
                          "test/run.sh": (b"exit 0\n", 0o755)}, tests)

    def test_nothing_is_read_when_no_definition_changed(self):
        self.write({"packages/a/calc.js": "exports.add = (a, b) => a - b;\n"})
        self.assertEqual(({}, {}), self.originals())

    def test_the_original_includes_the_reviewed_patch_without_writing_to_the_repository(self):
        narrow, wide = manifest({"test": "node --test test/a.test.js"}), manifest({"test": "node --test"})
        reviewed_test = self.CALC_TEST + "test('add 0', () => assert.equal(add(0, 0), 0));\n"
        self.write({"packages/a/package.json": wide, "test/calc.test.js": reviewed_test})
        patch = self.root.parent / "reviewed.patch"
        patch.write_text(subprocess.run(["git", "diff"], cwd=self.root, check=True, capture_output=True,
                                        text=True).stdout)
        # The follow-up puts back the narrow script and the old test: both match base, not the reviewed change.
        self.write({"packages/a/package.json": narrow, "test/calc.test.js": self.CALC_TEST,
                    "packages/a/calc.js": "exports.add = (a, b) => a - b;\n"})
        before = self.objects()
        self.assertEqual(({"packages/a/package.json": (wide, narrow)},
                          {"test/calc.test.js": (reviewed_test.encode(), 0o644)}), self.originals(patch))
        self.assertEqual(before, self.objects())
        self.assertEqual(({}, {}), self.originals())
        patch.write_text("not a patch\n")
        with self.assertRaises(ValueError):
            self.originals(patch)

    def test_a_linked_original_definition_cannot_be_restored(self):
        (self.root / "package.json").unlink()
        (self.root / "package.json").symlink_to("packages/a/package.json")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "link")
        self.base = git(self.root, "rev-parse", "HEAD")
        (self.root / "package.json").unlink()
        self.write({"package.json": manifest({"test": "exit 0"})})
        with self.assertRaisesRegex(ValueError, "package.json is not a regular file"):
            self.originals()


class InstallTests(unittest.TestCase):
    def test_restored_files_are_written_inside_the_tree_and_never_through_a_link(self):
        with tempfile.TemporaryDirectory() as temp:
            tree, outside, staging = Path(temp) / "tree", Path(temp) / "outside", Path(temp) / "staging"
            (tree / "web").mkdir(parents=True)
            outside.mkdir()
            (tree / ".npmrc").write_text("script-shell=true\n")
            (outside / ".npmrc").write_text("keep\n")
            (tree / "linked").symlink_to(outside, target_is_directory=True)
            (tree / "test").symlink_to(outside, target_is_directory=True)
            original_scripts.install(tree, {".npmrc": None, "linked/.npmrc": None, "web/package.json": "{}\n"},
                                     {"test/run.sh": (b"exit 1\n", 0o755)}, staging)
            self.assertFalse((tree / ".npmrc").exists())
            self.assertFalse((tree / "linked").is_symlink())
            self.assertEqual("keep\n", (outside / ".npmrc").read_text())
            self.assertEqual(["keep\n"], [path.read_text() for path in outside.iterdir()])
            self.assertEqual("{}\n", (tree / "web" / "package.json").read_text())
            self.assertEqual("exit 1\n", (tree / "test" / "run.sh").read_text())
            self.assertEqual(0o755, (tree / "test" / "run.sh").stat().st_mode & 0o777)
            self.assertEqual("{}\n", (staging / "web" / "package.json").read_text())
            with self.assertRaises(ValueError):
                scratch_overlay.apply(tree, {"web/package.json": staging / "web" / "package.json"}, None,
                                      removed=["web/package.json"])

if __name__ == "__main__":
    unittest.main()
