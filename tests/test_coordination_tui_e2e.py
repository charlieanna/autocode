"""A bug symptom to three verified, cross-linked PRs through the terminal chat.

The only human inputs are one-time source-root setup, the symptom and one exact
coordinated plan approval. Real Git repositories, the production supervised providers/proof
runner and a loopback GitHub server exercise every side effect offline.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import autocode_chat_client as chat
import autocode_chat_coordination as coordination
import autocode_taskrun as taskrun

from .test_taskrun import FIXTURE_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
BROKEN = {
    "checkout": (
        "client.py",
        "def display(payload):\n    return str(payload['amount']) + ' USD'\nHISTORY_REPOSITORY = 'history'\ndef checkout_total(history_client, subtotal):\n    return display(history_client.record(subtotal))\n",
    ),
    "history": (
        "history.py",
        "def serialize(total):\n    return {'amount': str(total)}\nTAX_REPOSITORY = 'tax'\nclass HistoryClient:\n    def __init__(self, tax_service):\n        self.tax_service = tax_service\n    def record(self, subtotal):\n        return serialize(self.tax_service.add_tax(subtotal))\n",
    ),
    "tax": ("tax.py", "def add_tax(value):\n    return value\n"),
}
PRESERVED = {
    "checkout": "from client import display\n        with self.assertRaises(KeyError):\n            display({})",
    "history": "from history import serialize\n        self.assertEqual(float(serialize(0)['amount']), 0)",
    "tax": "from tax import add_tax\n        self.assertEqual(add_tax(0), 0)",
}


class GitHubFixture:
    def __init__(self, bare):
        self.bare, self.pulls, self.posts = bare, {}, []
        self.drop_once = False
        self.dropped = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, value):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urlparse(self.path)
                repo = url.path.split("/")[3]
                if len(url.path.split("/")) == 4:
                    self.reply(200, {"full_name": "fixture/" + repo, "permissions": {"push": True}})
                    return
                query = parse_qs(url.query)
                rows = list(owner.pulls.get(repo, []))
                if query.get("head"):
                    rows = [row for row in rows if row["head"]["ref"] == query["head"][0].split(":", 1)[1]]
                self.reply(200, rows)

            def do_POST(self):
                repo = self.path.split("/")[3]
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                commit = subprocess.check_output(
                    ["git", "--git-dir", str(owner.bare[repo]), "rev-parse", "refs/heads/" + request["head"]], text=True
                ).strip()
                row = {
                    "number": len(owner.posts) + 1,
                    "html_url": f"https://github.com/fixture/{repo}/pull/{len(owner.posts) + 1}",
                    "head": {"ref": request["head"], "sha": commit},
                    "base": {
                        "ref": request["base"],
                        "sha": subprocess.check_output(
                            ["git", "--git-dir", str(owner.bare[repo]), "rev-parse", "refs/heads/" + request["base"]],
                            text=True,
                        ).strip(),
                    },
                    "body": request["body"],
                    "state": "open",
                }
                owner.pulls.setdefault(repo, []).append(row)
                owner.posts.append((repo, row))
                if owner.drop_once and not owner.dropped:
                    owner.dropped = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.reply(201, row)

            def do_PATCH(self):
                repo, number = self.path.split("/")[3], int(self.path.split("/")[-1])
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                row = next(row for row in owner.pulls[repo] if row["number"] == number)
                row.update(body=request["body"])
                self.reply(200, row)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class CoordinationTuiEndToEnd(unittest.TestCase):
    def tearDown(self):
        result = self._outcome.result
        if any(test is self for test, _ in [*result.errors, *result.failures]):
            output = ROOT / ".scenario-runs" / "coordination" / self.root.name
            shutil.copytree(self.root, output, symlinks=True)
            (output / "transcript.txt").write_text("\n".join(self.lines))
            print("Retained failed coordination fixture: " + str(output))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="coordination-tui-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repos, bare = {}, {}
        for name, (filename, contents) in BROKEN.items():
            workspace = self.root / "sources" / name
            workspace.mkdir(parents=True)
            (workspace / filename).write_text(contents)
            (workspace / "repository.json").write_text(json.dumps({"name": name}))
            (workspace / "tests").mkdir()
            (workspace / "tests" / ("test_preserved_" + name + ".py")).write_text(
                "import unittest\nimport sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n\nclass Preserved(unittest.TestCase):\n    def test_existing_boundary(self):\n        "
                + PRESERVED[name]
                + "\n"
            )
            (workspace / ".gitignore").write_text(".autocode/\n__pycache__/\n*.pyc\n")
            for arguments in (
                ["init", "-q", "-b", "main"],
                ["config", "user.name", "Fixture"],
                ["config", "user.email", "fixture@example.test"],
                ["config", "commit.gpgsign", "false"],
                ["add", "--all"],
                ["commit", "-qm", "Broken total contract"],
            ):
                subprocess.run(["git", "-C", str(workspace), *arguments], check=True, capture_output=True)
            bare[name] = self.root / (name + ".git")
            subprocess.run(["git", "clone", "--bare", str(workspace), str(bare[name])], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(workspace), "remote", "add", "origin", f"https://github.com/fixture/{name}.git"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(workspace), "remote", "set-url", "--push", "origin", str(bare[name])], check=True
            )
            self.repos[name] = workspace
        self.github = GitHubFixture(bare)
        self.addCleanup(self.github.close)
        binary = self.root / "bin"
        binary.mkdir()
        launcher = binary / "codex"
        launcher.write_text(
            "#!/bin/sh\nexec " + str(sys.executable) + " " + str(ROOT / "tests/coordination_provider.py") + ' "$@"\n'
        )
        launcher.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(
            # CI invokes the venv interpreter without activating it. The
            # fixture's real python3 checks need that interpreter's pytest.
            PATH=os.pathsep.join((str(binary), str(Path(sys.executable).parent), self.env["PATH"])),
            AUTOCODE_HOME=str(self.root / "registry"),
            CODEX_HOME=str(self.root / "codex-home"),
            XDG_CONFIG_HOME=str(self.root / "config"),
            PYTHONDONTWRITEBYTECODE="1",
            GITHUB_TOKEN="offline-fixture",
            AUTOCODE_GITHUB_API=f"http://127.0.0.1:{self.github.server.server_port}",
        )
        self.intake = self.root / "unrelated"
        self.intake.mkdir()
        (self.intake / "repository.json").write_text(json.dumps({"name": "intake"}))
        for arguments in (["init", "-q", "-b", "main"], ["add", "."],
                          ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                           "-c", "commit.gpgsign=false", "commit", "-qm", "Unrelated launch"]):
            subprocess.run(["git", "-C", str(self.intake), *arguments], check=True, capture_output=True)
        setup = chat.History(self.intake, env=self.env)
        setup.read(["registry", "roots", "add", str(self.root / "sources"), "--json"])
        self.lines, self.questions = [], []
        self.observed_mismatch = False
        self.reopened = False

    def answer(self, question):
        self.questions.append(question)
        self.assertIn(
            "Approve these coordinated plans?",
            question,
            "Only genuine plan approval should need user input\n" + "\n".join(self.lines[-20:]),
        )
        return "yes"

    def run_flow(self):
        history = chat.History(self.intake, env=self.env)
        with patch.dict(os.environ, self.env, clear=True):
            client = chat.start(
                self.intake,
                "Fix checkout showing the wrong final total.",
                ["--workflow", "bugfix"],
                [*FIXTURE_OPTIONS, "--no-adaptive-planning"],
                env=self.env,
                ask=self.answer,
                say=self.lines.append,
                poll_seconds=chat.POLL_SECONDS,
            )
            self.addCleanup(client.close)
            client.history = history
            self.flow_client = client
            selected_profile = client.status()["related_profile"]["settings"]
            for _ in range(100):
                view = client._status_view or {}
                if (
                    self.env.get("COORDINATION_FIXTURE_MISMATCH")
                    and (view.get("coordination") or {}).get("phase") == "joint_regression"
                ):
                    shown = coordination.read(client, view)
                    if shown["needs"]["kind"] == "rework":
                        self.assertEqual([], self.github.posts, "A mismatch cannot publish any member")
                        self.observed_mismatch = True
                try:
                    outcome = client.step()
                except chat.actions.ClientGap:
                    if not self.github.dropped or self.reopened:
                        raise
                    self.assertEqual(1, len(self.github.posts))
                    client.close()
                    client = chat.ChatClient(
                        client.workspace,
                        client.run_dir,
                        env=self.env,
                        ask=self.answer,
                        say=self.lines.append,
                        poll_seconds=chat.POLL_SECONDS,
                    )
                    client.history = history
                    self.addCleanup(client.close)
                    self.reopened = True
                    continue
                if outcome is not None:
                    break
            else:
                self.fail("TUI coordination failed to reach a terminal result")
            self.assertEqual(0, outcome, "\n".join(self.lines[-30:]))
            self.assertEqual(3, len(self.github.posts))
            self.assertEqual(1, len(self.questions), "All member and joint plans share one approval")
            summary = coordination.read(client, client.status())
            self.assertEqual("PASS", summary["verification"]["verdict"])
            for row in summary["members"]:
                response = client.cli(
                    ["--workspace", row["workspace"], "--run-dir", row["run_dir"], "--status", "--json"]
                )
                related = json.loads(response.stdout)["view"]["related_profile"]["settings"]
                member_plan = json.loads(response.stdout)["view"]["approved_contract"]["body"]
                self.assertTrue(all("local" in step for step in member_plan["end_to_end_flow"]))
                self.assertIn(
                    "The coordinator must separately prove the actual checkout, history and tax flow",
                    member_plan["required_behaviors"],
                )
                if self.env.get("COORDINATION_FIXTURE_SCOPE_METADATA") and row["target"] == "history":
                    view = json.loads(response.stdout)["view"]
                    parts = view["scope"]["external_parts"]
                    self.assertEqual(
                        {"tax", "tax repository", "checkout", "checkout repository"}, {part["target"] for part in parts}
                    )
                    exclusions = view["approved_contract"]["body"]["scope_exclusions"]
                    for part in parts:
                        self.assertIn(f"Not covered: changes in {part['target']}: {part['work']}", exclusions)
                self.assertEqual(selected_profile["roles"], related["roles"])
                for limit in ("max_seconds", "stage_timeout_seconds", "tool_timeout_seconds"):
                    self.assertEqual(selected_profile["limits"][limit], related["limits"][limit])
            if self.env.get("COORDINATION_FIXTURE_MEMBER_CONTEXT"):
                self.assertEqual("task_run", summary["verification"]["kind"])
            # A joint run whose change owes the changed-line proof (#2474) is verified by its own native
            # proof (#2339); only a command-only plan has an external verification record (#2495).
            if summary["verification"].get("kind") == "task_run":
                inspected = client.cli(
                    ["--run-dir", summary["integration"]["run_dir"], "--status", "--inspect-evidence", "--json"]
                )
                self.assertEqual("current", json.loads(inspected.stdout)["view"]["evidence_report"]["availability"])
            else:
                inspected = client.cli(["verify", "--record", summary["verification"]["record"], "--json"])
                self.assertEqual("PASS", json.loads(inspected.stdout)["verdict"])
            # Reopening the same TUI chat must reconcile delivery without a second publication.
            self.assertEqual(0, client.step())
            self.assertEqual(3, len(self.github.posts))
            # Editing even the generated integration test withdraws completion.
            check = Path(summary["integration"]["workspace"]) / "tests/test_joint.py"
            check.write_text(check.read_text() + "\n# input changed after verification\n")
            stale = coordination.read(client, client.status())
            self.assertFalse(stale["done"])
            self.assertEqual("blocked", stale["needs"]["kind"])
            self.assertEqual(3, len(self.github.posts))
        urls = {row["html_url"] for _, row in self.github.posts}
        for _, row in self.github.posts:
            for url in urls:
                self.assertIn(url, row["body"])
            self.assertIn("fails on the combined original code", row["body"])
            if self.env.get("COORDINATION_FIXTURE_SCOPE_METADATA"):
                # The alias fixture reports the consumer back to its producer,
                # so the scope graph cannot supply an acyclic merge order.
                self.assertIn("Dependency cycle: coordinate deployment", row["body"])
                self.assertIn("review compatibility before merging.", row["body"])
            else:
                self.assertIn("tax, then history, then checkout.", row["body"])
        for name, workspace in self.repos.items():
            self.assertEqual(
                BROKEN[name][1], (workspace / BROKEN[name][0]).read_text(), "Original checkout is unchanged"
            )

    def test_symptom_discovers_transitive_repositories_and_publishes_only_jointly_verified_fixes(self):
        self.env["COORDINATION_FIXTURE_MEMBER_CONTEXT"] = "1"
        self.env["COORDINATION_FIXTURE_SCOPE_METADATA"] = "1"
        self.run_flow()

    def test_scope_assignment_carries_clear_requirements_without_confirmation(self):
        self.env["COORDINATION_FIXTURE_EARLY_SCOPE"] = "1"
        self.env["COORDINATION_FIXTURE_CONSUMER_ALIAS"] = "coordinated"
        self.env["COORDINATION_FIXTURE_MEMBER_CONTEXT"] = "1"
        history = chat.History(self.intake, env=self.env)
        with patch.dict(os.environ, self.env, clear=True):
            client = chat.start(
                self.intake,
                "Fix checkout showing the wrong final total.",
                ["--workflow", "bugfix"],
                [*FIXTURE_OPTIONS, "--no-adaptive-planning", "--spec-first"],
                env=self.env,
                ask=self.answer,
                say=self.lines.append,
            )
            self.addCleanup(client.close)
            client.history = history
            for _ in range(30):
                current = client.status()
                if not current.get("coordination"):
                    client.step()
                    continue
                summary = coordination.read(client, current)
                checkout = next((row for row in summary["members"] if row["target"] == "checkout"), None)
                if checkout and checkout["status"] != "STARTING":
                    run = taskrun.TaskRun(Path(checkout["workspace"]), Path(checkout["run_dir"]), env=self.env)
                    view = run.status()
                    if view["scope"]["decision"] == "linked_repositories":
                        break
                client.step()
            else:
                self.fail("The fixture never assigned the discovered repository scope")
            self.assertFalse(any(row["id"].startswith("requirements-review-")
                                 for row in view["needs"].get("questions", [])), view["stop_reason"])
            self.assertNotEqual("answer", view["needs"]["kind"], view["stop_reason"])
            self.assertEqual(3, len(summary["members"]), "The consumer alias creates no duplicate run")
            for _ in range(20):
                summary = coordination.read(client, client.status())
                peer = next(row for row in summary["members"] if row["target"] == "history")
                peer_view = taskrun.TaskRun(Path(peer["workspace"]), Path(peer["run_dir"]), env=self.env).status()
                if peer_view["scope"]["decision"] == "linked_repositories":
                    break
                client.step()
            else:
                self.fail("The peer scope was not assigned through the public coordinator")
            self.assertEqual("Fix checkout showing the wrong final total.", peer_view["task"])
            self.assertEqual("linked_repositories", peer_view["scope"]["decision"])
            self.assertIn(
                "Coordinated checkout repository", [part["target"] for part in peer_view["scope"]["external_parts"]]
            )
            self.assertEqual([], self.github.posts, "Ownership assignment grants no publication authority")
            self.assertEqual([], self.questions, "Clear member requirements need no separate confirmation")

    def test_combined_suite_preserves_existing_cases_and_proves_the_joint_regression(self):
        from .coordination_provider import ASSERTIONS, JOINT_SETUP, JOINT_TEST, PRODUCT, TEST_PREAMBLE

        combined = self.root / "combined"
        combined.mkdir()
        manifest = {}
        for name, workspace in self.repos.items():
            relative = "repos/" + name
            shutil.copytree(workspace, combined / relative, ignore=shutil.ignore_patterns(".git"))
            manifest[name] = {"path": relative}
        (combined / "repositories.json").write_text(json.dumps(manifest))
        (combined / ".gitignore").write_text(".autocode/\n__pycache__/\n.pytest_cache/\n")
        for arguments in (
            ["init", "-q"],
            ["config", "user.name", "Fixture"],
            ["config", "user.email", "fixture@example.test"],
            ["add", "--all"],
            ["-c", "commit.gpgsign=false", "commit", "-qm", "Original combined code and its existing tests"],
        ):
            subprocess.run(["git", "-C", str(combined), *arguments], check=True, capture_output=True)
        base = subprocess.check_output(["git", "-C", str(combined), "rev-parse", "HEAD"], text=True).strip()
        for name, (filename, contents) in PRODUCT.items():
            (combined / "repos" / name / filename).write_text(contents)
            (combined / "repos" / name / "tests/test_behavior.py").write_text(TEST_PREAMBLE + ASSERTIONS[name] + "\n")
        (combined / "tests").mkdir()
        (combined / "tests" / "test_joint.py").write_text(JOINT_TEST)
        (combined / "conftest.py").write_text(JOINT_SETUP)
        (combined / "pytest.ini").write_text("[pytest]\naddopts = --import-mode=importlib -p no:cacheprovider\n")
        criteria = self.root / "criteria.json"
        suite = "python3 -m pytest -q"
        criteria.write_text(
            json.dumps(
                {
                    "intended_outcome": "Fix checkout showing the wrong final total.",
                    "proof_kind": "bugfix",
                    "acceptance_criteria": [
                        {
                            "id": "C1",
                            "criterion": "Actual checkout calls history and tax to display $110.00; existing boundary behavior is preserved",
                            "verification_method": suite,
                            "human_review": False,
                        }
                    ],
                }
            )
        )
        reply = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/autocode.py"),
                "verify",
                "--workspace",
                str(combined),
                "--base",
                base,
                "--criteria-file",
                str(criteria),
                "--suite-command",
                suite,
                "--out",
                str(self.root / "proof"),
                "--engine",
                "codex",
                "--model",
                "gpt-5.6-sol",
                "--evidence-provenance",
                "fake",
                "--json",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(0, reply.returncode, reply.stderr + reply.stdout[-4000:])
        result = json.loads(reply.stdout)
        self.assertEqual("PASS", result["verdict"])
        reread = subprocess.run(
            [sys.executable, str(ROOT / "tools/autocode.py"), "verify", "--record", result["record"], "--json"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, reread.returncode, reread.stderr)
        self.assertEqual("PASS", json.loads(reread.stdout)["verdict"])
        proof = result["evidence_report"]["document"]["regression_proof"]
        self.assertTrue(proof["fail_to_pass"])
        self.assertEqual(7, len(proof["checks"]["suite_on_candidate"]["results"]["passed"]))

    def test_locally_green_contract_mismatch_is_repaired_before_any_pr(self):
        self.env["COORDINATION_FIXTURE_MISMATCH"] = "1"
        self.github.drop_once = True
        self.run_flow()
        self.assertTrue(self.observed_mismatch, "The actual joint regression must reject the first candidate set")
        self.assertTrue(self.reopened, "A lost publication reply must be recovered by reopening the TUI")

    def test_a_refused_role_label_repairs_to_the_canonical_members_and_continues(self):
        # #2420: the live attempt-5 tax investigation labeled the checkout owner
        # "Checkout display member" and receipts "Receipt-handling member". The
        # report contract refuses both, the normal report repair names "checkout"
        # and "history" with both obligations kept, and delivery continues with no
        # /repos or repository-choice prompt: the only questions this flow may ask
        # are the genuine plan approvals.
        self.env["COORDINATION_FIXTURE_MEMBER_CONTEXT"] = "1"
        self.env["COORDINATION_FIXTURE_SCOPE_METADATA"] = "1"
        self.env["COORDINATION_FIXTURE_MEMBER_ROLE_LABEL"] = "repair"
        self.run_flow()
        summary = coordination.read(self.flow_client, self.flow_client.status())
        tax = next(row for row in summary["members"] if row["target"] == "tax")
        view = taskrun.TaskRun(Path(tax["workspace"]), Path(tax["run_dir"]), env=self.env).status()
        parts = (view.get("scope") or {}).get("external_parts", [])
        self.assertEqual(["checkout", "history"], [part["target"] for part in parts])
        self.assertTrue(all("receipt" in part["work"] or "display" in part["work"] for part in parts))
        # The provider-call receipt proves the repair ran: one refused attempt,
        # one bounded report repair, and no private state is read.
        calls = [
            json.loads(line)
            for line in (Path(tax["workspace"]) / ".autocode" / "provider-calls.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [("investigate_bug", False), ("investigate_bug", True)],
            [
                (row["stage"], row["repair"])
                for row in calls
                if row["name"] == "tax" and row["stage"] == "investigate_bug"
            ],
        )

    def test_an_unrepairable_role_label_stops_the_member_without_catalog_prompts(self):
        self.env["COORDINATION_FIXTURE_MEMBER_CONTEXT"] = "1"
        self.env["COORDINATION_FIXTURE_MEMBER_ROLE_LABEL"] = "stuck"
        history = chat.History(self.intake, env=self.env)
        captured = {}

        def decide(question):
            if "Approve these coordinated plans?" in question:
                return "yes"
            # Diagnose the public gate before answering it, while the member's own
            # stop reason is still the displayed one.
            client = captured.get("client")
            if client and "stop" not in captured:
                summary = coordination.read(client, client.status())
                tax = next((row for row in summary["members"] if row["target"] == "tax"), None)
                if tax and tax["status"] != "STARTING":
                    run = taskrun.TaskRun(Path(tax["workspace"]), Path(tax["run_dir"]), env=self.env)
                    captured["stop"] = run.status().get("needs") or {}
                    captured["shown"] = "\n".join(self.lines[-8:])
            return "n"

        with patch.dict(os.environ, self.env, clear=True):
            client = chat.start(
                self.intake,
                "Fix checkout showing the wrong final total.",
                ["--workflow", "bugfix"],
                [*FIXTURE_OPTIONS, "--no-adaptive-planning"],
                env=self.env,
                ask=decide,
                say=self.lines.append,
                poll_seconds=chat.POLL_SECONDS,
            )
            self.addCleanup(client.close)
            client.history = history
            captured["client"] = client
            for _ in range(200):
                outcome = client.step()
                if outcome is not None:
                    break
            else:
                self.fail("The member with the unrepairable label never surfaced its stop")
            self.assertEqual(2, outcome, "\n".join(self.lines[-30:]))
            # The publicly shown stop is the refused report's own bounded failure:
            # the configured two repair attempts both rejected the same labels, no
            # publication, no repository-choice or /repos prompt.
            stop = captured["stop"]
            self.assertIn("The same step repeatedly failed", captured["shown"])
            self.assertEqual("answer", stop.get("kind"), captured["shown"])
            self.assertEqual(
                ("operational_exhaustion", "PAUSED_REPEATED_FAILURE"),
                (stop.get("resolver_scope"), stop.get("pause_origin")),
                captured["shown"],
            )
            summary = coordination.read(client, client.status())
            tax = next(row for row in summary["members"] if row["target"] == "tax")
            self.assertIn("PAUSED", tax["status"])
            self.assertEqual(("input", "tax"), (summary["needs"]["kind"], summary["needs"].get("target")))
            view = taskrun.TaskRun(Path(tax["workspace"]), Path(tax["run_dir"]), env=self.env).status()
            self.assertEqual(
                [], (view.get("scope") or {}).get("external_parts", []), "No ungrounded scope was persisted"
            )
            # The same invalid labels on every bounded attempt: one original and
            # the configured two refused repairs, then the visible stop.
            calls = [
                json.loads(line)
                for line in (Path(tax["workspace"]) / ".autocode" / "provider-calls.jsonl").read_text().splitlines()
            ]
            attempts = [
                (row["stage"], row["repair"])
                for row in calls
                if row["name"] == "tax" and row["stage"] == "investigate_bug"
            ]
            self.assertEqual(
                [("investigate_bug", False), ("investigate_bug", True), ("investigate_bug", True)], attempts
            )
            self.assertEqual([], self.github.posts)
            self.assertTrue(
                all("repos PATH" not in line for line in self.lines),
                "No unavailable-catalog prompt may answer a refused report: " + "\n".join(self.lines[-10:]),
            )


if __name__ == "__main__":
    unittest.main()
