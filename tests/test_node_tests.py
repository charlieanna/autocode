"""Real Node events and hostile evidence controls for named case proof."""

import contextlib
import json
import os
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_node_tests as node_tests
import autocode_regression as regression
import autocode_verify as verify

from tests.test_verify import Project


class CommandTests(unittest.TestCase):
    def test_only_direct_test_commands_are_instrumented(self):
        for command in ("node --test tests/a.cjs", '/usr/bin/node --test "tests/a b.cjs"'):
            self.assertTrue(node_tests.command_words(command))
            self.assertTrue(verify.expects_results(None, command))
        for command in (
            "node tests/a.cjs --test",
            "npm test",
            "node --test | cat",
            "node --test; true",
            "node --test\ntrue",
            "node --test --watch",
            "node --test --test-reporter=spec",
            "node -- tests/a.cjs --test",
            "node --test $(echo tests/a.cjs)",
            'node --test "unterminated',
        ):
            with self.subTest(command=command):
                self.assertIsNone(node_tests.command_words(command))
                self.assertEqual(command, node_tests.instrument(command, "/tmp/proof.jsonl"))

    def test_configured_node_collector_keeps_runtime_and_options_for_changed_tests(self):
        command = "/configured/node --no-warnings --test --require ./setup.cjs --test-concurrency=1 old.test.cjs"
        framework = verify.command_framework(command)
        self.assertEqual("node", framework.name)
        self.assertEqual(
            [
                "/configured/node",
                "--no-warnings",
                "--test",
                "--require",
                "./setup.cjs",
                "--test-concurrency=1",
                "tests/new test.cjs",
            ],
            shlex.split(framework.targeted(["tests/new test.cjs", "tests/test_other.py"])),
        )

    def test_preload_options_before_test_are_not_mistaken_for_a_script(self):
        for options in (
            ["--require", "./setup.cjs"],
            ["-r", "./setup.cjs"],
            ["--import", "./setup.mjs"],
            ["--require=./setup.cjs"],
        ):
            with self.subTest(options=options):
                words = ["/configured/node", *options, "--test", "old.test.cjs"]
                framework = verify.command_framework(shlex.join(words))
                self.assertEqual("node", framework.name)
                self.assertEqual([*words[:-1], "new.test.cjs"], shlex.split(framework.targeted(["new.test.cjs"])))
        for command in (
            "node --require ./setup.cjs script.cjs --test",
            "node -r ./setup.cjs -- --test",
            "node --require --test old.test.cjs",
            "node --future-option --test old.test.cjs",
        ):
            with self.subTest(command=command):
                self.assertIsNone(verify.command_framework(command))
                self.assertEqual(command, node_tests.instrument(command, "/tmp/proof.jsonl"))

    def test_unknown_option_arity_keeps_original_suite_and_wrappers_are_not_collectors(self):
        for command in (
            "node --test --future-option value old.test.cjs",
            'node --test --future-option value "old suite"/*.test.cjs',
            "node --test old.test.cjs --test-name-pattern=kept",
            "node --test --require",
        ):
            with self.subTest(command=command):
                framework = verify.command_framework(command)
                self.assertEqual(command, framework.targeted(["new.test.cjs"]))
        for command in (
            "env NODE_OPTIONS=--no-warnings node --test old.test.cjs",
            "node script.cjs --test",
            "node --test old.test.cjs && true",
        ):
            with self.subTest(command=command):
                self.assertIsNone(verify.command_framework(command))

    def test_targeted_paths_keep_the_option_boundary_and_cannot_become_flags(self):
        for command, expected in [
            ("node --test -- old.test.cjs", ["node", "--test", "--", "./--new.test.cjs"]),
            ("node --test old.test.cjs", ["node", "--test", "./--new.test.cjs"]),
        ]:
            with self.subTest(command=command):
                self.assertEqual(expected, shlex.split(verify.command_framework(command).targeted(["--new.test.cjs"])))

    def test_execution_identity_binds_configured_node_bytes_and_keeps_incomplete_baseline_fresh(self):
        project = Project({"product.cjs": "module.exports=1;\n"})
        self.addCleanup(project.close)
        runtime = Path(project.temp.name) / "runtime"
        runtime.mkdir()
        node = runtime / "node"
        node.write_text("fake Node version one\n")
        node.chmod(0o755)
        preload = runtime / "setup.cjs"
        preload.write_text("globalThis.proofSetup=17;\n")
        command = shlex.join([str(node), "--test", "--require", str(preload), "tests/one.test.cjs"])
        before = verify.execution_identity(project.root, command=command)
        self.assertEqual(str(node.resolve()), before["interpreter"]["path"])
        self.assertFalse(before["reuse_supported"])
        node.write_text("fake Node version two\n")
        after = verify.execution_identity(project.root, command=command)
        self.assertNotEqual(before["interpreter"]["sha256"], after["interpreter"]["sha256"])
        baseline = verify.baseline_identity(project.root, command=command)
        self.assertEqual(after["interpreter"], baseline["interpreter"])
        self.assertFalse(baseline["cache_binding_complete"])
        self.assertFalse(baseline["reuse_supported"])
        self.assertEqual("fresh_execution_only", baseline["cache_policy"])

    def test_node_identity_resolves_relative_runtimes_and_path_from_the_workspace(self):
        project = Project({"product.cjs": "module.exports=1;\n"})
        self.addCleanup(project.close)
        controller = tempfile.TemporaryDirectory(prefix="node-controller-")
        self.addCleanup(controller.cleanup)
        controller_root = Path(controller.name).resolve()
        for root, version in ((project.root, "workspace"), (controller_root, "controller")):
            for relative in ("node", "runtime/node"):
                binary = root / relative
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_text(version + "\n")
                binary.chmod(0o755)
        original_path = os.environ.get("PATH", "")
        cases = [
            ("./node", None, "node"),
            ("runtime/node", None, "runtime/node"),
            ("node", "runtime", "runtime/node"),
            ("node", "", "node"),
            ("node", str(project.root / "runtime"), "runtime/node"),
        ]
        with contextlib.chdir(controller_root):
            for runtime, entry, relative in cases:
                with self.subTest(runtime=runtime, path_entry=entry):
                    search_path = original_path if entry is None else entry + os.pathsep + original_path
                    with mock.patch.dict(os.environ, {"PATH": search_path}):
                        binary = project.root / relative
                        before = verify.execution_identity(project.root, command=runtime + " --test one.cjs")
                        self.assertEqual(str(binary.resolve()), before["interpreter"]["path"])
                        binary.write_text(binary.read_text() + "changed bytes\n")
                        after = verify.execution_identity(project.root, command=runtime + " --test one.cjs")
                        self.assertNotEqual(before["interpreter"]["sha256"], after["interpreter"]["sha256"])
                        self.assertFalse(after["reuse_supported"])


