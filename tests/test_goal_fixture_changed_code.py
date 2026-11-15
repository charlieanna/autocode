"""Default offline provider review reasons require its exact behavioral tests."""

import contextlib
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import autocode_context as context
import blackbox_build_provider as blackbox
import goal_fixtures as fixture


class DefaultGreetingReasonsTests(unittest.TestCase):
    def test_visual_milestone_goodbye_reason_is_limited_to_exact_fixture_lines(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.chdir(folder):
            fixture.seed_greeting_workspace(folder)
            Path("bye.py").write_text(
                "import sys\nprint('Goodbye, ' + sys.argv[1])\n# batch 12345678-1234-1234-1234-123456789abc\n"
            )
            goodbye = Path("bye.py").read_text()
            rows = [
                {
                    "gap_id": f"bye:{line}",
                    "path": "bye.py",
                    "sha256": hashlib.sha256(goodbye.encode()).hexdigest(),
                    "line": line,
                    "status": "reason_required",
                    "mutation_outcomes": [],
                }
                for line in (1, 2, 3)
            ]
            data = {"regression_proof": {"path": "proof.json", "changed_code_evidence": {"obligations": rows}}}
            accepted = fixture.greeting_changed_code_reasons(data)
            self.assertEqual(["bye:1", "bye:2"], [row["gap_id"] for row in accepted])

            rows[1]["mutation_outcomes"] = ["survived"]
            self.assertEqual(["bye:1"], [row["gap_id"] for row in fixture.greeting_changed_code_reasons(data)])
            rows[1]["mutation_outcomes"] = []
            Path("bye.py").write_text(goodbye.replace("Goodbye", "Hello"))
            self.assertFalse(fixture.greeting_changed_code_reasons(data))

    def test_known_fixture_reasons_are_precise_and_cannot_waive_survivors_or_changed_inputs(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.chdir(folder):
            fixture.seed_greeting_workspace(folder)
            fixture.write_greeting_source(folder, revision="test")
            source = Path("greet.py").read_text()
            rows = [
                {
                    "gap_id": f"gap:{i}",
                    "path": "greet.py",
                    "sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "line": i,
                    "status": "reason_required",
                    "mutation_outcomes": [],
                }
                for i in range(1, len(source.splitlines()) + 1)
            ]
            data = {"regression_proof": {"path": "proof.json", "changed_code_evidence": {"obligations": rows}}}
            accepted = fixture.greeting_changed_code_reasons(data)
            self.assertEqual(6, len(accepted))
            self.assertEqual(6, len({row["reason"] for row in accepted}))
            self.assertIn("comment", accepted[0]["reason"])
            for mutation in (
                {"mutation_outcomes": ["survived"]},
                {"mutation_outcomes": ["not_run"]},
                {"sha256": "0" * 64},
                {"path": "unrelated.py"},
            ):
                changed = copy.deepcopy(data)
                for row in changed["regression_proof"]["changed_code_evidence"]["obligations"]:
                    row.update(mutation)
                with self.subTest(mutation=mutation):
                    self.assertEqual([], fixture.greeting_changed_code_reasons(changed))
            for name, original in (("greet.py", source), ("test_greeting.py", fixture.GREETING_TESTS)):
                with self.subTest(name=name):
                    Path(name).write_text("# missing or weakened\n")
                    self.assertEqual([], fixture.greeting_changed_code_reasons(data))
                    Path(name).write_text(original)


class ScriptedBuilderFilesReasonsTests(unittest.TestCase):
    def test_failed_proof_rejects_undecided_scripted_lines_instead_of_claiming_coverage(self):
        source = "def answer():\n    return 42\n"
        row = {
            "gap_id": "unverified-line",
            "path": "other.py",
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
            "line": 2,
            "status": "reason_required",
            "mutation_outcomes": [],
        }
        data = {
            "regression_proof": {
                "path": "proof.json",
                "verdict": "UNVERIFIED",
                "changed_code_evidence": {"obligations": [row]},
            }
        }
        decisions = fixture.scripted_files_changed_code_rejections(data, {"other.py": source})
        self.assertEqual(["unverified-line"], [item["gap_id"] for item in decisions])
        self.assertEqual("reject", decisions[0]["decision"])
        self.assertIn("other.py:2", decisions[0]["reason"])
        self.assertIn("regression test", decisions[0]["reason"])
        self.assertEqual(["proof.json", "other.py"], decisions[0]["evidence_refs"])
        for files in (None, {}):
            self.assertEqual([], fixture.scripted_files_changed_code_rejections(data, files))
        for change in (
            {"sha256": "0" * 64},
            {"path": "not-scripted.py"},
            {"line": 0},
            {"line": 3},
            {"line": True},
            {"mutation_outcomes": ["survived"]},
            {"status": "tested"},
        ):
            changed = copy.deepcopy(data)
            rows_of(changed)[0].update(change)
            with self.subTest(change=change):
                self.assertEqual([], fixture.scripted_files_changed_code_rejections(changed, {"other.py": source}))
        for verdict in ("PASS", None):
            data["regression_proof"]["verdict"] = verdict
            self.assertEqual([], fixture.scripted_files_changed_code_rejections(data, {"other.py": source}))

    def test_only_scripted_bytes_on_lines_without_a_mutation_site_receive_a_reason(self):
        oracle = "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(f())\n"
        digest = hashlib.sha256(oracle.encode()).hexdigest()
        rows = [
            {
                "gap_id": f"gap:{line}",
                "path": "shared/oracle.py",
                "sha256": digest,
                "line": line,
                "status": "reason_required",
                "mutation_outcomes": [],
            }
            for line in (3, 4)
        ]
        data = {
            "regression_proof": {
                "path": "proof.json",
                "changed_code_evidence": {"obligations": rows},
                "mutation_check": {
                    "collected": True,
                    "files": [{"path": "shared/oracle.py", "sha256": digest, "mutants": [{"line": 3}]}],
                },
            }
        }
        files = {"shared/oracle.py": oracle}
        accepted = fixture.scripted_files_changed_code_reasons(data, files)
        self.assertEqual(["gap:4"], [row["gap_id"] for row in accepted])
        self.assertIn("shared/oracle.py:4", accepted[0]["reason"])
        self.assertEqual(["proof.json", "shared/oracle.py"], accepted[0]["evidence_refs"])
        for name, change in (
            ("unscripted file", lambda d, f: f.clear()),
            ("other bytes", lambda d, f: f.update({"shared/oracle.py": oracle + "\n"})),
            (
                "mutation check not collected",
                lambda d, f: d["regression_proof"]["mutation_check"].update(collected=False),
            ),
            ("mutant reached the line", lambda d, f: rows_of(d)[1].update(mutation_outcomes=["survived"])),
            ("line inside a mutant's span", lambda d, f: mutants_of(d).append({"line": 4, "end_line": 4})),
            ("not a reason_required gap", lambda d, f: rows_of(d)[1].update(status="tested")),
        ):
            changed, changed_files = copy.deepcopy(data), dict(files)
            change(changed, changed_files)
            with self.subTest(name):
                self.assertEqual([], fixture.scripted_files_changed_code_reasons(changed, changed_files))

    def test_compacted_obligations_and_mutants_recover_reasons_and_rejections_or_fail_closed(self):
        oracle = "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(f())\n"
        digest = hashlib.sha256(oracle.encode()).hexdigest()
        compact_rows = [
            {
                "gap_id": f"changed-code:{line}" + "a" * 52,
                "handle": f"L{line}",
                "path": "shared/oracle.py",
                "sha256": digest,
                "line": line,
                "source_line": oracle.splitlines()[line - 1].strip(),
                "status": "reason_required",
                "mutation_outcomes": [],
                "evidence": ["runner.log"] * 6000,
            }
            for line in (3, 4)
        ]
        mutants = [{"line": 3, "evidence": ["runner.log"] * 6000}]
        data = {
            "stage": "tester",
            "regression_proof": {
                "path": "proof.json",
                "verdict": "UNVERIFIED",
                "changed_code_evidence": {"obligations": compact_rows},
                "mutation_check": {
                    "collected": True,
                    "files": [{"path": "shared/oracle.py", "sha256": digest, "mutants": mutants}],
                },
            },
        }
        files = {"shared/oracle.py": oracle}
        expected_reasons = fixture.quote_changed_lines(data, fixture.scripted_files_changed_code_reasons(data, files))
        expected_rejections = fixture.quote_changed_lines(
            data, fixture.scripted_files_changed_code_rejections(data, files)
        )
        with tempfile.TemporaryDirectory() as folder:
            packet, moved = context.compact(data, Path(folder) / "run/state.json")
            self.assertIn("regression_proof_obligations", moved)
            self.assertIn("regression_proof_mutation_files", moved)
            self.assertEqual(
                expected_reasons,
                fixture.quote_changed_lines(packet, fixture.scripted_files_changed_code_reasons(packet, files)),
            )
            self.assertEqual(
                expected_rejections,
                fixture.quote_changed_lines(packet, fixture.scripted_files_changed_code_rejections(packet, files)),
            )
            packet["context_artifact"]["sha256"] = "0" * 64
            self.assertEqual([], fixture.scripted_files_changed_code_reasons(packet, files))
            self.assertEqual([], fixture.scripted_files_changed_code_rejections(packet, files))


class ScriptedOracleReasonTests(unittest.TestCase):
    def setUp(self):
        oracle = (
            "import unittest\nfrom layout import height\nclass Layout(unittest.TestCase):\n"
            "    def test_required_height(self):\n        self.assertEqual(height(), 40)\n\n"
            "    def test_positive_height(self):\n        self.assertGreater(height(), 0)\n"
        )
        self.files = {
            "shared/oracle.py": oracle,
            "test_layout.py": oracle,
            "layout.py": "def height():\n    return 40\n",
        }
        digest = hashlib.sha256(oracle.encode()).hexdigest()
        self.row = {
            "gap_id": "current-oracle-gap",
            "path": "shared/oracle.py",
            "sha256": digest,
            "line": 8,
            "status": "reason_required",
            "mutation_outcomes": ["survived"],
        }
        self.mutant = {
            "line": 8,
            "end_line": 8,
            "operator": "drop_call",
            "before": "self.assertGreater(height(), 0)",
            "after": "None",
            "outcome": "survived",
            "source_sha256": digest,
            "failed_tests": [],
            "survived_tests": [
                "test_layout.Layout.test_required_height",
                "test_layout.Layout.test_positive_height",
            ],
        }
        self.data = {
            "regression_proof": {
                "path": "proof.json",
                "verdict": "PASS",
                "changed_code_evidence": {"obligations": [self.row]},
                "mutation_check": {
                    "collected": True,
                    "files": [{"path": "shared/oracle.py", "sha256": digest, "mutants": [self.mutant]}],
                },
            }
        }

    def test_exact_scripted_redundant_oracle_assertion_gets_an_equivalence_reason(self):
        decisions = fixture.scripted_files_changed_code_reasons(self.data, self.files)
        self.assertEqual(["current-oracle-gap"], [row["gap_id"] for row in decisions])
        self.assertEqual("equivalent_mutant", decisions[0]["reason_kind"])
        self.assertIn("height() == 40", decisions[0]["reason"])
        self.assertEqual(["proof.json", "shared/oracle.py"], decisions[0]["evidence_refs"])

    def test_other_source_gaps_mutants_or_unobserved_tests_get_no_oracle_reason(self):
        for change in (
            lambda: self.files.pop("shared/oracle.py"),
            lambda: self.files.update({"shared/oracle.py": self.files["shared/oracle.py"] + "\n"}),
            lambda: self.files.update({"test_layout.py": "other tests"}),
            lambda: self.files.update({"layout.py": "def height():\n    return 20\n"}),
            lambda: self.data["regression_proof"].update(verdict="FAIL"),
            lambda: self.data["regression_proof"]["mutation_check"].update(collected=False),
            lambda: self.row.update(path="other.py"),
            lambda: self.row.update(sha256="0" * 64),
            lambda: self.row.update(line=7),
            lambda: self.row.update(status="tested"),
            lambda: self.row.update(mutation_outcomes=["not_run"]),
            lambda: self.mutant.update(operator="constant_return"),
            lambda: self.mutant.update(before="other assertion"),
            lambda: self.mutant.update(after="False"),
            lambda: self.mutant.update(source_sha256="0" * 64),
            lambda: self.mutant.update(failed_tests=["test_layout.Layout.test_required_height"]),
            lambda: self.mutant.update(survived_tests=["test_layout.Layout.test_positive_height"]),
        ):
            self.setUp()
            change()
            self.assertEqual([], fixture.scripted_files_changed_code_reasons(self.data, self.files))


def rows_of(data):
    return data["regression_proof"]["changed_code_evidence"]["obligations"]


def mutants_of(data):
    return data["regression_proof"]["mutation_check"]["files"][0]["mutants"]


class HelloFixtureReasonsTests(unittest.TestCase):
    def test_only_the_known_observed_print_call_can_receive_a_reason(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.chdir(folder):
            Path("main.py").write_text(blackbox.HELLO_SOURCE)
            Path("test_main.py").write_text(blackbox.HELLO_TESTS)
            data = {
                "regression_proof": {
                    "path": "proof.json",
                    "changed_code_evidence": {
                        "obligations": [
                            {
                                "gap_id": "hello",
                                "path": "main.py",
                                "line": 1,
                                "sha256": hashlib.sha256(blackbox.HELLO_SOURCE.encode()).hexdigest(),
                                "status": "reason_required",
                                "mutation_outcomes": [],
                            }
                        ]
                    },
                }
            }
            spec = {"payloads": {"M1": {"main.py": blackbox.HELLO_SOURCE, "test_main.py": blackbox.HELLO_TESTS}}}
            self.assertEqual(1, len(blackbox.scripted_changed_code_reasons(spec, data)))
            data["regression_proof"]["changed_code_evidence"]["obligations"][0]["mutation_outcomes"] = ["not_run"]
            self.assertEqual([], blackbox.scripted_changed_code_reasons(spec, data))
            data["regression_proof"]["changed_code_evidence"]["obligations"][0]["mutation_outcomes"] = []
            # A file the fixture did not script, or other bytes than it wrote, get no reason.
            self.assertEqual([], blackbox.scripted_changed_code_reasons({"payloads": {}}, data))
            data["regression_proof"]["changed_code_evidence"]["obligations"][0]["sha256"] = "0" * 64
            self.assertEqual([], blackbox.scripted_changed_code_reasons(spec, data))

    def test_compacted_tester_and_checkpoint_proofs_restore_full_rows_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.chdir(folder):
            fixture.seed_greeting_workspace(folder)
            Path("main.py").write_text(blackbox.HELLO_SOURCE)
            Path("test_main.py").write_text(blackbox.HELLO_TESTS)
            greet_sha = hashlib.sha256(fixture.GREETING_SOURCE.encode()).hexdigest()
            hello_sha = hashlib.sha256(blackbox.HELLO_SOURCE.encode()).hexdigest()
            spec = {"payloads": {"M1": {"main.py": blackbox.HELLO_SOURCE, "test_main.py": blackbox.HELLO_TESTS}}}
            base = {
                "stage": "tester",
                "regression_proof": {
                    "verdict": "FAIL",
                    "path": "proof.json",
                    "mutation_check": {
                        "collected": True,
                        "files": [
                            {
                                "path": "greet.py",
                                "sha256": greet_sha,
                                "mutants": [{"line": 99, "end_line": 99, "outcome": "killed", "pad": "x" * 14000}],
                            }
                        ],
                    },
                    "changed_code_evidence": {
                        "obligations": [
                            {
                                "gap_id": "hello:1",
                                "handle": "L1",
                                "path": "main.py",
                                "line": 1,
                                "source_line": "print('hello')",
                                "sha256": hello_sha,
                                "status": "reason_required",
                                "mutation_outcomes": [],
                                "evidence": ["runner.log"] * 1200,
                            },
                            {
                                "gap_id": "greet:1",
                                "handle": "L2",
                                "path": "greet.py",
                                "line": 1,
                                "source_line": "import sys",
                                "sha256": greet_sha,
                                "status": "reason_required",
                                "mutation_outcomes": [],
                                "evidence": ["runner.log"] * 1200,
                            },
                        ]
                    },
                },
            }
            for stage in ("tester", "review_checkpoint"):
                state_path = Path(folder) / stage / "state.json"
                state_path.parent.mkdir(parents=True, exist_ok=True)
                packet, _, _ = context.compact_with_metrics(
                    {**copy.deepcopy(base), "stage": stage}, state_path, stage=stage
                )
                before = copy.deepcopy(packet)
                quoted_blackbox = blackbox.quote_changed_lines(
                    packet, blackbox.scripted_changed_code_reasons(spec, packet)
                )
                self.assertEqual(
                    [("hello:1", "print('hello')")], [(r["gap_id"], r["source_line"]) for r in quoted_blackbox]
                )
                quoted_goal = fixture.quote_changed_lines(packet, fixture.greeting_changed_code_reasons(packet))
                self.assertEqual([("greet:1", "import sys")], [(r["gap_id"], r["source_line"]) for r in quoted_goal])
                self.assertEqual(
                    ["greet:1"],
                    [
                        r["gap_id"]
                        for r in fixture.scripted_files_changed_code_reasons(
                            packet, {"greet.py": fixture.GREETING_SOURCE}
                        )
                    ],
                )
                self.assertEqual(
                    ["greet:1"],
                    [
                        r["gap_id"]
                        for r in fixture.scripted_files_changed_code_rejections(
                            packet, {"greet.py": fixture.GREETING_SOURCE}
                        )
                    ],
                )
                self.assertEqual(before, packet)

            for mod in (blackbox, fixture):
                self.assertIsNone(mod._review_data([]))
                self.assertIsNone(mod._review_data({"context_artifact": []}))
                self.assertIsNone(mod._review_data({"context_artifact": {"fields": []}}))
                self.assertIsNone(
                    mod._review_data({"regression_proof": {"details_artifact": {"field": "regression_proof"}}})
                )
                self.assertIsNone(
                    mod._review_data({"regression_proof": {"mutation_check": {"files": [{"mutants": 1}]}}})
                )
                self.assertIsNone(
                    mod._review_data({"regression_proof": {"changed_code_evidence": {"obligations": [{"gaps": []}]}}})
                )
                art_path = Path(folder) / "bad.json"
                art_path.write_bytes(b"[]")
                self.assertIsNone(
                    mod._review_data(
                        {
                            "context_artifact": {
                                "path": str(art_path),
                                "sha256": hashlib.sha256(b"[]").hexdigest(),
                                "fields": {"regression_proof": {"total": 1}},
                            }
                        }
                    )
                )
                raw = json.dumps({"regression_proof": {"path": "p"}}).encode()
                art_path.write_bytes(raw)
                digest = hashlib.sha256(raw).hexdigest()
                self.assertIsNone(
                    mod._review_data(
                        {
                            "context_artifact": {
                                "path": str(art_path),
                                "sha256": "0" * 64,
                                "fields": {"regression_proof": {"total": 1}},
                            }
                        }
                    )
                )
                self.assertIsNone(
                    mod._review_data(
                        {
                            "context_artifact": {
                                "path": str(art_path),
                                "sha256": digest,
                                "fields": {"regression_proof": {"total": 2}},
                            }
                        }
                    )
                )
                raw_rows = json.dumps({"regression_proof_obligations": [{"gap_id": "g"}]}).encode()
                art_path.write_bytes(raw_rows)
                digest_rows = hashlib.sha256(raw_rows).hexdigest()
                self.assertIsNone(
                    mod._review_data(
                        {
                            "regression_proof": ["bad"],
                            "context_artifact": {
                                "path": str(art_path),
                                "sha256": digest_rows,
                                "fields": {"regression_proof_obligations": {"total": 1}},
                            },
                        }
                    )
                )
                self.assertIsNone(
                    mod._review_data(
                        {
                            "regression_proof": {"path": "p"},
                            "context_artifact": {
                                "path": str(art_path),
                                "sha256": digest_rows,
                                "fields": {"regression_proof_obligations": {"total": 2}},
                            },
                        }
                    )
                )
                restored = mod._review_data(
                    {
                        "regression_proof": {"path": "p"},
                        "context_artifact": {
                            "path": str(art_path),
                            "sha256": digest_rows,
                            "fields": {"regression_proof_obligations": {"total": 1}},
                        },
                    }
                )
                self.assertEqual(
                    [{"gap_id": "g"}], restored["regression_proof"]["changed_code_evidence"]["obligations"]
                )
                self.assertIsNone(
                    mod._review_data(
                        {
                            "context_artifact": {
                                "path": str(art_path) + ".missing",
                                "sha256": digest_rows,
                                "fields": {"regression_proof_obligations": {"total": 1}},
                            }
                        }
                    )
                )
            self.assertEqual([], fixture.scripted_files_changed_code_reasons([], {"greet.py": fixture.GREETING_SOURCE}))
            self.assertEqual(
                [], fixture.scripted_files_changed_code_rejections([], {"greet.py": fixture.GREETING_SOURCE})
            )
            self.assertEqual([], blackbox.scripted_changed_code_reasons(spec, []))


if __name__ == "__main__":
    unittest.main()
