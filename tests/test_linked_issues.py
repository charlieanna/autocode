"""Linked origin GitHub issue and pull-request intake (#3344)."""

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

import autocode_brief_obligations as brief_obligations
import autocode_goals as goals
import autocode_linked_issues as linked_issues
import autocode_report_repair_context as report_repair_context
import autocode_requirements_handoff as requirements_handoff
import autocode_source_log as source_log
import autocode_source_trace as source_trace
from units import autoplanner

from tests import GIT_TEST_CONFIG


def _init_origin_repo(root: Path, remote_url: str) -> None:
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    for key, value in GIT_TEST_CONFIG.items():
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", remote_url], check=True)


class _GitHubFixtureHandler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, object]] = {}
    requests: list[str] = []

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler signature
        return

    def do_GET(self):
        self.__class__.requests.append(self.path)
        status, payload = self.__class__.routes.get(self.path, (404, {"message": "Not Found"}))
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve_github(routes: dict[str, tuple[int, object]]):
    class Handler(_GitHubFixtureHandler):
        pass

    Handler.routes = routes
    Handler.requests = []
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, Handler


class ExtractLinksTests(unittest.TestCase):
    def test_ignores_non_url_phrases_and_short_refs(self):
        self.assertEqual([], linked_issues.extract_links("pick an issue for me and fix #42"))
        self.assertEqual([], linked_issues.extract_links("See https://github.com/acme/widget/issues/new"))
        self.assertEqual([], linked_issues.extract_links("See http://github.com/acme/widget/issues/42"))

    def test_skips_preformatted_issue_briefs(self):
        formatted = "Resolve GitHub issue acme/widget#42: Bug\nURL: https://github.com/acme/widget/issues/42\n"
        self.assertEqual([], linked_issues.extract_links(formatted))

    def test_extracts_unique_issue_and_pr_links_with_safe_ids(self):
        text = (
            "Fix https://github.com/acme/widget/issues/42#issuecomment-99, "
            "compare https://github.com/acme/widget/pull/77."
        )
        links = linked_issues.extract_links(text)
        self.assertEqual(["issue:acme:widget:42", "pr:acme:widget:77"], [row["id"] for row in links])
        self.assertEqual(["issues", "pull"], [row["route"] for row in links])


