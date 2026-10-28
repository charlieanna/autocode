"""Public review publication refuses changed evidence, stale heads and invalid anchors."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_github as github
import autocode_pr_checkout as checkout
import autocode_pr_review as publication
import autocode_review_pr as cli


class RecordedGitHubTests(unittest.TestCase):
    def test_comment_transport_can_only_comment_and_paginates(self):
        calls = []
        pages = [[{"filename": f"f{x}"} for x in range(100)], [{"filename": "last"}], {"html_url": "review"}]

        def transport(method, url, headers, body):
            calls.append((method, url, json.loads(body) if body else None))
            return 200, pages.pop(0)

        client = github.Client(token="offline-token", transport=transport)
        ref = github.parse_pull_request_url("https://github.com/acme/widgets/pull/7")
        self.assertEqual(101, len(client.pull_request_files(ref)))
        client.comment_review(ref, commit_id="a" * 40, body="canonical", comments=[])
        self.assertEqual(["GET", "GET", "POST"], [row[0] for row in calls])
        self.assertIn("page=2", calls[1][1])
        self.assertEqual({"event": "COMMENT", "commit_id": "a" * 40, "body": "canonical", "comments": []}, calls[2][2])

    def test_issue_urls_and_credential_urls_are_not_pull_request_urls(self):
        for url in (
            "https://github.com/a/b/issues/7",
            "https://token@github.com/a/b/pull/7",
            "http://github.com/a/b/pull/7",
            "https://github.com/a/b/pull/0",
        ):
            with self.subTest(url=url), self.assertRaises(github.GitHubError):
                github.parse_pull_request_url(url)

    def test_a_value_that_is_not_a_pull_request_url_is_told_the_form_it_takes(self):
        for given in (
            "12",
            "charlieanna/autocode#12",
            "https://github.com/a/b/pulls",
            "https://github.com/a/b/issues/7",
        ):
            with self.subTest(given=given), self.assertRaises(github.GitHubError) as caught:
                github.parse_pull_request_url(given)
            self.assertEqual(
                f"pull request URL must be https://github.com/OWNER/REPO/pull/N, not {given!r}", str(caught.exception)
            )
        with self.assertRaises(github.GitHubError) as caught:
            github.parse_pull_request_url("https://token@github.com/a/b/pull/7")
        self.assertNotIn("token", str(caught.exception), "a credential in the value is never echoed")
        with self.assertRaises(github.GitHubError) as caught:
            github.parse_pull_request_url("https://foreign/a/b/pull/7", api="https://enterprise.test:8443/api/v3")
        self.assertEqual(
            "pull request URL must match the configured GitHub API web host and port: "
            "https://enterprise.test:8443/OWNER/REPO/pull/N",
            str(caught.exception),
        )

    def test_web_host_and_port_bind_to_the_configured_api_before_any_get(self):
        for api, accepted in (
            ("https://api.github.com", "https://github.com/a/b/pull/7#discussion"),
            ("https://enterprise.test:8443/api/v3", "https://enterprise.test:8443/a/b/pull/7"),
            ("http://127.0.0.1:12345", "https://github.com/a/b/pull/7"),
        ):
            self.assertEqual(7, github.parse_pull_request_url(accepted, api=api).number)
            for url in (
                "https://foreign/a/b/pull/7",
                "https://github.com:8443/a/b/pull/7",
                "https://github.com:0/a/b/pull/7",
            ):
                with self.subTest(api=api, url=url), self.assertRaises(github.GitHubError):
                    github.parse_pull_request_url(url, api=api)
        with self.subTest(api_zero_port=True):
            self.assertEqual(("enterprise.test", 0), github.web_authority("https://enterprise.test:0/api/v3"))
        with self.subTest(clone_zero_port=True), self.assertRaises(checkout.CheckoutError):
            checkout.clone_url("https://github.com:0/a/b.git", api="https://api.github.com", owner="a", repo="b")
        calls = []
        client = github.Client(transport=lambda *args: calls.append(args))
        with self.assertRaises(github.GitHubError):
            cli.review("https://foreign/acme/widgets/pull/7", client=client)
        self.assertEqual([], calls)
        for clone in ("https://github.com:8443/a/b.git", "https://enterprise.test/a/b.git"):
            with self.assertRaises(checkout.CheckoutError):
                checkout.clone_url(clone, api="https://enterprise.test:8443/api/v3", owner="a", repo="b")
        self.assertEqual(
            "https://enterprise.test:8443/a/b.git",
            checkout.clone_url(
                "https://enterprise.test:8443/a/b.git", api="https://enterprise.test:8443/api/v3", owner="a", repo="b"
            ),
        )

    def test_options_cannot_change_workflow_or_approve_or_pick_foreign_workspace(self):
        for flags in (
            ["--workflow", "build"],
            ["--workspace", "/foreign"],
            ["--approve-goal", "token"],
            ["--single-model"],
        ):
            with self.subTest(flags=flags), self.assertRaises(publication.ReviewError):
                cli.creation_options(flags)
        self.assertEqual(
            ("--provider", "opencode", "--sol-model", "zai-coding-plan/glm-5.3"),
            cli.creation_options(["--provider", "opencode", "--sol-model", "zai-coding-plan/glm-5.3"]),
        )

    def test_reasoning_effort_uses_the_existing_creation_option(self):
        self.assertEqual(("--reasoning-effort", "high"), cli.creation_options(["--reasoning-effort", "high"]))

    def test_model_environment_retains_provider_and_drops_github_tokens_and_git_headers(self):
        before = {
            "GH_TOKEN": "secret",
            "GITHUB_TOKEN": "secret",
            "GITHUB_ENTERPRISE_TOKEN": "secret",
            "GH_ENTERPRISE_TOKEN": "secret",
            "GH_CONFIG_DIR": "/secret",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_VALUE_0": "Authorization secret",
            "GIT_ASKPASS": "/secret",
            "ZAI_API_KEY": "provider",
            "PATH": "bin",
        }
        after = checkout.child_environment(before)
        self.assertEqual("provider", after["ZAI_API_KEY"])
        self.assertEqual("bin", after["PATH"])
        self.assertFalse(any(k in after for k in before if k not in {"ZAI_API_KEY", "PATH"}))
        with patch.dict(os.environ, before, clear=True):
            task = checkout.task_environment({"ZAI_API_KEY": "selected"})
            merged = {**os.environ, **task}  # the public TaskRun merge
        self.assertEqual("selected", merged["ZAI_API_KEY"])
        self.assertEqual("0", merged["GIT_CONFIG_COUNT"])
        self.assertFalse(any(merged[k] for k in before if k not in {"ZAI_API_KEY", "PATH", "GIT_CONFIG_COUNT"}))


class OutputCustodyTests(unittest.TestCase):
    def test_new_nested_output_supports_relative_paths_without_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            with patch("os.getcwd", return_value=str(parent)):
                result = checkout.create_root("nested/new")
            self.assertTrue(result.is_dir())
            with self.assertRaises(checkout.CheckoutError):
                checkout.create_root(result)

    def test_symlink_parent_and_traversal_are_refused_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            foreign = parent / "foreign"
            foreign.mkdir()
            (parent / "alias").symlink_to(foreign, target_is_directory=True)
            with self.assertRaises(checkout.CheckoutError):
                checkout.create_root(parent / "alias" / "new")
            self.assertEqual([], list(foreign.iterdir()))
            with self.assertRaises(checkout.CheckoutError):
                checkout.create_root(parent / "new" / ".." / "other")
            self.assertFalse((parent / "new").exists())


class AnchorTests(unittest.TestCase):
    FILES = [{"filename": "rates.py", "status": "modified", "patch": "@@ -1,3 +1,3 @@\n same\n-old\n+new\n final"}]

    def report(self, **changes):
        return {
            "findings": [
                {
                    "id": "F1",
                    "file": "rates.py",
                    "lines": [2, 3],
                    "severity": "blocking",
                    "summary": "wrong rate",
                    "evidence": "real test fails",
                    **changes,
                }
            ]
        }

    def test_multiline_comments_use_new_side_and_complete_span(self):
        [row] = publication.comments(self.report(), self.FILES)
        self.assertEqual(
            ("rates.py", "RIGHT", 3, 2, "RIGHT"),
            (row["path"], row["side"], row["line"], row["start_line"], row["start_side"]),
        )
        self.assertIn("real test fails", row["body"])

    def test_no_finding_is_silently_dropped_or_moved_to_other_side(self):
        for changes in (
            {"file": "../rates.py"},
            {"lines": [0]},
            {"lines": [True]},
            {"lines": [3, 1]},
            {"lines": []},
        ):
            with self.subTest(changes=changes), self.assertRaises(publication.ReviewError):
                publication.comments(self.report(**changes), self.FILES)
        for files in (
            [{**self.FILES[0], "patch": "@@ -1,3 +1,3 @@\n only"}],
            self.FILES * 2,
        ):
            with self.subTest(files=files), self.assertRaises(publication.ReviewError):
                publication.comments(self.report(), files)

    def test_outside_diff_observations_stay_in_summary_without_inline_anchors(self):
        for changes in ({"file": "else.py"}, {"lines": [4]}):
            report = self.report(**changes)
            self.assertEqual([], publication.comments(report, self.FILES))
            text = publication.summary(
                report, {"markdown": "Authenticated evidence"}, "https://github.com/acme/r/pull/1", "abc"
            )
            self.assertIn("wrong rate", text)
            self.assertIn("real test fails", text)
        for files in ([{**self.FILES[0], "status": "removed"}], [{**self.FILES[0], "patch": None}]):
            self.assertEqual([], publication.comments(self.report(), files))

    def test_disposition_labels_observations_and_inconclusive_blockers(self):
        from autocode_review_attribution import RUNNER

        ref = {"path": "/proof.json", "sha256": "a" * 64}
        disposition = {
            "runner": RUNNER,
            "kind": "pre_existing",
            "reason": "Equivalent full failures",
            "base_proof": ref,
            "changed_proof": ref,
            "inputs": ref,
        }
        [row] = publication.comments(self.report(disposition=disposition), self.FILES)
        self.assertIn("pre-existing observation", row["body"])
        self.assertNotIn("[blocking]", row["body"])
        [row] = publication.comments(
            self.report(disposition={"kind": "inconclusive", "reason": "No baseline"}), self.FILES
        )
        self.assertIn("blocking; attribution inconclusive", row["body"])
        self.assertIn("No baseline", row["body"])

    def test_old_side_truncation_and_overflow_are_refused_even_when_new_side_is_complete(self):
        for text in (
            "@@ -1,2 +1 @@\n-old\n+new",
            "@@ -1,0 +1 @@\n-old\n+new",
            "@@ -1,2 +1 @@\n-old\n+new\n@@ -20 +20 @@\n other",
        ):
            with self.subTest(text=text), self.assertRaises(publication.ReviewError):
                publication.right_lines(text)
        self.assertEqual({1}, publication.right_lines("@@ -0,0 +1 @@\n+new"))
        self.assertEqual(set(), publication.right_lines("@@ -1 +0,0 @@\n-old"))

    def test_comment_span_cannot_cross_an_omitted_diff_gap(self):
        files = [{**self.FILES[0], "patch": "@@ -1 +1 @@\n+new\n-old\n@@ -20 +20 @@\n+other\n-old"}]
        self.assertEqual([], publication.comments(self.report(lines=[1, 20]), files))


class AuthenticatedArtifactTests(unittest.TestCase):
    def test_artifact_reader_refuses_links_oversize_short_reads_and_replaced_leaf(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            folder = workspace / "review"
            folder.mkdir()
            target = folder / "findings.json"
            raw = b'{"change_patch":"","verdict":"approve","findings":[]}\n'
            target.write_bytes(raw)
            self.assertEqual(raw, publication.read_artifact(workspace))
            with patch.object(publication, "REPORT_BYTES", len(raw) - 1), self.assertRaises(publication.ReviewError):
                publication.read_artifact(workspace)
            with (
                patch.object(publication.os, "read", return_value=b""),
                self.assertRaisesRegex(publication.ReviewError, "short-read"),
            ):
                publication.read_artifact(workspace)
            hardlink = folder / "hardlink"
            os.link(target, hardlink)
            with self.assertRaises(publication.ReviewError):
                publication.read_artifact(workspace)
            hardlink.unlink()
            original_read = os.read
            replaced = False

            def redirected(fd, count):
                nonlocal replaced
                value = original_read(fd, count)
                if not replaced:
                    replacement = folder / "replacement"
                    replacement.write_bytes(raw)
                    replacement.replace(target)
                    replaced = True
                return value

            with (
                patch.object(publication.os, "read", side_effect=redirected),
                self.assertRaises(publication.ReviewError),
            ):
                publication.read_artifact(workspace)
            target.unlink()
            target.symlink_to(folder / "missing")
            with self.assertRaises(publication.ReviewError):
                publication.read_artifact(workspace)
            target.unlink()
            target.write_bytes(raw)
            alias = workspace / "alias"
            folder.rename(alias)
            folder.symlink_to(alias, target_is_directory=True)
            with self.assertRaises(publication.ReviewError):
                publication.read_artifact(workspace)

    def test_ambiguous_duplicate_and_nonfinite_json_are_refused(self):
        for raw in (
            b'{"verdict":"approve","verdict":"request_changes"}',
            b'{"nested":{"x":1,"x":2}}',
            b'{"x":NaN}',
            b'{"x":Infinity}',
            b'{"x":-Infinity}',
            b'{"x":1e999}',
        ):
            with self.subTest(raw=raw), self.assertRaises(publication.ReviewError):
                publication.strict_json(raw)

    def test_only_authenticated_canonical_report_and_bound_owned_artifact_are_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp).resolve()
            (workspace / "review").mkdir()
            target = workspace / "review/findings.json"
            raw = b'{"verdict":"approve","change_patch":"","findings":[]}\n'
            target.write_bytes(raw)
            result = {
                "outcome": "approve",
                "artifact": "review/findings.json",
                "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            }
            canonical = {
                "availability": "current",
                "binding": "bound",
                "document": {"workflow_result": result},
                "markdown": "canonical",
            }
            view = {
                "workflow": "review",
                "done": True,
                "evidence": {"workflow_result": result},
                "evidence_report": {"availability": "recorded", "binding": "bound"},
            }
            run = SimpleNamespace(status=lambda **kw: view, evidence_report=lambda **kw: canonical)
            owned = SimpleNamespace(workspace=workspace, unchanged=lambda: None)
            report, _ = publication.findings(run, owned)
            self.assertEqual("approve", report["verdict"])
            target.write_bytes(b'{"verdict":"approve","findings":[{}]}\n')
            with self.assertRaisesRegex(publication.ReviewError, "changed"):
                publication.findings(run, owned)
            target.write_bytes(raw)
            for key, value in (("artifact", "../foreign"), ("artifact_sha256", None)):
                old = result[key]
                result[key] = value
                with self.assertRaises(publication.ReviewError):
                    publication.findings(run, owned)
                result[key] = old
            for change in (None, "review/unrelated.patch", " "):
                changed = json.dumps({"verdict": "approve", "change_patch": change, "findings": []}).encode()
                target.write_bytes(changed)
                result["artifact_sha256"] = hashlib.sha256(changed).hexdigest()
                with self.assertRaisesRegex(publication.ReviewError, "fetched head"):
                    publication.findings(run, owned)
            target.write_bytes(raw)
            result["artifact_sha256"] = hashlib.sha256(raw).hexdigest()
            canonical["availability"] = "recorded"
            self.assertEqual("approve", publication.findings(run, owned)[0]["verdict"])
            canonical["availability"] = "stale"
            with self.assertRaises(publication.ReviewError):
                publication.findings(run, owned)
            canonical["availability"] = "current"
            view["evidence_report"]["binding"] = "different"
            with self.assertRaises(publication.ReviewError):
                publication.findings(run, owned)
