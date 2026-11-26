"""The MCP server (autocode_mcp, #693): its protocol in process, and one fake run over its stdio command line."""

import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import autocode_agent_step as agent_step
import autocode_mcp as mcp
import autocode_subcommands as subcommands

from tests.test_agent_step import callers, fixture_workspace, hold_the_provider
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "tools" / "autocode_mcp.py"
TOOL_NAMES = ["autocode_start", "autocode_status", "autocode_respond", "autocode_evidence", "autocode_runs"]


def saved_run(root: Path, project: str, name: str, **state) -> tuple[Path, Path]:
    """A project under ``root`` holding one saved run, registered as a real run registers itself."""
    import autocode_registry as registry

    workspace = root / project
    run_dir = workspace / ".autocode" / "runs" / name
    run_dir.mkdir(parents=True)
    (workspace / ".git").mkdir(exist_ok=True)
    saved = {"version": 3, "workspace": str(workspace), "task": f"{name} in {project}", **state}
    (run_dir / "state.json").write_text(json.dumps(saved), encoding="utf-8")
    registry.register_run(workspace, run_dir, saved)
    return workspace, run_dir


class ProtocolTests(unittest.TestCase):
    """JSON-RPC handling with no run and no provider."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mcp-protocol-")
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        self.server = mcp.Server(agent_step.AgentStep(self.workspace, ("--engine", "codex"), caller="mcp"), "test")

    def exchange(self, *messages):
        out = io.StringIO()
        mcp.serve(self.server, [m if isinstance(m, str) else json.dumps(m) for m in messages], out)
        return [json.loads(line) for line in out.getvalue().splitlines()]

    def request(self, method, params=None, ident=1):
        return {"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}}

    def call(self, name, **arguments):
        (response,) = self.exchange(self.request("tools/call", {"name": name, "arguments": arguments}))
        return response["result"]

    def test_initialize_agrees_a_protocol_version_and_offers_tools(self):
        for asked, agreed in (("2024-11-05", "2024-11-05"), ("2025-06-18", "2025-06-18"), ("1999-01-01", "2025-06-18")):
            with self.subTest(asked=asked):
                (response,) = self.exchange(self.request("initialize", {"protocolVersion": asked, "capabilities": {}}))
                result = response["result"]
                self.assertEqual(agreed, result["protocolVersion"])
                self.assertEqual({"tools": {"listChanged": False}}, result["capabilities"])
                self.assertEqual({"name": "autocode", "version": "test"}, result["serverInfo"])
                self.assertIn("next_command", result["instructions"])
        self.assertEqual([{"jsonrpc": "2.0", "id": 2, "result": {}}], self.exchange(self.request("ping", ident=2)))

    def test_notifications_and_responses_get_no_reply(self):
        silent = (
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
            {"jsonrpc": "2.0", "id": 9, "result": {}},
            "",
        )
        self.assertEqual([], self.exchange(*silent))

    def test_a_malformed_request_is_a_json_rpc_error(self):
        responses = self.exchange(
            "{not json",
            {"id": 2, "method": "ping"},
            self.request("resources/list", ident=3),
            self.request("tools/call", {"name": "autocode_delete", "arguments": {}}, ident=4),
            self.request("tools/call", {"name": "autocode_status", "arguments": ["full"]}, ident=5),
        )
        self.assertEqual(
            [(None, -32700), (2, -32600), (3, -32601), (4, -32602), (5, -32602)],
            [(response["id"], response["error"]["code"]) for response in responses],
        )

    def test_an_unexpected_failure_answers_its_request_and_keeps_the_session(self):
        call = self.request("tools/call", {"name": "autocode_status", "arguments": {}})
        with mock.patch.object(agent_step.AgentStep, "status", side_effect=RuntimeError("boom")):
            failed, pinged = self.exchange(call, self.request("ping", ident=2))
        self.assertEqual({"code": -32603, "message": "RuntimeError: boom"}, failed["error"])
        self.assertEqual((2, {}), (pinged["id"], pinged["result"]))

    def test_the_tools_have_closed_schemas(self):
        (response,) = self.exchange(self.request("tools/list"))
        tools = {tool["name"]: tool for tool in response["result"]["tools"]}
        self.assertEqual(TOOL_NAMES, list(tools))
        for tool in tools.values():
            schema = tool["inputSchema"]
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool["description"])
                self.assertEqual(("object", False), (schema["type"], schema["additionalProperties"]))
                self.assertLessEqual(set(schema.get("required", ())), set(schema["properties"]))
                self.assertNotIn("options", schema["properties"])
        self.assertEqual(
            list(agent_step.WORKFLOWS), tools["autocode_start"]["inputSchema"]["properties"]["workflow"]["enum"]
        )
        self.assertEqual(
            list(agent_step.ACTIONS), tools["autocode_respond"]["inputSchema"]["properties"]["action"]["enum"]
        )
        self.assertEqual(list(agent_step.SHOWN), tools["autocode_runs"]["inputSchema"]["properties"]["show"]["enum"])

    def test_runs_lists_every_projects_runs_from_a_server_on_another_workspace(self):
        """An agent asked "what is running?" could read one run of its own workspace and no other."""
        home = self.workspace / "registry"
        with mock.patch.dict(os.environ, {"AUTOCODE_HOME": str(home)}):
            api, waiting = saved_run(self.workspace, "api", "waiting", status="PAUSED_REQUESTED", stop_reason="paused")
            web, finished = saved_run(self.workspace, "web", "finished", status="TASK_COMPLETE")
        elsewhere = self.workspace / "elsewhere"
        elsewhere.mkdir()
        step = agent_step.AgentStep(elsewhere, caller="mcp", env={"AUTOCODE_HOME": str(home)})
        self.server = mcp.Server(step, "test")

        active = self.call("autocode_runs")
        self.assertFalse(active["isError"], active)
        listed = active["structuredContent"]
        self.assertEqual(listed, json.loads(active["content"][0]["text"]))
        self.assertEqual("registry", listed["scope"])
        self.assertEqual((2, 1, 1), tuple(listed["counts"][key] for key in ("total", "needs you", "settled")))
        (row,) = listed["runs"]
        self.assertEqual((str(waiting), str(api), "needs you"), (row["run_dir"], row["workspace"], row["bucket"]))
        self.assertEqual("waiting in api", row["title"])
        self.assertIn("--resume-paused", row["next_command"])

        self.assertEqual(listed["runs"], self.call("autocode_runs", show="needs")["structuredContent"]["runs"])
        everything = self.call("autocode_runs", show="all")["structuredContent"]["runs"]
        self.assertEqual(
            [(str(waiting), str(api)), (str(finished), str(web))],
            [(item["run_dir"], item["workspace"]) for item in everything],
        )
        before = sorted(path.relative_to(self.workspace) for path in self.workspace.rglob("*"))
        self.call("autocode_runs", show="all")
        self.assertEqual(before, sorted(path.relative_to(self.workspace) for path in self.workspace.rglob("*")))

    def test_runs_refuses_a_filter_it_does_not_have_and_reports_a_failed_listing(self):
        refused = self.call("autocode_runs", show="mine")
        self.assertTrue(refused["isError"])
        self.assertIn("show must be one of active, needs, all", refused["content"][0]["text"])
        self.assertIn(
            "autocode_runs takes no workspace", self.call("autocode_runs", workspace="/")["content"][0]["text"]
        )
        failed = subprocess.CompletedProcess([], 2, stdout="", stderr="autocode: the registry could not be read\n")
        with mock.patch.object(agent_step.taskrun, "run_process", return_value=failed):
            broken = self.call("autocode_runs")
        self.assertTrue(broken["isError"])
        self.assertEqual(
            "could not list the runs: autocode: the registry could not be read", broken["content"][0]["text"]
        )
        with mock.patch.object(
            agent_step.taskrun, "run_process", side_effect=subprocess.TimeoutExpired("autocode", 60)
        ):
            self.assertIn("no answer in 60 seconds", self.call("autocode_runs")["content"][0]["text"])

    def test_the_example_configuration_launches_this_server_with_the_persons_flags(self):
        example = json.loads((ROOT / "examples" / "agent-step" / "mcp.json").read_text(encoding="utf-8"))
        server = example["mcpServers"]["autocode"]
        # `autocode mcp`, not `python3 -m autocode_cli.autocode_mcp`: after pipx or Homebrew the
        # system python3 cannot import AutoCode, and only `autocode` is on PATH.
        self.assertEqual(("autocode", ["mcp", "--workspace", "."]), (server["command"], server["args"]))
        self.assertEqual("autocode_mcp", subcommands.SUBCOMMANDS[server["args"][0]])
        self.assertIn(json.dumps(server["args"]), (ROOT / "docs" / "agent-step.md").read_text(encoding="utf-8"))

    def test_a_waiting_call_sends_progress_only_to_a_client_that_asked_for_it(self):
        def waits(step, *_, **__):
            step.progress("Planner working")
            step.progress("Plan Reviewer working")
            return {"run_dir": "r", "working": False}

        call = {"name": "autocode_status", "arguments": {"wait_seconds": 30}}
        asked = {**call, "_meta": {"progressToken": "p-1"}}
        with mock.patch.object(agent_step.AgentStep, "status", autospec=True, side_effect=waits):
            *notices, answered = self.exchange(self.request("tools/call", asked))
            self.assertEqual(
                [
                    {"progressToken": "p-1", "progress": 1, "message": "Planner working"},
                    {"progressToken": "p-1", "progress": 2, "message": "Plan Reviewer working"},
                ],
                [notice["params"] for notice in notices],
            )
            self.assertEqual({"notifications/progress"}, {notice["method"] for notice in notices})
            self.assertFalse({"id"} & {key for notice in notices for key in notice})
            self.assertEqual(1, answered["id"])
            self.assertIsNone(self.server.step.progress, "progress belongs to the one call that asked")
        for unasked in (call, {**call, "_meta": {"progressToken": True}}):
            with self.subTest(meta=unasked.get("_meta")):

                def unreported(step, *_, **__):
                    self.assertIsNone(step.progress, "no progress for a client that did not ask")
                    return {"working": False}

                with mock.patch.object(agent_step.AgentStep, "status", autospec=True, side_effect=unreported) as status:
                    (only,) = self.exchange(self.request("tools/call", unasked))
                self.assertIn("result", only)
                status.assert_called_once()

    def test_a_refusal_is_a_tool_result_the_agent_reads(self):
        refusals = {
            "no AutoCode run yet": self.call("autocode_status"),
            "brief must be a nonempty string": self.call("autocode_start", brief="  "),
            "takes no options": self.call("autocode_start", brief="Build it", options=["--engine", "opencode"]),
            "action must be one of": self.call("autocode_respond", action="approve_everything"),
            "full must be true or false": self.call("autocode_status", full="yes"),
            "wait_seconds must be a number": self.call("autocode_status", wait_seconds=-1),
        }
        for reason, result in refusals.items():
            with self.subTest(reason=reason):
                self.assertTrue(result["isError"], result)
                self.assertIn(reason, result["content"][0]["text"])
        self.assertFalse((self.workspace / ".autocode").exists(), "no refused call starts a run")


class StdioServerTests(unittest.TestCase):
    """One fake run through `python tools/autocode_mcp.py` over stdio, with the offline fixture provider."""

    def setUp(self):
        self.workspace, env = fixture_workspace(self)
        self.hold = hold_the_provider(self, env)
        self.server = subprocess.Popen(
            [sys.executable, str(SERVER), "--workspace", str(self.workspace), "--", *FIXTURE_OPTIONS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **env},
        )
        self.addCleanup(self.stop)
        watchdog = threading.Timer(600, self.server.kill)
        watchdog.start()
        self.addCleanup(watchdog.cancel)
        self.ids = itertools.count(1)

    def stop(self):
        if self.server.poll() is None:
            self.server.stdin.close()  # the end of input ends the server
            try:
                self.server.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait()
        self.server.stdout.close()
        self.server.stderr.close()

    def send(self, message):
        self.server.stdin.write(json.dumps(message) + "\n")
        self.server.stdin.flush()

    def request(self, method, params):
        ident = next(self.ids)
        self.send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        line = self.server.stdout.readline()
        if not line:
            self.fail(f"the server stopped: {self.server.wait()} {self.server.stderr.read()}")
        response = json.loads(line)
        self.assertEqual(ident, response["id"])
        return response["result"]

    def call(self, name, **arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def notified_call(self, name, token, **arguments):
        """A call that asked for progress: the notifications sent before its response, and the response."""
        ident = next(self.ids)
        params = {"name": name, "arguments": arguments, "_meta": {"progressToken": token}}
        self.send({"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": params})
        notices = []
        while True:
            line = self.server.stdout.readline()
            if not line:
                self.fail(f"the server stopped: {self.server.wait()} {self.server.stderr.read()}")
            message = json.loads(line)
            if message.get("id") == ident:
                return notices, message["result"]
            notices.append(message)

    def view(self, name, **arguments):
        result = self.call(name, **arguments)
        self.assertFalse(result["isError"], result["content"][0]["text"])
        self.assertEqual(result["structuredContent"], json.loads(result["content"][0]["text"]))
        return result["structuredContent"]

    def refusal(self, name, **arguments):
        result = self.call(name, **arguments)
        self.assertTrue(result["isError"], result)
        return result["content"][0]["text"]

    def test_an_agent_drives_one_fake_run_from_its_request_to_its_evidence(self):
        client = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}
        self.assertEqual("2025-06-18", self.request("initialize", client)["protocolVersion"])
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(TOOL_NAMES, [tool["name"] for tool in self.request("tools/list", {})["tools"]])

        # The start returns while the run works (#3984): the held provider keeps it working.
        started = self.view("autocode_start", brief=BRIEF)
        self.assertTrue(started["working"], started)
        self.assertTrue(self.view("autocode_status", wait_seconds=1)["working"])
        # A waiting call tells a client that asked where the run stands while it waits.
        notices, waited = self.notified_call("autocode_status", 7, wait_seconds=7)
        self.assertTrue(waited["structuredContent"]["working"])
        self.assertGreaterEqual(len(notices), 1, notices)
        self.assertEqual({"notifications/progress"}, {notice["method"] for notice in notices})
        self.assertEqual(7, notices[0]["params"]["progressToken"])
        self.assertTrue(all(notice["params"]["message"] for notice in notices), notices)
        self.assertIn("still working", self.refusal("autocode_respond", action="continue"))
        self.hold.unlink()
        planned = self.view("autocode_status", wait_seconds=250)
        self.assertEqual(started["run_dir"], planned["run_dir"])
        self.assertEqual((False, "approve_plan"), (planned["working"], planned["needs"]["kind"]), planned)
        self.assertEqual(
            ("your_decision", "Your decision"), (planned["pause_category"], planned["pause_category_label"])
        )
        self.assertIn("--approve-goal", planned["next_command"])
        self.assertIn("Approval token: " + planned["needs"]["token"], planned["plan"])
        self.assertEqual(planned["needs"], self.view("autocode_status")["needs"])

        self.assertIn("approve plan exited", self.refusal("autocode_respond", action="approve_plan", token="stale"))
        self.assertIn("person's to decide", self.refusal("autocode_respond", action="continue"))
        self.assertIn("not current", self.refusal("autocode_evidence"))
        self.assertFalse((self.workspace / "greet.py").exists(), "nothing is built before the person approves")

        done = self.view("autocode_respond", action="approve_plan", token=planned["needs"]["token"], wait_seconds=250)
        self.assertEqual(("TASK_COMPLETE", True, None), (done["status"], done["done"], done["next_command"]), done)
        self.assertEqual(planned["run_dir"], done["run_dir"])
        self.assertTrue((self.workspace / "greet.py").is_file())
        report = self.view("autocode_evidence")
        self.assertEqual("current", report["availability"])
        self.assertTrue(report["markdown"].startswith("## AutoCode evidence"))
        self.assertTrue(Path(report["markdown_path"]).is_file())
        self.assertEqual({"mcp"}, callers(done["run_dir"]))


if __name__ == "__main__":
    unittest.main()
