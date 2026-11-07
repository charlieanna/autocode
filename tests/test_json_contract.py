"""The commands a script drives a run with, under --json at each stop that waits on you (#714).

The scripted provider (tools/fake_codex.py) takes a run through the stops that wait on a person
(pause_category your_decision): a pause raised in the build loop (--pause-after-stage), a question,
the plan approval, a human review, then completion. It never stops for its environment, a provider
or AutoCode; test_json_output covers those: the iteration and time limits (AutoCode), and a saved
auth or rate-limit pause (your_environment / model_or_provider).

At each stop the test runs `--status`, a plain relaunch, the action the status view offers there
(resume, answer, approve the plan, approve the review; as displayed, tokens from its environment
assignments), that action with stale tokens, `wait`, `runs` and `events`, and a usage error; at the
first, also a refusal before the run loads and settings a saved run cannot take (the saved payload
with the refusal as `result`, then `autocode: ...` on stderr). Each status payload on stdout must
validate against docs/schemas/status-view.schema.json, and the lone `{"result": ...}` printed before
the run loads against its `properties.result`; each `events` line must validate against
run-event.schema.json, and `wait --json` and `runs --json`, which print their own shapes, must agree
with the status view. Every exit code must be the one docs/cli.md's exit-code table gives that
outcome.
"""

import json
import re
import shlex
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_util as util

from . import test_subprocess

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "docs" / "schemas" / "status-view.schema.json").read_text())
EVENT_SCHEMA = json.loads((ROOT / "docs" / "schemas" / "run-event.schema.json").read_text())
# The stops the fixture reaches in order; "resume" is the --pause-after-stage pause.
PAUSES = ["resume", "answer", "approve_plan", "review"]


def exit_codes() -> dict[str, int]:
    """docs/cli.md's exit-code table: each outcome and its code."""
    section = (ROOT / "docs" / "cli.md").read_text().split("\n## Exit codes\n", 1)[1].split("\n## ", 1)[0]
    return {outcome: int(code) for code, outcome in re.findall(r"^\| `(\d+)` \| `(\w+)` \|", section, re.M)}


class JsonContractTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp

    def autocode(self, *argv, env=None):
        return subprocess.run(
            [*self.entry, *argv],
            env={**self.env, **(env or {})},
            cwd=self.project,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def exits(self, outcome, proc):
        """The documented code for ``outcome``; returns the one object stdout carries, validated."""
        self.assertEqual(
            self.codes[outcome], proc.returncode, f"{outcome}: {proc.args[len(self.entry) :]}\n{proc.stderr[-3000:]}"
        )
        self.seen.add(outcome)
        if outcome == "usage":
            self.assertEqual("", proc.stdout)
            self.assertTrue(proc.stderr.startswith("usage: "), proc.stderr[-500:])
            return None
        payload = json.loads(proc.stdout)  # exactly one object: anything more would not parse
        if set(payload) == {"result"}:
            util.validate_schema(payload["result"], SCHEMA["properties"]["result"], "$.result")
        else:
            util.validate_schema(payload, SCHEMA)
        return payload

    def run_payload(self, outcome, proc):
        """A main-command invocation on the loaded run: the full status payload, never a lone result."""
        payload = self.exits(outcome, proc)
        self.assertIn("view", payload, f"{outcome} printed no status payload: {payload}")
        self.assertEqual(str(self.run_dir), payload["run_dir"])
        if "result" in payload:
            self.assertEqual(proc.returncode, payload["result"]["exit_code"])
        return payload

    def test_every_command_at_every_pause_validates_and_exits_by_the_documented_table(self):
        self.codes, self.seen = exit_codes(), set()
        self.assertEqual({"completed", "saved", "read", "stopped", "refused", "usage"}, set(self.codes))

        # Stopped by a pause raised inside the build loop, right after the first stage.
        brief = ("Build greeting and goodbye", "--engine", "codex", "--in-place", "--pause-after-stage", "--json")
        start = self.autocode(*brief, "--workspace", str(self.project))
        (self.run_dir,) = self.project.glob(".autocode/runs/*")
        self.assertEqual("PAUSED_REQUESTED", self.run_payload("stopped", start)["status"])
        on_run = ("--run-dir", str(self.run_dir), "--workspace", str(self.project))

        # Refused before the run loads: the result alone.
        refused = self.exits("refused", self.autocode(*on_run, "--feedback", " ", "--json"))
        self.assertEqual({"result"}, set(refused))
        self.assertEqual("input_rejected", refused["result"]["error"]["code"])
        # A setting the saved run cannot take: the saved payload with the refusal, then `autocode: ...` as before.
        for setting in (("--single-model", "gpt-5.6-sol"), ("--spec-first",)):
            refused = self.autocode(*on_run, *setting, "--json")
            error = self.run_payload("refused", refused)["result"]["error"]
            self.assertEqual("input_rejected", error["code"])
            self.assertTrue(error["message"].startswith(f"{setting[0]} "), error["message"])
            self.assertIn(f"autocode: {setting[0]} ", refused.stderr)

        reached = []
        for _ in range(12):
            status = self.run_payload("read", self.autocode(*on_run, "--status"))
            self.assertNotIn("result", status)
            view = status["view"]
            need = view["needs"] or {}
            if need.get("kind") == "continue":
                # Relaunching works until the run next stops, or completes.
                advanced = self.autocode(*on_run, "--no-chat", "--json")
                done = json.loads(advanced.stdout)["view"]["done"]
                self.run_payload("completed" if done else "stopped", advanced)
                continue
            self.read_commands(view, on_run)
            if view["done"]:
                done = self.run_payload("completed", self.autocode(*on_run, "--no-chat", "--json"))
                self.assertTrue(done["view"]["done"])
                break
            reached.append(need["kind"])
            self.assertEqual("your_decision", view["pause_category"])
            # A plain relaunch leaves a waiting run where it is.
            held = self.run_payload("stopped", self.autocode(*on_run, "--no-chat", "--json"))
            self.assertEqual((view["status"], view["needs"]), (held["view"]["status"], held["view"]["needs"]))
            self.assertEqual((False, None), (held["result"]["saved"], held["result"]["error"]))
            self.offered(view, need)
            self.exits("usage", self.autocode(*on_run, "--json", "--no-such-option"))
        else:
            self.fail(f"The fixture run did not complete; it reached {reached}")
        self.assertEqual(PAUSES, reached)
        self.assertEqual(set(self.codes), self.seen, "every documented outcome was exercised")

    def read_commands(self, view, on_run):
        """`wait`, `runs` and `events` agree with the status view and exit by the table."""
        waited = self.autocode("wait", *on_run, "--json", "--timeout", "30")
        self.assertEqual(self.codes["completed" if view["done"] else "stopped"], waited.returncode, waited.stderr)
        outcome = json.loads(waited.stdout)
        self.assertEqual(view["status"], outcome["status"])
        if not view["done"]:
            self.assertEqual(("needs_you", view["next_command"]), (outcome["outcome"], outcome["next_command"]))
        listed = self.autocode("runs", "--workspace", str(self.project), "--json")
        self.assertEqual(self.codes["read"], listed.returncode, listed.stderr)
        (row,) = json.loads(listed.stdout)["runs"]
        need = view["needs"] or {}
        self.assertEqual(
            (view["status"], need.get("kind"), view["pause_category"], view["next_command"]),
            (row["status"], row["needs"], row["pause_category"], row["next_command"]),
        )
        events = self.autocode("events", *on_run)
        self.assertEqual(self.codes["read"], events.returncode, events.stderr)
        for line in events.stdout.splitlines():
            util.validate_schema(json.loads(line), EVENT_SCHEMA, "$.event")

    def offered(self, view, need):
        """The view's own next_command with --json: refused with stale tokens, then saved or resumed."""
        words = shlex.split(view["next_command"])
        tokens = {}
        while words[0] != "autocode":
            name, value = words.pop(0).split("=", 1)
            tokens[name] = value
        command = [*words[1:], "--json"]
        if need["kind"] == "answer":  # the one placeholder these stops show: a person's answer
            command = [word.replace("=ANSWER", "=" + need["questions"][0]["options"][0]) for word in command]
        if tokens:
            stale = self.run_payload("refused", self.autocode(*command, env=dict.fromkeys(tokens, "r9:stale")))
            self.assertEqual("input_rejected", stale["result"]["error"]["code"])
            self.assertFalse(stale["result"]["saved"])
            self.assertEqual((view["status"], view["needs"]), (stale["view"]["status"], stale["view"]["needs"]))
        acted = self.autocode(*command, env=tokens)
        if need["kind"] == "resume":
            # Resuming continues the run until it next stops; the run prints its own payload.
            self.assertFalse(self.run_payload("stopped", acted)["view"]["done"])
            return
        saved = self.run_payload("saved", acted)
        self.assertEqual((True, None), (saved["result"]["saved"], saved["result"]["error"]))
        self.assertEqual("continue", saved["view"]["needs"]["kind"])


class SchemaCoverageTests(unittest.TestCase):
    def test_execution_identity_schema_covers_known_unknown_and_invalid_types(self):
        import autocode_route_evidence as evidence
        import autocode_run_view as run_view

        schema = SCHEMA["properties"]["view"]["properties"]["usage"]
        facts = evidence.launch_identity(
            role="terra",
            engine="codex",
            provider="codex",
            model="gpt-6-sol",
            effort="medium",
            runtime_commit="a" * 40,
            settings={},
        )
        stage = {
            "stage": "terra",
            "role": "terra",
            "engine": "codex",
            "model": "gpt-6-sol",
            "execution_identity": facts,
        }
        shown = run_view.view({"status": "RUNNING", "stages": [stage]})["usage"]
        self.assertEqual(facts, shown["accounting"]["attempts"][0]["execution_identity"])
        util.validate_schema(shown, schema)
        shown["accounting"]["attempts"][0]["execution_identity"] = {}
        util.validate_schema(shown, schema)
        for invalid in (None, [], "unknown"):
            with self.subTest(invalid=invalid):
                shown["accounting"]["attempts"][0]["execution_identity"] = invalid
                with self.assertRaises(ValueError):
                    util.validate_schema(shown, schema)
        del shown["accounting"]["attempts"][0]["execution_identity"]
        with self.assertRaisesRegex(ValueError, "missing execution_identity"):
            util.validate_schema(shown, schema)

    def test_coordination_and_inherited_routes_match_the_public_schema(self):
        import autocode_coordination_store as coordination
        import autocode_run_view as run_view

        document = {
            "phase": "fixing",
            "revision": 4,
            "active": "history",
            "members": [{"target": "history", "run_dir": "/runs/history"}],
            "member_statuses": {"history": "RUNNING"},
        }
        state = {
            "status": "RUNNING",
            "settings": {"engine": "codex", "roles": {"terra": {"engine": "codex", "model": "gpt-5.6-terra"}}},
        }
        with patch.object(coordination, "read", return_value=document):
            view = run_view.view(state, run_dir=Path("/runs/checkout"))
        self.assertEqual("history", view["coordination"]["active"])
        self.assertEqual(state["settings"], view["related_profile"]["settings"])
        for name in ("coordination", "related_profile", "scope"):
            util.validate_schema(view[name], SCHEMA["properties"]["view"]["properties"][name])

    def test_every_field_the_view_always_returns_is_required_by_the_schema(self):
        # #1905 found eight such fields missing from the schema; required, removing one fails the contract above.
        import autocode_run_view as run_view

        states = (
            {"status": "RUNNING", "version": 3, "settings": {}, "stages": []},
            {"status": "TASK_COMPLETE", "version": 3, "settings": {}, "stages": [], "run_dir": "/r", "workspace": "/w"},
            {"status": "PAUSED_REQUESTED", "version": 2, "settings": {}, "stages": []},
        )
        always = set.intersection(*(set(run_view.view(state)) for state in states))
        self.assertEqual(set(), always - set(SCHEMA["properties"]["view"]["required"]))


if __name__ == "__main__":
    unittest.main()
