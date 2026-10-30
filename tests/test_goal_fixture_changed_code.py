"""Default offline provider review reasons require its exact behavioral tests."""

import contextlib
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

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
            self.assertEqual(1, len(blackbox.hello_changed_code_reasons(data)))
            data["regression_proof"]["changed_code_evidence"]["obligations"][0]["mutation_outcomes"] = ["not_run"]
            self.assertEqual([], blackbox.hello_changed_code_reasons(data))
            data["regression_proof"]["changed_code_evidence"]["obligations"][0]["mutation_outcomes"] = []
            Path("test_main.py").write_text("# assertions absent\n")
            self.assertEqual([], blackbox.hello_changed_code_reasons(data))


if __name__ == "__main__":
    unittest.main()