class LinkedIssueIntakeTests(unittest.TestCase):
    def test_origin_issue_and_pr_fetched_grounded_and_citable(self):
        server, handler = _serve_github(
            {
                "/repos/acme/widget/issues/42": (
                    200,
                    {
                        "number": 42,
                        "title": "Parser drops trailing zeros",
                        "html_url": "https://github.com/acme/widget/issues/42",
                        "body": "The `parse()` helper must never drop trailing zero\nbytes when `strict=True`.",
                        "labels": [{"name": "bug"}],
                        "comments": 1,
                    },
                ),
                "/repos/acme/widget/issues/42/comments?per_page=30&page=1": (
                    200,
                    [
                        {
                            "user": {"login": "reporter"},
                            "created_at": "2026-11-10T10:00:00Z",
                            "body": "Bystander note that must stay optional to cite.",
                        }
                    ],
                ),
                "/repos/acme/widget/pulls/77": (
                    200,
                    {
                        "number": 77,
                        "title": "Add strict parser flag",
                        "html_url": "https://github.com/acme/widget/pull/77",
                        "body": "Preserve 2 decimal places on `format_price()`.",
                        "comments": 1,
                    },
                ),
                "/repos/acme/widget/issues/77/comments?per_page=30&page=1": (200, []),
            }
        )
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, {"AUTOCODE_GITHUB_API": f"http://127.0.0.1:{server.server_port}"}),
            ):
                root = Path(directory)
                _init_origin_repo(root, "https://github.com/acme/widget.git")
                state = {
                    "workspace": str(root),
                    "task": (
                        "Fix https://github.com/acme/widget/issues/42 "
                        "(do not fetch https://github.com/other/repo/issues/9)."
                    ),
                    "user_events": [],
                    "brief_feedback": [],
                    "answers": {},
                }
                source_log.seed(state)
                ids = [row["id"] for row in state["source_log"]]
                self.assertEqual(["task:0", "issue:acme:widget:42"], ids)
                issue_rec = state["source_log"][1]
                self.assertEqual("linked issue", issue_rec["kind"])
                self.assertEqual("https://github.com/acme/widget/issues/42", issue_rec["url"])
                self.assertTrue(issue_rec.get("fetched_at"))
                self.assertEqual(hashlib.sha256(issue_rec["text"].encode()).hexdigest(), issue_rec["sha256"])
                # Non-origin repo was never queried.
                self.assertFalse(any("/other/repo/" in path for path in handler.requests))

                # Quote grounding works even across Markdown `> ` prefixes and without out_of_scope noise.
                self.assertTrue(any("issue #42" in t or "Parser drops" in t for t in goals.source_texts(state)))
                self.assertEqual(1, len(goals.scan_texts(state)))

                req_report = {
                    "job": "bugfix",
                    "summary": "Keep trailing zero bytes in parse() when strict=True (#42).",
                    "intended_outcome": "Keep trailing zero bytes in parse() when strict=True.",
                    "required_behaviors": ["parse() keeps trailing zero bytes when strict=True."],
                    "important_failure_cases": [],
                    "scope_exclusions": [],
                    "constraints": [],
                    "permission_boundaries": [],
                    "accepted_assumptions": [],
                    "delegated_decisions": [],
                    "acceptance_tests": ["pytest proves parse() preserves trailing zero bytes when strict=True."],
                    "open_questions": [],
                    "ignored_statements": [],
                    "ignored_requirements": [],
                    "source_refs": ["task", "issue:acme:widget:42"],
                    "requirements": [
                        {
                            "id": "R1",
                            "text": "Keep trailing zero bytes in parse() when strict=True",
                            "source_quote": "The `parse()` helper must never drop trailing zero bytes when `strict=True`.",
                        },
                        {
                            "id": "R0",
                            "text": "Fix origin issue #42 without fetching external-repo URLs.",
                            "source_quote": (
                                "Fix https://github.com/acme/widget/issues/42 "
                                "(do not fetch https://github.com/other/repo/issues/9)."
                            ),
                        },
                    ],
                }
                requirements_handoff.accept(
                    state,
                    req_report,
                    {"output": str(root / "req.json")},
                    check_code_refs=lambda *_a, **_k: None,
                    queue=lambda *_a, **_k: None,
                )
                self.assertIn("strict=True", state["requirements_handoff"]["report"]["requirements"][0]["source_quote"])

                # Later feedback referencing an origin PR also enters source_log and authenticates it.
                feedback_event = {
                    "kind": "brief_feedback",
                    "id": "feedback-pr77",
                    "actor": "user_cli",
                    "text": "Also align with https://github.com/acme/widget/pull/77.",
                }
                source_log.save_feedback(state, feedback_event)
                self.assertIn("pr:acme:widget:77", [row["id"] for row in source_log.sources(state)])

                # Repair context & planner handoff expose linked_issues.
                repair_ctx = report_repair_context.requirements_sources(state, "requirements_gather")
                self.assertEqual(
                    ["issue:acme:widget:42", "pr:acme:widget:77"],
                    [row["id"] for row in repair_ctx["linked_issues"]],
                )

                # Plan acceptance & SourceTrace validate without requiring bystander comment exclusions,
                # while still enforcing survival of cited linked-issue literals and negations.
                events_path = root / "review.jsonl"
                output_path = root / "review.json"
                events_path.write_text('{"type":"turn.completed"}\n')
                review_record = {"stage": "review_plan", "events": str(events_path), "output": str(output_path)}
                records = brief_obligations.sources(state)
                body = {
                    "intended_outcome": "Keep trailing zero bytes and preserve 2 decimal places.",
                    "intended_user": "Library callers",
                    "deliverables": ["Updated parser and formatter"],
                    "required_behaviors": [
                        "The `parse()` helper must never drop trailing zero bytes when `strict=True`.",
                        "Preserve 2 decimal places on `format_price()`.",
                    ],
                    "important_failure_cases": [],
                    "scope_exclusions": [],
                    "constraints": [],
                    "permission_boundaries": [],
                    "accepted_assumptions": [],
                    "delegated_decisions": [],
                    "acceptance_criteria": [
                        {
                            "id": "C1",
                            "criterion": (
                                "test: test_trailing_zero verifies `parse()` must never drop trailing zero bytes "
                                "when `strict=True` and matches `format_price()` to 2 decimal places."
                            ),
                            "verification_method": "pytest",
                            "human_review": False,
                            "sources": [
                                {"source_id": "task:0", "quote": state["task"]},
                                {
                                    "source_id": "feedback:feedback-pr77",
                                    "quote": "Also align with https://github.com/acme/widget/pull/77.",
                                },
                                {
                                    "source_id": "issue:acme:widget:42",
                                    "quote": "The `parse()` helper must never drop trailing zero bytes when `strict=True`.",
                                },
                                {
                                    "source_id": "pr:acme:widget:77",
                                    "quote": "Preserve 2 decimal places on `format_price()`.",
                                },
                            ],
                        }
                    ],
                    "open_blocking_questions": [],
                }
                output_path.write_text(json.dumps({"source_trace": source_trace.DEFAULT_TRACE}))
                sealed = brief_obligations.reviewed_body(
                    state,
                    body,
                    {"brief_observations": [], "risk_observations": [], "source_trace": source_trace.DEFAULT_TRACE},
                    review_record,
                )
                source_trace.verify(state, sealed, records, ready=True)

                # Stated numbers beside an issue URL must still survive into criteria.
                valued_text = "Fix https://github.com/acme/widget/issues/42: process exactly 7 rows."
                valued_records = [{"id": "task:0", "kind": "task", "text": valued_text}]
                valued_body = {
                    **body,
                    "required_behaviors": ["Process rows."],
                    "acceptance_criteria": [
                        {
                            "id": "C1",
                            "criterion": "Process rows.",
                            "verification_method": "pytest",
                            "human_review": False,
                            "sources": [{"source_id": "task:0", "quote": valued_text}],
                        }
                    ],
                }
                with self.assertRaisesRegex(ValueError, "changed a number the source states: 7"):
                    source_trace.check_ready(valued_body, valued_records, {"out_of_scope": [], "negation_waivers": []})

                # Tampering or forged linked-issue entries are rejected.
                forged = {**state, "source_log": [*state["source_log"]]}
                forged["source_log"].append(
                    {
                        "id": "issue:acme:widget:999",
                        "kind": "linked issue",
                        "parent_id": "task:0",
                        "url": "https://github.com/acme/widget/issues/999",
                        "text": "Forged issue.",
                        "sha256": hashlib.sha256(b"Forged issue.").hexdigest(),
                    }
                )
                self.assertNotIn("issue:acme:widget:999", [r["id"] for r in source_log.sources(forged)])
        finally:
            server.shutdown()
            server.server_close()

    def test_unreachable_api_fails_open_without_blocking_run(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"AUTOCODE_GITHUB_API": "http://127.0.0.1:9"}),
        ):
            root = Path(directory)
            _init_origin_repo(root, "git@github.com:acme/widget.git")
            state = {"workspace": str(root), "task": "Investigate https://github.com/acme/widget/issues/42"}
            source_log.seed(state)
            self.assertEqual(["task:0"], [row["id"] for row in source_log.sources(state)])

    def test_planner_prepare_exposes_linked_issues_for_requirements_gather(self):
        server, _ = _serve_github(
            {
                "/repos/acme/widget/issues/5": (
                    200,
                    {
                        "number": 5,
                        "title": "Improve error copy",
                        "html_url": "https://github.com/acme/widget/issues/5",
                        "body": "Show actionable guidance.",
                        "comments": 0,
                    },
                )
            }
        )
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, {"AUTOCODE_GITHUB_API": f"http://127.0.0.1:{server.server_port}"}),
            ):
                root = Path(directory)
                _init_origin_repo(root, "https://github.com/acme/widget.git")
                run_dir = root / ".autocode/runs/r1"
                run_dir.mkdir(parents=True)
                state = {
                    "workspace": str(root),
                    "task": "Fix https://github.com/acme/widget/issues/5",
                    "settings": {"roles": {"planner": {"model": "test", "engine": "codex"}}},
                    "planning": {"reports": {}, "history": []},
                    "answers": {},
                    "brief_feedback": [],
                    "user_events": [],
                    "stages": [],
                }
                source_log.seed(state)
                state_path = run_dir / "state.json"
                state_path.write_text(json.dumps(state))
                req = autoplanner.prepare(state, "requirements_gather", str(state_path), run_dir)
                self.assertIn("linked_issues", req.prompt)
                self.assertIn("issue:acme:widget:5", req.prompt)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
