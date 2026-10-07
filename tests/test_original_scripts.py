"""Which files define a package suite, and what the original-definitions run receives (#528).

Pure rules over file contents, plus the Git reading of the original code (real
git, no package manager). The end-to-end proofs with real npm, Yarn and pnpm
are in tests/test_package_suite_proof.py.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_original_scripts as original_scripts
import autocode_scratch_overlay as scratch_overlay
import autocode_verify as verify


def manifest(scripts=None, **fields):
    return (json.dumps({"name": "calc", "version": "1.0.0", **fields,
                        **({"scripts": scripts} if scripts is not None else {})}, indent=2) + "\n").encode()


def restored(original, candidate, path="package.json"):
    return original_scripts.restored(path, original, candidate)


class DefinitionFileTests(unittest.TestCase):
    def test_manifests_package_manager_configuration_and_yarn_releases_anywhere_are_definitions(self):
        # Under a test path too: a workspace member in tests/ runs its own scripts (it returns whole, as a test).
        for path in ("package.json", "web/package.json", "packages/calc/package.json", ".npmrc", "web/.npmrc",
                     ".yarnrc", ".yarnrc.yml", ".pnpmfile.cjs", "pnpm-workspace.yaml", "bunfig.toml",
                     "turbo.json", "packages/calc/turbo.json", "nx.json", "lerna.json", "tests/package.json",
                     "test/fixtures/app/package.json", "__tests__/.npmrc", ".yarn/releases/yarn-4.5.0.cjs",
                     ".yarn/plugins/@yarnpkg/plugin-workspace-tools.cjs", "web/.yarn/releases/yarn-4.9.1.cjs"):
            with self.subTest(path=path):
                self.assertTrue(original_scripts.is_definition(path))

    def test_other_files_are_not(self):
        for path in ("calc.js", "my-package.json", "package.json.bak", "packages/calc/index.js", "jest.config.js",
                     "tsconfig.json", ".yarn/cache/left-pad.zip", ".yarn/install-state.gz", "releases/notes.md"):
            with self.subTest(path=path):
                self.assertFalse(original_scripts.is_definition(path))


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
        candidate = json.dumps({"scripts": {"test": "exit 0"}, "version": "2.0.0", "name": "calc"}).encode()
        self.assertEqual(["scripts", "version", "name"], list(json.loads(restored(original, candidate))))

    def test_a_change_to_code_and_metadata_fields_alone_restores_nothing(self):
        original = manifest({"test": "node --test"})
        for candidate in (manifest({"test": "node --test"}, dependencies={"left-pad": "1.3.0"}),
                          manifest({"test": "node --test"}, type="module", main="lib/index.js", description="x"),
                          json.dumps({"scripts": {"test": "node --test"}, "version": "1.0.1", "name": "calc"}).encode()):
            with self.subTest(candidate=candidate):
                self.assertIs(candidate, restored(original, candidate))

    def test_a_widened_or_added_script_is_restored_too(self):
        # Only the run under the original scripts can tell a widened script from a narrowed one.
        original = manifest({"test": "node --test test/calc.test.js"})
        for scripts in ({"test": "node --test test/calc.test.js test/feature.test.js"},
                        {"test": "node --test test/calc.test.js", "start": "node calc.js"}):
            with self.subTest(scripts=scripts):
                self.assertEqual(original, restored(original, manifest(scripts)))

    def test_an_added_package_goes_and_a_deleted_one_comes_back(self):
        # A new workspace member, or a package.json where npm used to find the parent's, defines what
        # the original suite never ran.
        self.assertIsNone(restored(None, manifest({"test": "node --test"})))
        self.assertIsNone(restored(None, json.dumps({"name": "aaa", "type": "module"}).encode()))
        self.assertIsNone(restored(None, b"{broken"))
        self.assertEqual(manifest({"test": "node --test"}), restored(manifest({"test": "node --test"}), None))

    def test_an_added_manifest_with_only_loading_fields_stays(self):
        marker = json.dumps({"type": "module", "exports": "./index.js"}).encode()
        self.assertIs(marker, restored(None, marker))

    def test_an_unreadable_manifest_is_put_back_whole(self):
        original = manifest({"test": "node --test"})
        for candidate in (b'{"scripts": {"test": ', b"[1, 2]", b'"text"', b"\xff\xfe"):
            with self.subTest(candidate=candidate):
                self.assertEqual(original, restored(original, candidate))
        self.assertEqual(b"{broken", restored(b"{broken", manifest({"test": "exit 0"})))

    def test_a_byte_order_mark_is_read_like_npm_reads_it(self):
        bom = "﻿".encode()
        original = bom + manifest({"test": "node --test"})
        candidate = bom + manifest({"test": "node --test"}, dependencies={"left-pad": "1.3.0"})
        self.assertIs(candidate, restored(original, candidate))
        self.assertEqual({"test": "node --test"}, json.loads(
            restored(original, bom + manifest({"test": "exit 0"})))["scripts"])

    def test_a_file_a_definition_link_points_at_follows_the_link_name(self):
        self.assertEqual(b"a=1\n", original_scripts.restored("config/npmrc", b"a=1\n", b"script-shell=true\n",
                                                             ".npmrc"))
        merged = original_scripts.restored("config/pkg.json", manifest({"test": "node --test"}),
                                           manifest({"test": "exit 0"}), "package.json")
        self.assertEqual({"test": "node --test"}, json.loads(merged)["scripts"])


class RestoredConfigurationTests(unittest.TestCase):
    def test_configuration_returns_to_base_whole(self):
        for path in (".npmrc", "web/.npmrc", "pnpm-workspace.yaml", "turbo.json", ".yarn/releases/yarn-4.5.0.cjs"):
            with self.subTest(path=path):
                self.assertIsNone(restored(None, b"script-shell=/usr/bin/true\n", path))
                self.assertEqual(b"a=1\n", restored(b"a=1\n", None, path))
                self.assertEqual(b"a=1\n", restored(b"a=1\n", b"a=1\nnode-options=--test-name-pattern=mul\n", path))


class PlanTests(unittest.TestCase):
    def test_only_files_that_restoring_changes_are_planned(self):
        pairs = {
            "package.json": (manifest({"test": "node --test"}),
                             manifest({"test": "node --test"}, dependencies={"left-pad": "1.3.0"})),
            "packages/calc/package.json": (manifest({"test": "node --test"}), manifest({"test": "exit 0"})),
            ".npmrc": (None, b"script-shell=/usr/bin/true\n"),
            "packages/new/package.json": (None, manifest({"test": "node --test"})),
            "packages/esm/package.json": (None, b'{"type": "module"}'),
            "web/.npmrc": (b"a=1\n", b"a=1\n"),
        }
        self.assertEqual({".npmrc": None, "packages/calc/package.json": manifest({"test": "node --test"}),
                          "packages/new/package.json": None}, original_scripts.plan(pairs))
        self.assertEqual({}, original_scripts.plan({}))


class CoChangedTests(unittest.TestCase):
    def co_changed(self, before, after, changed, path="package.json"):
        return original_scripts.co_changed({path: (manifest(before), manifest(after))}, set(changed))

    def test_a_changed_test_script_with_a_changed_file_it_runs(self):
        self.assertEqual([("run-tests.js", "package.json", "test")], self.co_changed(
            {"test": "node run-tests.js"}, {"test": "node run-tests.js && echo done"}, {"run-tests.js", "calc.js"}))
        # A moved build step: the original pretest runs a file the candidate deleted.
        self.assertEqual([("build.js", "package.json", "pretest")], self.co_changed(
            {"pretest": "node ./build.js", "test": "node --test"},
            {"pretest": "node scripts/build.js", "test": "node --test"}, {"build.js", "scripts/build.js"}))
        # Paths are relative to the package.json, and runner configuration beside it counts too.
        self.assertEqual([("packages/calc/.mocharc.yml", "packages/calc/package.json", "test:unit"),
                          ("packages/calc/run.js", "packages/calc/package.json", "test:unit")],
                         self.co_changed({"test:unit": "mocha && node run.js"}, {"test:unit": "mocha --bail"},
                                         {"packages/calc/run.js", "packages/calc/.mocharc.yml", "run.js",
                                          "jest.config.js"}, "packages/calc/package.json"))

    def test_nothing_when_the_script_is_unchanged_names_no_changed_file_or_is_not_a_test_script(self):
        changed = {"run-tests.js", "server.js", "scripts/run-tests.js"}
        for before, after in (({"test": "node run-tests.js"}, {"test": "node run-tests.js", "lint": "eslint ."}),
                              ({"start": "node server.js"}, {"start": "node --watch server.js"}),
                              ({"test": "node --test"}, {"test": "node --test --test-reporter=spec"}),
                              ({"test": "node lib/run-tests.js"}, {"test": "node lib/run-tests.js -v"})):
            with self.subTest(after=after):
                self.assertEqual([], self.co_changed(before, after, changed))
        self.assertEqual([("jest.config.js", "package.json", "test")],
                         self.co_changed({"test": "jest"}, {"test": "jest --silent"}, {"jest.config.js"}))
        self.assertEqual([], original_scripts.co_changed({"package.json": (None, manifest({"test": "x"}))}, {"x"}))


def receipt(exit_code=0, **fields):
    return {"command": "npm test --silent", "exit_code": exit_code, "timed_out": False, "results": None,
            "restored": ["package.json"], **fields}


RESULTS = {"passed": ["a"], "failed": [], "skipped": [], "total": 1, "complete": True}


class JudgeTests(unittest.TestCase):
    def judged(self, run, base_exit=0, base_results=None, compare=None):
        fail, unverified, notes = [], [], []
        original_scripts.judge(run, {"receipt": receipt(base_exit, results=base_results)}, fail, unverified, notes,
                               compare=compare)
        return fail, unverified, notes

    def test_a_pass_under_the_original_definitions_is_noted(self):
        fail, unverified, notes = self.judged(receipt(0, left_out_tests=["test/feature.test.js"]))
        self.assertEqual(([], []), (fail, unverified))
        self.assertIn("also passes on the candidate", notes[0])
        self.assertIn("1 added test file(s) were left out", notes[0])

    def test_a_failure_under_the_original_definitions_fails_when_base_passed(self):
        fail, unverified, notes = self.judged(receipt(1))
        self.assertEqual(([], []), (unverified, notes))
        self.assertIn("as originally defined passes on base but fails on the candidate", fail[0])
        self.assertIn("keep the files those scripts run", fail[0])
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

    def test_a_test_script_changed_with_a_file_it_runs_is_unproven_without_a_run(self):
        fail, unverified, notes = self.judged(
            {"restored": ["package.json"], "co_changed": [("run-tests.js", "package.json", "test")]})
        self.assertEqual(([], []), (fail, notes))
        self.assertIn('run-tests.js with the "test" script in package.json', unverified[0])

    def test_per_test_results_on_both_runs_are_compared_like_the_suite(self):
        seen = []

        def compare(run, fail, unverified, notes):
            seen.append(run)
            fail.append("Tests that pass on base did not pass on the candidate: test/calc.test.js::add")
            notes.append("ignored")

        run = receipt(0, results={**RESULTS, "passed": ["test/feature.test.js::mul"]})
        fail, unverified, notes = self.judged(run, base_results=RESULTS, compare=compare)
        self.assertEqual([run], seen)
        self.assertEqual(([], []), (unverified, notes))
        self.assertIn("as originally defined (restored: package.json): Tests that pass on base", fail[0])
        # A clean comparison is the proof; without results on both runs the exit code decides.
        self.assertEqual(([], []), self.judged(run, base_results=RESULTS, compare=lambda *found: None)[:2])
        self.assertEqual(([], []), self.judged(run, compare=compare)[:2])
        self.assertEqual(1, len(seen))


class DecidedByExitCodeTests(unittest.TestCase):
    def test_only_a_passing_suite_without_a_per_test_comparison_needs_the_run(self):
        base = {"receipt": receipt(0)}
        decided = original_scripts.decided_by_exit_code
        self.assertTrue(decided(receipt(0), base))
        self.assertTrue(decided(receipt(0, results=RESULTS), base))
        self.assertTrue(decided(receipt(0), {"receipt": receipt(0, results=RESULTS)}))
        self.assertFalse(decided(receipt(0, results=RESULTS), {"receipt": receipt(0, results=RESULTS)}))
        self.assertFalse(decided(receipt(1), base))
        self.assertFalse(decided(receipt(0, timed_out=True), base))
        self.assertFalse(decided(receipt(0), None))


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class PrepareTests(unittest.TestCase):
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
                    "packages/demo/package.json": manifest({"test": "node index.js"}),
                    "packages/demo/index.js": "console.log(1);\n",
                    "test/package.json": manifest({"test": "fixture"}), ".npmrc": "fund=false\n",
                    "config/npmrc": "fund=false\n", "test/calc.test.js": self.CALC_TEST, "test/run.sh": "exit 0\n",
                    "test/fixtures/v1.json": "{}\n"})
        (self.root / "test/run.sh").chmod(0o755)
        (self.root / "web").mkdir()
        (self.root / "web/.npmrc").symlink_to("../config/npmrc")
        (self.root / "test/fixtures/current.json").symlink_to("v1.json")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")

    def write(self, files):
        for path, text in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_bytes(text if isinstance(text, bytes) else text.encode())

    def prepare(self, patch=None):
        changes = verify.changed_files(self.root, self.base)
        return original_scripts.prepare(self.root, self.base, patch, changes, verify.is_test_path)

    def objects(self):
        return git(self.root, "count-objects")

    def test_changed_definitions_are_restored_and_tests_are_as_the_original_has_them(self):
        self.write({"packages/a/package.json": manifest({"test": "exit 0"}), "packages/b/.npmrc": "x=1\n",
                    "packages/a/calc.js": "exports.add = (a, b) => a - b;\n",
                    "test/calc.test.js": "test('add', () => assert.equal(add(2, 3), -1));\n",
                    "test/feature.test.js": "test('mul')\n"})
        (self.root / ".npmrc").unlink()
        (self.root / "test/run.sh").unlink()
        (self.root / "test/fixtures/current.json").unlink()
        (self.root / "test/fixtures/current.json").symlink_to("v2.json")
        prepared = self.prepare()
        self.assertEqual({".npmrc": (b"fund=false\n", 0o644),
                          "packages/a/package.json": (manifest({"test": "node --test test/a.test.js"}), 0o644),
                          "test/calc.test.js": (self.CALC_TEST.encode(), 0o644),
                          "test/run.sh": (b"exit 0\n", 0o755)}, prepared["files"])
        self.assertEqual({"test/fixtures/current.json": "v1.json"}, prepared["links"])
        self.assertEqual(["packages/b/.npmrc", "test/feature.test.js"], prepared["removed"])
        self.assertEqual([".npmrc", "packages/a/package.json", "packages/b/.npmrc"], prepared["definitions"])
        self.assertEqual(["test/calc.test.js", "test/fixtures/current.json", "test/run.sh"], prepared["tests"])
        self.assertEqual(["test/feature.test.js"], prepared["left_out"])
        self.assertEqual([], prepared["co_changed"])

    def test_nothing_runs_when_no_definition_changed_or_restoring_changes_nothing(self):
        self.write({"packages/a/calc.js": "exports.add = (a, b) => a - b;\n", "test/feature.test.js": "x\n"})
        self.assertIsNone(self.prepare())
        self.write({"packages/a/package.json": manifest({"test": "node --test test/a.test.js"},
                                                        dependencies={"left-pad": "1.3.0"})})
        self.assertIsNone(self.prepare())

    def test_a_definition_under_a_test_path_returns_whole(self):
        self.write({"test/package.json": manifest({"test": "changed fixture"})})
        prepared = self.prepare()
        self.assertEqual({"test/package.json": (manifest({"test": "fixture"}), 0o644)}, prepared["files"])
        self.assertEqual(["test/package.json"], prepared["definitions"])

    def test_an_added_package_is_removed_and_a_removed_package_stays_removed(self):
        self.write({"packages/aaa/package.json": manifest({"test": "node -e 0"}, name="aaa")})
        for name in ("package.json", "index.js"):
            (self.root / "packages/demo" / name).unlink()
        self.assertEqual(["packages/aaa/package.json"], self.prepare()["removed"])
        # Deleting only the manifest drops a package that still has code: it comes back.
        (self.root / "packages/aaa/package.json").unlink()
        self.write({"packages/demo/index.js": "console.log(1);\n"})
        self.assertEqual({"packages/demo/package.json": (manifest({"test": "node index.js"}), 0o644)},
                         self.prepare()["files"])

    def test_a_file_a_definition_link_points_at_is_a_definition(self):
        self.write({"config/npmrc": "fund=false\nscript-shell=/usr/bin/true\n"})
        prepared = self.prepare()
        self.assertEqual({"config/npmrc": (b"fund=false\n", 0o644)}, prepared["files"])
        self.assertEqual(["config/npmrc"], prepared["definitions"])

    def test_a_test_script_changed_with_a_file_it_runs(self):
        self.write({"packages/a/package.json": manifest({"test": "node --test test/a.test.js && node calc.js"}),
                    "packages/a/calc.js": "exports.add = (a, b) => a - b;\n"})
        self.assertEqual([], self.prepare()["co_changed"])  # the original script does not run calc.js
        self.write({"packages/a/package.json": manifest({"test": "node run.js"}), "packages/a/run.js": "all\n"})
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "runner")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.write({"packages/a/package.json": manifest({"test": "node run.js && echo done"}),
                    "packages/a/run.js": "narrowed\n"})
        self.assertEqual([("packages/a/run.js", "packages/a/package.json", "test")], self.prepare()["co_changed"])

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
        prepared = self.prepare(patch)
        self.assertEqual({"packages/a/package.json": (wide, 0o644),
                          "test/calc.test.js": (reviewed_test.encode(), 0o644)}, prepared["files"])
        self.assertEqual(before, self.objects())
        self.assertIsNone(self.prepare())
        patch.write_text("not a patch\n")
        with self.assertRaises(ValueError):
            self.prepare(patch)

    def test_a_linked_original_definition_cannot_be_restored(self):
        (self.root / "package.json").unlink()
        (self.root / "package.json").symlink_to("packages/a/package.json")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "link")
        self.base = git(self.root, "rev-parse", "HEAD")
        (self.root / "package.json").unlink()
        self.write({"package.json": manifest({"test": "exit 0"})})
        with self.assertRaisesRegex(ValueError, "package.json is not a regular file"):
            self.prepare()


class InstallTests(unittest.TestCase):
    def test_restored_files_and_links_are_written_inside_the_tree_and_never_through_a_link(self):
        with tempfile.TemporaryDirectory() as temp:
            tree, outside, staging = Path(temp) / "tree", Path(temp) / "outside", Path(temp) / "staging"
            (tree / "web").mkdir(parents=True)
            outside.mkdir()
            (tree / ".npmrc").write_text("script-shell=true\n")
            (outside / ".npmrc").write_text("keep\n")
            (tree / "linked").symlink_to(outside, target_is_directory=True)
            (tree / "test").symlink_to(outside, target_is_directory=True)
            prepared = {"files": {"web/package.json": (b"{}\n", 0o644), "test/run.sh": (b"exit 1\n", 0o755)},
                        "links": {"test/fixtures/current.json": "v1.json"}, "removed": [".npmrc", "linked/.npmrc"]}
            original_scripts.install(tree, prepared, staging)
            self.assertFalse((tree / ".npmrc").exists())
            self.assertFalse((tree / "linked").is_symlink())
            self.assertEqual("keep\n", (outside / ".npmrc").read_text())
            self.assertEqual(["keep\n"], [path.read_text() for path in outside.iterdir()])
            self.assertEqual("{}\n", (tree / "web" / "package.json").read_text())
            self.assertEqual("exit 1\n", (tree / "test" / "run.sh").read_text())
            self.assertEqual(0o755, (tree / "test" / "run.sh").stat().st_mode & 0o777)
            self.assertFalse((tree / "test").is_symlink())
            self.assertEqual("v1.json", os.readlink(tree / "test" / "fixtures" / "current.json"))
            self.assertEqual("{}\n", (staging / "web" / "package.json").read_text())
            with self.assertRaises(ValueError):
                scratch_overlay.apply(tree, {"web/package.json": staging / "web" / "package.json"}, None,
                                      removed=["web/package.json"])


class OutsideTests(unittest.TestCase):
    def test_the_proof_tree_folder_is_outside_the_workspace_and_removed_afterwards(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "project"
            workspace.mkdir()
            with original_scripts.outside(workspace) as holder:
                self.assertTrue(holder.is_dir())
                self.assertFalse(holder.is_relative_to(workspace.resolve()))
            self.assertFalse(holder.exists())
            # A temporary folder inside the workspace would still see the candidate's configuration.
            with mock.patch.object(tempfile, "tempdir", str(workspace)):
                with self.assertRaisesRegex(ValueError, "inside the workspace"):
                    with original_scripts.outside(workspace):
                        pass
            self.assertEqual([], list(workspace.iterdir()))


if __name__ == "__main__":
    unittest.main()