@unittest.skipUnless(shutil.which("node"), "Node is required for the actual test-runner protocol")
class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / "evidence"
        self.framework = verify.Framework("node", "node --test")

    def run_node(self, files, *, label="probe"):
        for name, source in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(source)
        command = "node --test " + shlex.join(list(files))
        return verify.run_suite(self.framework, command, self.root, self.evidence, label, timeout=20)

    def test_named_outcomes_nested_suites_and_same_name_in_other_files(self):
        receipt = self.run_node(
            {
                "a.test.cjs": "const {test,describe}=require('node:test');"
                "const assert=require('node:assert/strict');"
                "test('test_ok',()=>{});test('test_bad',()=>assert.fail('wrong'));"
                "test('test_skip',{skip:true},()=>{});"
                "describe('group',()=>{test('test_nested',()=>{});"
                "test('test_todo',{todo:true},()=>assert.fail('todo'));});",
                "b.test.cjs": "require('node:test')('test_ok',()=>{});",
            }
        )
        self.assertEqual(1, receipt["exit_code"], receipt)
        self.assertEqual(
            {
                "passed": ["a.test.cjs::group::test_nested", "a.test.cjs::test_ok", "b.test.cjs::test_ok"],
                "failed": ["a.test.cjs::test_bad"],
                "skipped": ["a.test.cjs::group::test_todo", "a.test.cjs::test_skip"],
                "collection_errors": [],
                "uncollected": [],
                "total": 6,
                "complete": True,
            },
            receipt["results"],
        )

    def test_forged_stdout_empty_file_and_early_exit_do_not_prove_a_named_case(self):
        for source in (
            "console.log('PASS test_fake');",
            "console.log(JSON.stringify({type:'pass',name:'test_fake'}));",
            "require('node:test')('test_fake',()=>{process.exit(0)});",
        ):
            with self.subTest(source=source):
                receipt = self.run_node({"test_fake.cjs": source})
                self.assertTrue(receipt["results_expected"])
                self.assertEqual([], (receipt["results"] or {}).get("passed", []), receipt)

    def test_collection_and_hook_errors_do_not_reproduce_a_bug(self):
        cases = [
            ("require('./missing.cjs');", ["a.cjs::[collection]"]),
            (
                "const {test,before}=require('node:test');before(()=>{throw Error('setup')});test('test_case',()=>{});",
                [],
            ),
        ]
        for source, uncollected in cases:
            with self.subTest(source=source):
                receipt = self.run_node({"a.cjs": source})
                self.assertEqual(1, receipt["exit_code"])
                self.assertTrue(receipt["results"]["collection_errors"], receipt)
                self.assertEqual(receipt["results"]["failed"], receipt["results"]["collection_errors"])
                # Only a file that never imported is uncollected; a failed hook executed (#503).
                self.assertEqual(uncollected, receipt["results"]["uncollected"], receipt)

    def test_duplicate_identities_are_ambiguous_even_when_node_exits_zero(self):
        receipt = self.run_node(
            {"a.cjs": "const {test}=require('node:test');for(let i=0;i<2;i++)test('test_same',()=>{});"}
        )
        self.assertEqual(0, receipt["exit_code"])
        self.assertIsNone(receipt["results"])

    def test_aborted_test_is_not_a_passing_case(self):
        receipt = self.run_node(
            {
                "a.cjs": "const ac=new AbortController(); ac.abort();"
                "require('node:test')('test_cancel',{signal:ac.signal},()=>{});"
            }
        )
        self.assertEqual(1, receipt["exit_code"], receipt)
        result = receipt["results"]
        self.assertIsNotNone(result, receipt)
        self.assertNotIn("a.cjs::test_cancel", result["passed"])
        self.assertIn("a.cjs::test_cancel", result["collection_errors"])
        self.assertEqual([], result["uncollected"])

    def test_timeout_and_running_abort_keep_complete_nonpassing_results(self):
        # Keep Node's event loop alive until its test timeout, including on Node 22.
        sources = {
            "testTimeoutFailure": "test('test_cancel', {timeout:100}, async t=>{"
            "const hold=setTimeout(()=>{},1000);t.after(()=>clearTimeout(hold));"
            "await new Promise(()=>{});});",
            "testAborted": "const ac=new AbortController();"
            "test('test_cancel',{signal:ac.signal,timeout:1000},async()=>{"
            "setImmediate(()=>ac.abort(new Error('fixture abort')));await new Promise(()=>{});});",
        }
        for failure_type, source in sources.items():
            with self.subTest(failure_type=failure_type):
                receipt = self.run_node({"a.cjs": "const {test}=require('node:test');test('test_ok',()=>{});" + source})
                self.assertEqual(1, receipt["exit_code"], receipt)
                self.assertFalse(receipt["timed_out"], receipt)
                events = [json.loads(line) for line in (self.evidence / "probe.node.jsonl").read_text().splitlines()]
                failure = next(row for row in events if row.get("name") == "test_cancel" and row["type"] == "fail")
                self.assertEqual(failure_type, failure["failure_type"])
                self.assertEqual(
                    (1, 0, 1), tuple(events[-2]["counts"][name] for name in ("passed", "failed", "cancelled"))
                )
                self.assertEqual(
                    {
                        "passed": ["a.cjs::test_ok"],
                        "failed": ["a.cjs::test_cancel"],
                        "skipped": [],
                        "collection_errors": ["a.cjs::test_cancel"],
                        "uncollected": [],
                        "total": 2,
                        "complete": True,
                    },
                    receipt["results"],
                )

    def regression_proof(self, *, ordinary):
        preamble = "const {test}=require('node:test');const assert=require('node:assert/strict');const p=require('./product.cjs');\n"
        healthy = "test('test_existing',()=>assert.equal(p.existing(),1));\n"
        cancelled = "test('test_increment',{timeout:100},async t=>{const hold=setTimeout(()=>{},1000);"
        cancelled += "t.after(()=>clearTimeout(hold));assert.equal(await p.increment(4),5);});\n"
        restored = "test('test_restore',async()=>{for(const x of [-7,-1,0,4,16]){const value=p.increment(x);"
        restored += "assert.ok(value instanceof Promise);const pending=Symbol('pending');"
        restored += "assert.equal(await Promise.race([value,Promise.resolve(pending)]),x+1);}});\n"
        seed = preamble + healthy + (cancelled if ordinary else "")
        project = Project(
            {
                "product.cjs": "exports.existing=()=>1;exports.increment=x=>new Promise(()=>{});\n",
                "suite.test.cjs": seed,
            }
        )
        self.addCleanup(project.close)
        command = "node --test suite.test.cjs"
        framework = verify.Framework("node", command, node_files=["suite.test.cjs"])
        baseline = verify.baseline(
            project.root,
            project.base,
            project.evidence / "baseline-run",
            framework=framework,
            suite_command=command,
            timeout=20,
        )
        project.write(
            {
                "product.cjs": "exports.existing=()=>1;exports.increment=x=>Promise.resolve(x+1);\n",
                "suite.test.cjs": seed + (restored if ordinary else cancelled),
            }
        )
        proof = verify.verify(
            project.root,
            project.base,
            project.evidence / "proof",
            framework=framework,
            suite_command=command,
            base_suite=baseline,
            timeout=20,
            new_behavior=False,
        )
        self.assertEqual("derived:node", proof["commands"]["regression_source"], proof)
        self.assertTrue((project.root / "suite.test.cjs").read_text().startswith(seed))
        for receipt in proof["checks"].values():
            self.assertFalse(receipt["timed_out"], receipt)
            self.assertTrue(verify.command_receipt.completed(receipt), receipt)
        return proof

    def test_cancellation_alone_cannot_reproduce_a_bugfix(self):
        proof = self.regression_proof(ordinary=False)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual([], proof["fail_to_pass"], proof)
        self.assertEqual(["suite.test.cjs::test_existing"], proof["pass_to_pass"], proof)
        base = proof["checks"]["regression_on_base"]["results"]
        self.assertTrue(base["complete"])
        self.assertEqual(["suite.test.cjs::test_increment"], base["failed"])
        self.assertEqual(base["failed"], base["collection_errors"])

    def test_ordinary_regression_restores_bugfix_beside_cancelled_seed(self):
        proof = self.regression_proof(ordinary=True)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["suite.test.cjs::test_restore"], proof["fail_to_pass"], proof)
        self.assertEqual(["suite.test.cjs::test_existing"], proof["pass_to_pass"], proof)
        base = proof["checks"]["regression_on_base"]["results"]
        self.assertEqual(["suite.test.cjs::test_increment"], base["collection_errors"])
        self.assertEqual(["suite.test.cjs::test_increment", "suite.test.cjs::test_restore"], base["failed"])
        candidate = proof["checks"]["suite_on_candidate"]
        self.assertEqual(0, candidate["exit_code"])
        self.assertTrue(candidate["results"]["complete"])
        self.assertEqual(3, len(candidate["results"]["passed"]))

    def test_incomplete_malformed_or_inconsistent_evidence_is_never_credited(self):
        receipt = self.run_node({"a.cjs": "require('node:test')('test_case',()=>{});"})
        self.assertEqual(["a.cjs::test_case"], receipt["results"]["passed"])
        path = self.evidence / "probe.node.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        broken_counts = json.loads(json.dumps(rows))
        broken_counts[-2]["counts"]["passed"] += 1
        variants = [
            rows[:-1],
            rows[:1] + rows[2:],
            rows[:-2] + rows[-1:],
            rows + [{"type": "pass"}],
            rows[:2] + [rows[2]] + rows[2:],
            broken_counts,
            rows[:-2] + [{"type": "summary", "counts": []}, rows[-1]],
            [None],
            [{"protocol": "autocode-node-tests", "version": 2}, *rows[1:]],
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                path.write_text("\n".join(map(json.dumps, variant)) + "\n")
                self.assertIsNone(node_tests.results(path))
        path.write_text("{not JSON}\n")
        self.assertIsNone(node_tests.results(path))
        path.unlink()
        self.assertIsNone(node_tests.results(path))

    def test_generic_suite_keeps_its_exit_code_and_does_not_reuse_stale_results(self):
        self.run_node({"a.cjs": "require('node:test')('test_case',()=>{});"})
        receipt = verify.run_suite(
            self.framework, 'node -e "process.exit(0)"', self.root, self.evidence, "probe", timeout=20
        )
        self.assertEqual(0, receipt["exit_code"])
        self.assertFalse(receipt["results_expected"])
        self.assertIsNone(receipt["results"])
        # A subsequent failed Node launch must delete the previous named report.
        receipt = verify.run_suite(
            self.framework, "/missing/node --test a.cjs", self.root, self.evidence, "probe", timeout=20
        )
        self.assertTrue(receipt["results_expected"])
        self.assertIsNone(receipt["results"])

    def test_explicit_node_suite_overrides_mocha_and_proves_named_restore_and_preservation(self):
        preamble = "const {test}=require('node:test');const assert=require('node:assert/strict');"
        preamble += "const p=require('../product.cjs');\n"
        preserved = (
            "test('test_preserve',()=>{assert.equal(globalThis.proofSetup,17);assert.equal(p.existing(),1);});\n"
        )
        project = Project(
            {
                "product.cjs": "exports.existing=()=>1;exports.increment=x=>x+2;\n",
                "package.json": json.dumps(
                    {"devDependencies": {"mocha": "1.0.0"}, "scripts": {"test": "mocha tests/*.cjs"}}
                ),
                "setup.cjs": "globalThis.proofSetup=17;\n",
                "tests/seed.test.cjs": preamble + preserved,
            }
        )
        self.addCleanup(project.close)
        framework = verify.detect_framework(project.root)
        self.assertEqual("mocha", framework.name)
        node = str(Path(shutil.which("node")).resolve())
        words = [
            node,
            "--no-warnings",
            "--require",
            "./setup.cjs",
            "--test",
            "--test-concurrency=1",
            "tests/seed.test.cjs",
        ]
        command = shlex.join(words)
        baseline = verify.baseline(
            project.root,
            project.base,
            project.evidence / "baseline-run",
            framework=framework,
            suite_command=command,
            timeout=20,
        )
        self.assertEqual("passing", baseline["health"], baseline)
        project.write(
            {
                "product.cjs": "exports.existing=()=>1;exports.increment=x=>x+1;\n",
                "tests/arena.test.cjs": preamble
                + preserved
                + "test('test_restore',()=>assert.equal(p.increment(4),5));\n",
            }
        )
        proof = verify.verify(
            project.root,
            project.base,
            project.evidence / "proof",
            framework=framework,
            suite_command=command,
            base_suite=baseline,
            timeout=20,
        )
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual("node", proof["framework"]["name"], proof)
        self.assertEqual("derived:node", proof["commands"]["regression_source"], proof)
        self.assertEqual([*words[:-1], "tests/arena.test.cjs"], shlex.split(proof["commands"]["regression"]), proof)
        self.assertEqual(["tests/arena.test.cjs::test_restore"], proof["fail_to_pass"], proof)
        self.assertEqual(["tests/arena.test.cjs::test_preserve"], proof["pass_to_pass"], proof)
        for receipt in proof["checks"].values():
            self.assertTrue(receipt["results_expected"], receipt)
            self.assertTrue(receipt["results"]["complete"], receipt)
            self.assertTrue(verify.command_receipt.completed(receipt), receipt)
        self.assertEqual(
            ["tests/arena.test.cjs::test_restore"], proof["checks"]["regression_on_base"]["results"]["failed"]
        )
        self.assertEqual(
            ["tests/arena.test.cjs::test_preserve"], proof["checks"]["regression_on_base"]["results"]["passed"]
        )
        for label, public_words in (
            ("long", words),
            ("short", [node, "-r", "./setup.cjs", "--test", "--test-concurrency=1", "tests/seed.test.cjs"]),
        ):
            with self.subTest(preload=label):
                state = {
                    "goal_contract": {"body": {"task_kind": "bugfix"}},
                    "base_commit": project.base,
                    "settings": {"regression": {"test_command": shlex.join(public_words), "test_timeout": 20}},
                }
                public = regression.prove(state, project.root, project.evidence / ("public-proof-" + label))
                self.assertEqual(verify.PASS, public["verdict"], public)
                self.assertEqual("node", public["framework"]["name"], public)
                self.assertEqual("derived:node", public["commands"]["regression_source"], public)
                self.assertEqual(
                    [*public_words[:-1], "tests/arena.test.cjs"], shlex.split(public["commands"]["regression"]), public
                )
                self.assertEqual(["tests/arena.test.cjs::test_restore"], public["fail_to_pass"], public)
                self.assertEqual(["tests/arena.test.cjs::test_preserve"], public["pass_to_pass"], public)
                receipt = json.loads(Path(public["path"]).read_text())
                for check in ("regression_on_base", "regression_on_candidate", "suite_on_candidate"):
                    self.assertTrue(receipt["checks"][check]["results"]["complete"], check)
