"""Pure project-memory boundaries: no provider, controller or process launches."""

import copy
import fcntl
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_project_memory as memory


def record(**changes):
    return {
        "version": 1,
        "framework": "unittest",
        "test_command": "python3 -m unittest discover -v",
        "conventions": [
            {"question": "Which logging convention should this project keep?", "answer": "Use the existing logger."}
        ],
        "routes": [{"role": "terra", "engine": "opencode", "provider": "kilocode", "model": "zai-coding-plan/glm-5.3"}],
        "human_pause_states": ["WAITING_FOR_USER"],
        **changes,
    }


class ProjectMemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / ".autocode"
        self.path = self.directory / memory.FILENAME

    def raw(self, value):
        self.directory.mkdir(exist_ok=True)
        self.path.write_text(value if isinstance(value, str) else json.dumps(value))

    def test_absent_read_and_forget_create_no_directories(self):
        self.assertIsNone(memory.read(self.root))
        self.assertEqual("absent", memory.load(self.root)["status"])
        self.assertFalse(memory.forget(self.root))
        self.assertEqual([], list(self.root.iterdir()))

    def test_loading_a_team_committed_record_is_read_only_without_a_write_lock(self):
        expected = record()
        self.raw(expected)
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertFalse((self.directory / memory.LOCKFILE).exists())
        snapshot = memory.load(self.root)
        self.assertEqual("loaded", snapshot["status"])
        self.assertEqual(expected["conventions"], memory.context(snapshot)["conventions"])
        self.assertEqual(expected, memory.read(self.root))
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.directory / memory.LOCKFILE).exists())

    def test_round_trip_sha_defaults_and_context_are_separate_advisory_data(self):
        expected = record()
        self.assertEqual(expected, memory.write(self.root, expected))
        snapshot = memory.load(self.root)
        self.assertEqual("loaded", snapshot["status"])
        self.assertEqual(expected, snapshot["record"])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), snapshot["sha256"])
        self.assertEqual({"test-command": expected["test_command"]}, memory.defaults(snapshot))
        self.assertEqual(
            {"framework": "unittest", "test_command": expected["test_command"], "conventions": expected["conventions"]},
            memory.context(snapshot),
        )
        self.assertNotIn("routes", memory.context(snapshot))
        self.assertNotIn("human_pause_states", memory.context(snapshot))
        snapshot["record"]["conventions"][0]["answer"] = "A detached snapshot"
        self.assertEqual(expected, memory.read(self.root))

    def test_strict_unknown_fields_versions_and_types(self):
        bad = [
            record(extra="not a preference"),
            record(version=2),
            record(version=True),
            record(version="1"),
            record(conventions={}),
            record(test_command=[]),
            record(framework=""),
            record(routes=[{}]),
            record(conventions=[{"question": "Q", "answer": "A", "approval": True}]),
            record(human_pause_states=["TASK_COMPLETE"]),
            record(human_pause_states=["PAUSED_TEST", "PAUSED_TEST"]),
            record(routes=[{"role": "Bad Role", "engine": None, "provider": None, "model": None}]),
        ]
        missing = record()
        missing.pop("framework")
        bad.append(missing)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                memory.sanitize_record(value)
        self.assertFalse(self.directory.exists())

    def test_duplicate_keys_and_identity_rows_are_rejected(self):
        malformed = json.dumps(record()).replace('"version": 1', '"version": 1, "version": 1')
        for value in (
            malformed,
            record(conventions=[{"question": "Q", "answer": "A"}, {"question": "Q", "answer": "B"}]),
            record(routes=record()["routes"] * 2),
        ):
            with self.subTest(value=value):
                self.raw(value)
                with self.assertRaises(ValueError):
                    memory.read(self.root)
                self.assertEqual("invalid", memory.load(self.root)["status"])

    def test_invalid_existing_file_is_preserved_and_never_echoed(self):
        bad = '{"version":99,"password":"synthetic-secret-value"}'
        self.raw(bad)
        before = self.path.read_bytes()
        with self.assertRaises(ValueError) as error:
            memory.write(self.root, record())
        self.assertNotIn("synthetic-secret-value", str(error.exception))
        self.assertEqual(before, self.path.read_bytes())
        snapshot = memory.load(self.root)
        self.assertEqual(
            {
                "status": "invalid",
                "record": None,
                "sha256": None,
                "note": "Project memory could not be safely loaded; it was ignored.",
            },
            snapshot,
        )
        self.assertNotIn("synthetic-secret-value", json.dumps(snapshot))
        self.assertEqual({}, memory.defaults(snapshot))
        self.assertEqual({}, memory.context(snapshot))

    def test_bounded_lengths_lists_and_physical_file_size(self):
        bad = [
            record(framework="x" * 81),
            record(test_command="x" * 2049),
            record(conventions=[{"question": "Q", "answer": "x" * 2049}]),
            record(conventions=[{"question": str(i), "answer": "A"} for i in range(33)]),
            record(routes=[{"role": "r" + str(i), "engine": None, "provider": None, "model": None} for i in range(33)]),
            record(human_pause_states=["PAUSED_" + str(i) for i in range(33)]),
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                memory.sanitize_record(value)
        self.raw(" " * (memory.MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            memory.read(self.root)
        self.assertEqual("invalid", memory.load(self.root)["status"])

    def test_malformed_utf8_constants_and_control_characters_are_invalid(self):
        self.directory.mkdir()
        for data in (b"\xff", b"{", b'{"version":NaN}', json.dumps(record(framework="a\x00b")).encode()):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                with self.assertRaises(ValueError):
                    memory.read(self.root)

    def test_shared_credential_policy_drops_whole_values_not_false_quotations(self):
        unsafe = [
            "ghp_" + "x" * 30,
            "sk-" + "x" * 30,
            "Bearer synthetic-token",
            "API_KEY=x",
            "AUTOCODE_SECRET=x",
            "PASSWORD=x",
            "password is hunter2",
            "Authorization: Basic abc",
            "postgres://user:password@example.test/db",
            "-----BEGIN PRIVATE KEY-----",
            "Read /Users/someone/private/config",
            "Use /home/alice/project",
            "Use `/Users/alice/private/project`",
            "Use [/home/alice/private]",
            r"Use \\server\share\private",
            "Use ~/private",
            "Use C:\\Users\\alice\\secret",
            "Read .autocode/runs/private/state.json",
            "Read `.autocode/runs/private/state.json`",
            "Read .ssh/id_rsa",
        ]
        for value in unsafe:
            with self.subTest(value=value):
                current = record(
                    framework=value[:80],
                    test_command="echo " + value,
                    conventions=[{"question": "Project convention?", "answer": value}],
                )
                actual = memory.sanitize_record(current)
                self.assertIsNone(actual["framework"])
                self.assertIsNone(actual["test_command"])
                self.assertEqual([], actual["conventions"])
                self.assertNotIn("REDACTED", json.dumps(actual))
                self.assertNotIn(value, json.dumps(actual))

    def test_known_opaque_environment_secrets_are_omitted_whole_without_logging_or_persistence(self):
        opaque = "syntheticopaquecredentialvalue"
        safe = {"question": "Logging convention?", "answer": "Keep the existing logger."}
        current = record(
            test_command="python3 -m unittest --pattern " + opaque,
            conventions=[
                safe,
                {"question": "A project convention?", "answer": opaque},
                {"question": "Use " + opaque + "?", "answer": "Do so."},
            ],
        )
        with patch.dict(os.environ, {"MEMORY_TEST_PASSWORD": opaque}, clear=True):
            actual = memory.sanitize_record(current)
            self.assertIsNone(actual["test_command"])
            self.assertEqual([safe], actual["conventions"])
            self.assertEqual(actual, memory.write(self.root, current))
            self.assertNotIn(opaque, self.path.read_text())
            self.assertNotIn("REDACTED", self.path.read_text())
            self.assertEqual(actual, memory.read(self.root))

    def test_short_known_environment_secrets_are_omitted_at_the_store_boundary(self):
        current = record(
            test_command="python3 -m unittest short7",
            conventions=[{"question": "Project convention?", "answer": "short7"}],
            routes=[{"role": "terra", "engine": "opencode", "provider": "kilocode", "model": "vendor/short7"}],
        )
        with patch.dict(os.environ, {"MEMORY_TEST_PASSWORD": "short7"}, clear=True):
            actual = memory.sanitize_record(current)
            self.assertIsNone(actual["test_command"])
            self.assertEqual([], actual["conventions"])
            self.assertEqual([], actual["routes"])
            memory.write(self.root, current)
            self.assertNotIn("short7", self.path.read_text())
            self.assertNotIn("REDACTED", self.path.read_text())

    def test_commands_with_credential_flags_variables_and_private_paths_are_omitted(self):
        commands = [
            "pytest --api-key x",
            "pytest --token=short",
            "env MY_PASSWORD=x pytest",
            "pytest --auth $TOKEN",
            "env TOKEN=$OTHER pytest",
            "/home/alice/.venv/bin/python -m pytest",
            "pytest --config /private/tmp/config",
            "pytest\necho another command",
        ]
        for command in commands:
            with self.subTest(command=command):
                actual = memory.sanitize_record(record(test_command=command))
                self.assertIsNone(actual["test_command"])
                self.assertEqual({}, memory.defaults({"status": "loaded", "record": actual}))

    def test_safe_exact_quotes_and_relative_commands_are_preserved(self):
        actual = record(
            test_command="env MAX_OUTPUT_TOKEN_COUNT=1000 python3 -m unittest -v tests/test_api.py",
            conventions=[
                {
                    "question": "Keep the public API unchanged?",
                    "answer": "Yes. Use src/logger.py; keep the public API unchanged.",
                }
            ],
        )
        before = copy.deepcopy(actual)
        self.assertEqual(before, memory.sanitize_record(actual))
        self.assertEqual(before, actual)

    def test_null_current_facts_do_not_erase_prior_known_facts(self):
        original = record()
        memory.write(self.root, original)
        current = record(
            framework=None,
            test_command=None,
            conventions=[],
            routes=[{"role": "terra", "engine": None, "provider": None, "model": None}],
            human_pause_states=[],
        )
        self.assertEqual(original, memory.write(self.root, current))

    def test_new_facts_merge_without_losing_unrelated_current_facts(self):
        memory.write(self.root, record(conventions=[{"question": "Old question?", "answer": "Old answer"}]))
        memory.write(
            self.root,
            record(
                framework=None,
                test_command=None,
                conventions=[{"question": "Another question?", "answer": "Another answer"}],
                routes=[{"role": "sol", "engine": "codex", "provider": None, "model": "gpt-6-sol"}],
                human_pause_states=["PAUSED_TEST"],
            ),
        )
        result = memory.write(
            self.root,
            record(
                framework=None,
                test_command=None,
                conventions=[{"question": "Old question?", "answer": "Current answer"}],
                routes=[{"role": "terra", "engine": None, "provider": None, "model": "new-model"}],
                human_pause_states=[],
            ),
        )
        self.assertEqual(
            [
                {"question": "Another question?", "answer": "Another answer"},
                {"question": "Old question?", "answer": "Current answer"},
            ],
            result["conventions"],
        )
        self.assertEqual(
            {"role": "terra", "engine": "opencode", "provider": "kilocode", "model": "new-model"}, result["routes"][-1]
        )
        self.assertEqual(["WAITING_FOR_USER", "PAUSED_TEST"], result["human_pause_states"])
        self.assertEqual("unittest", result["framework"])

    def test_merge_limits_keep_new_conventions_and_routes(self):
        old = record(
            conventions=[{"question": f"Old{i}?", "answer": "A"} for i in range(32)],
            routes=[{"role": f"old{i}", "engine": None, "provider": None, "model": "model"} for i in range(32)],
        )
        memory.write(self.root, old)
        updated = memory.write(
            self.root,
            record(
                conventions=[{"question": "New?", "answer": "B"}],
                routes=[{"role": "new", "engine": None, "provider": None, "model": "model"}],
            ),
        )
        self.assertEqual(32, len(updated["conventions"]))
        self.assertNotIn("Old0?", [row["question"] for row in updated["conventions"]])
        self.assertEqual({"question": "New?", "answer": "B"}, updated["conventions"][-1])
        self.assertEqual("new", updated["routes"][-1]["role"])

    def test_context_is_bounded_and_keeps_whole_exact_rows(self):
        value = record(conventions=[{"question": f"Question{i}?", "answer": "é" * 1500} for i in range(16)])
        snapshot = {"status": "loaded", "record": value}
        context = memory.context(snapshot)
        self.assertTrue(context["conventions"])
        self.assertLess(len(context["conventions"]), 16)
        self.assertLessEqual(len(memory._encoded(context)), memory.MAX_CONTEXT_BYTES)
        self.assertTrue(all(row in value["conventions"] for row in context["conventions"]))

    def test_symlink_storage_directory_is_refused_without_touching_target(self):
        target = self.root / "other"
        target.mkdir()
        (target / memory.FILENAME).write_text(json.dumps(record()))
        self.directory.symlink_to(target, target_is_directory=True)
        before = (target / memory.FILENAME).read_bytes()
        for operation in (memory.read, memory.forget):
            with self.assertRaises(ValueError):
                operation(self.root)
        with self.assertRaises(ValueError):
            memory.write(self.root, record())
        self.assertEqual(before, (target / memory.FILENAME).read_bytes())

    def test_symlink_leaf_is_refused_and_never_removed_or_overwritten(self):
        self.directory.mkdir()
        other = self.root / "other.json"
        other.write_text(json.dumps(record()))
        self.path.symlink_to(other)
        for operation in (memory.read, memory.forget):
            with self.assertRaises(ValueError):
                operation(self.root)
        with self.assertRaises(ValueError):
            memory.write(self.root, record())
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(record(), json.loads(other.read_text()))

    def test_fifo_and_hardlinked_memory_are_refused_without_blocking(self):
        self.directory.mkdir()
        os.mkfifo(self.path)
        for operation in (memory.read, memory.forget):
            with self.assertRaises(ValueError):
                operation(self.root)
        self.path.unlink()
        other = self.root / "other.json"
        other.write_text(json.dumps(record()))
        os.link(other, self.path)
        with self.assertRaises(ValueError):
            memory.write(self.root, record())
        self.assertEqual(2, other.stat().st_nlink)

    def test_hostile_lock_symlink_fifo_and_busy_lock_are_refused(self):
        self.raw(record())
        lock = self.directory / memory.LOCKFILE
        target = self.root / "lock-target"
        target.write_text("untouched")
        lock.symlink_to(target)
        with self.assertRaises(ValueError):
            memory.write(self.root, record())
        self.assertEqual("untouched", target.read_text())
        lock.unlink()
        os.mkfifo(lock)
        with self.assertRaises(ValueError):
            memory.write(self.root, record())
        lock.unlink()
        with lock.open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ValueError):
                memory.write(self.root, record())
            self.assertEqual("loaded", memory.load(self.root)["status"])
            self.assertEqual(record(), memory.read(self.root))

    def test_atomic_failure_preserves_existing_file_and_cleans_only_own_temporary(self):
        memory.write(self.root, record())
        before = self.path.read_bytes()
        with patch.object(memory.os, "replace", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(ValueError):
                memory.write(self.root, record(framework="pytest"))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], list(self.directory.glob(".project-memory-*")))

    def test_forget_preserves_config_runs_and_other_files_and_does_not_resurrect_snapshot(self):
        memory.write(self.root, record())
        snapshot = memory.load(self.root)
        (self.directory / "config.toml").write_text("[defaults]\n")
        run = self.directory / "runs" / "owned-fixture"
        run.mkdir(parents=True)
        (run / "state.json").write_text('{"untouched":true}')
        (self.root / "pyproject.toml").write_text("[tool.autocode]\n")
        self.assertTrue(memory.forget(self.root))
        self.assertFalse(memory.forget(self.root))
        self.assertEqual("[defaults]\n", (self.directory / "config.toml").read_text())
        self.assertEqual('{"untouched":true}', (run / "state.json").read_text())
        self.assertEqual("[tool.autocode]\n", (self.root / "pyproject.toml").read_text())
        self.assertEqual(record()["conventions"], snapshot["record"]["conventions"])
        current = record(
            framework=None,
            test_command=None,
            conventions=[{"question": "New run?", "answer": "Current only"}],
            routes=[],
            human_pause_states=[],
        )
        self.assertEqual(current, memory.write(self.root, current))
        self.assertNotIn(record()["conventions"][0], memory.read(self.root)["conventions"])

    def test_schema_contract_matches_required_fields_and_pure_limits(self):
        path = Path(__file__).resolve().parents[1] / "docs/schemas/project-memory-v1.schema.json"
        schema = json.loads(path.read_text())
        self.assertEqual(memory.FIELDS, frozenset(schema["required"]))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(memory.MAX_CONVENTIONS, schema["properties"]["conventions"]["maxItems"])
        self.assertEqual(memory.MAX_PAUSE_STATES, schema["properties"]["human_pause_states"]["maxItems"])
        self.assertEqual(memory.VERSION, schema["properties"]["version"]["const"])


if __name__ == "__main__":
    unittest.main()
