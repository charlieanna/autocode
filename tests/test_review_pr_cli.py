"""Real review-pr CLI/public TaskRun, local bare remotes and recorded GitHub writes.

Both existing catalog oracles stay unchanged. Their base copy applies the original
patch independently; AutoCode reviews the already-patched fetched PR head.
"""

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from autocode_taskrun import TaskRun
from harness import catalog, driver

from tests import GIT_TEST_CONFIG

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/acme/widgets/pull/7"


class RecordedAPI(BaseHTTPRequestHandler):
    def handle_request(self):
        api = self.server
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        value = json.loads(raw) if raw else None
        api.calls.append((self.command, self.path, self.headers.get("Authorization"), value))
        path = urlsplit(self.path).path
        if path == "/repos/acme/widgets/pulls/7" and self.command == "GET":
            api.reads += 1
            pull = dict(api.pull)
            if api.drift and api.reads > 1:
                pull["head"] = {"sha": "f" * 40}
            return self.reply(200, pull)
        if path == "/repos/acme/widgets/pulls/7/files" and self.command == "GET":
            return self.reply(200, api.files)
        if path == "/repos/acme/widgets/pulls/7/reviews" and self.command == "POST":
            api.reviews.append(value)
            return self.reply(201, {"html_url": URL + "#pullrequestreview-1"})
        return self.reply(404, {"message": "Unexpected recorded API call"})

    do_GET = handle_request
    do_POST = handle_request

    def reply(self, code, value):
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class ReviewPullRequestCLI(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="review-pr-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()

    def repository(self, scenario_id, *, generated_cache=False):
        scenario = catalog.load(scenario_id)
        self.oracle_base = self.root / "oracle-base"
        shutil.copytree(scenario.dir / "seed", self.oracle_base)
        self.git(self.oracle_base, "init", "-q")
        for key, value in {**GIT_TEST_CONFIG, "user.name": "fixture", "user.email": "fixture@example.test"}.items():
            self.git(self.oracle_base, "config", key, value)
        self.git(self.oracle_base, "add", ".")
        self.git(self.oracle_base, "commit", "-qm", "independent scenario base")
        source = self.root / "pr-source"
        shutil.copytree(scenario.dir / "seed", source, ignore=shutil.ignore_patterns("*.patch"))
        self.git(source, "init", "-q", "-b", "main")
        for key, value in {**GIT_TEST_CONFIG, "user.name": "fixture", "user.email": "fixture@example.test"}.items():
            self.git(source, "config", key, value)
        if generated_cache:
            (source / "cache_fixture.py").write_text("VALUE = 7\n")
            (source / "keeper_fixture.py").write_text(
                "import subprocess, sys\n"
                "subprocess.run([sys.executable, '-E', '-c', 'import cache_fixture; assert cache_fixture.VALUE == 7'], check=True)\n"
            )
        self.git(source, "add", ".")
        self.git(source, "commit", "-qm", "base")
        base = self.git(source, "rev-parse", "HEAD")
        [patch] = (scenario.dir / "seed").glob("*.patch")
        self.git(source, "apply", str(patch))
        self.git(source, "add", ".")
        self.git(source, "commit", "-qm", "PR head")
        head = self.git(source, "rev-parse", "HEAD")
        remote = self.root / "widgets.git"
        remote.mkdir()
        self.git(remote, "init", "--bare", "-q")
        for key, value in GIT_TEST_CONFIG.items():
            self.git(remote, "config", key, value)
        self.git(source, "remote", "add", "origin", str(remote))
        self.git(source, "push", "-q", "origin", f"{base}:refs/heads/main", f"{head}:refs/pull/7/head")
        patches = {}
        name = None
        for row in self.git(source, "diff", "--no-ext-diff", base, head).splitlines():
            if row.startswith("+++ b/"):
                name = row[6:]
                patches[name] = []
            elif name and (row.startswith("@@ ") or (patches[name] and row[:1] in {"+", "-", " ", "\\"})):
                patches[name].append(row)
            elif row.startswith("diff --git"):
                name = None
        return (
            scenario,
            head,
            base,
            remote,
            [{"filename": name, "status": "modified", "patch": "\n".join(rows)} for name, rows in patches.items()],
        )

    def execute(
        self,
        scenario_id,
        *,
        post=False,
        drift=False,
        variant=None,
        alias=False,
        unrelated_patch=False,
        generated_cache=False,
    ):
        scenario, head, base, remote, files = self.repository(scenario_id, generated_cache=generated_cache)
        fake = self.root / "fake"
        solution = scenario.dir / ("broken/" + variant if variant else "reference")
        flags, env_extra = driver.fake_setup(scenario, fake, solution)
        fake_provider = fake / "bin/codex"
        code = fake_provider.read_text()
        code = code.replace(
            "import os\n",
            "import os\nassert not any(os.environ.get(k) for k in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN', 'GIT_CONFIG_VALUE_0'))\n",
            1,
        )
        if unrelated_patch:
            before = (self.root / "pr-source/README.md").read_text()
            difference = "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    (before + "\nUnrelated review scratch change.\n").splitlines(keepends=True),
                    fromfile="a/README.md",
                    tofile="b/README.md",
                )
            )
            code = code.replace(
                '"change_patch": patches[0] if patches else "",',
                '"change_patch": "review/unrelated.patch",',
                1,
            )
            code = code.replace(
                '    if stage == "review_change":\n        return review()',
                '    if stage == "review_change":\n        Path("review").mkdir(exist_ok=True)\n'
                + '        Path("review/unrelated.patch").write_text('
                + repr(difference)
                + ")\n        return review()",
                1,
            )
        if generated_cache:
            code = code.replace(
                '    if stage == "review_change":\n        return review()',
                '    if stage == "review_change":\n'
                + "        import subprocess, sys\n"
                + '        assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"\n'
                + '        subprocess.run([sys.executable, "-E", "keeper_fixture.py"], check=True)\n'
                + '        assert not list(Path("__pycache__").glob("*.pyc"))\n'
                + "        return review()",
                1,
            )
        fake_provider.write_text(code)
        api = ThreadingHTTPServer(("127.0.0.1", 0), RecordedAPI)
        api.pull = {
            "state": "open",
            "title": scenario.title,
            "body": scenario.brief,
            "head": {"sha": head},
            "base": {"sha": base, "repo": {"full_name": "acme/widgets", "clone_url": remote.as_uri()}},
        }
        api.files, api.calls, api.reviews, api.reads, api.drift = files, [], [], 0, drift
        thread = threading.Thread(target=api.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(api.server_close)
        self.addCleanup(api.shutdown)
        env = {
            **env_extra,
            "CODEX_HOME": str(fake / "codex-home"),
            "AUTOCODE_HOME": str(self.root / "registry"),
            "AUTOCODE_GITHUB_API": f"http://127.0.0.1:{api.server_port}",
            "GH_TOKEN": "offline-token",
            "GITHUB_TOKEN": "offline-token",
            "GH_ENTERPRISE_TOKEN": "offline-token",
            "GITHUB_ENTERPRISE_TOKEN": "offline-token",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraheader",
            "GIT_CONFIG_VALUE_0": "Authorization offline-secret",
        }
        entry = "autocode_issue.py" if alias else "autocode.py"
        out = self.root / "review-output"
        proc = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "tools" / entry),
                "review-pr",
                URL,
                "--out",
                str(out),
                "--json",
                *(["--post"] if post else []),
                "--",
                *flags,
            ],
            env=env,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=scenario.timeout_minutes * 60,
        )
        return proc, api, head, out, env, scenario

    def oracle(self, result, env, scenario):
        workspace = Path(result["workspace"])
        shutil.copytree(workspace / "review", self.oracle_base / "review")
        view = TaskRun(workspace, Path(result["run_dir"]), env=env).status(inspect_evidence=True)
        stages = [row["stage"] for row in result["evidence_report"]["document"]["attempts"]]
        checks = scenario.oracle()(
            self.oracle_base,
            scenario,
            {"status": view["status"], "view": view, "stages": stages, "answers": [], "cli_calls": ["start", "status"]},
        )
        self.assertTrue(all(row.ok for row in checks), [(row.name, row.ok, row.detail) for row in checks])
        self.assertEqual(["review_change"], stages)
        self.assertTrue(view["done"])
        self.assertIsNone(view["needs"])
        self.assertEqual("recorded", result["evidence_report"]["availability"])
        self.assertEqual("review", result["evidence_report"]["document"]["run"]["workflow"])
        self.assertEqual(
            result["evidence_report"]["document"]["workflow_result"]["artifact_sha256"],
            view["evidence"]["workflow_result"]["artifact_sha256"],
        )

    def test_planted_defects_are_reviewed_from_exact_fetched_head_and_post_comment_only(self):
        proc, api, head, _, env, scenario = self.execute("review-planted-defects", post=True)
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout[-3000:])
        result = json.loads(proc.stdout)
        self.assertEqual(head, result["head_sha"])
        self.oracle(result, env, scenario)
        self.assertEqual(1, len(api.reviews))
        posted = api.reviews[0]
        self.assertEqual(("COMMENT", head), (posted["event"], posted["commit_id"]))
        self.assertEqual(3, len(posted["comments"]))
        self.assertTrue(all(row["side"] == "RIGHT" for row in posted["comments"]))
        self.assertIn(result["evidence_report"]["markdown"], posted["body"])
        self.assertEqual("GET", api.calls[-2][0])
        self.assertEqual("POST", api.calls[-1][0])

    def test_vacuous_tests_require_real_targeted_failure_and_fixed_success_through_issue_alias(self):
        proc, api, _, _, env, scenario = self.execute("review-vacuous-tests", alias=True)
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout[-3000:])
        result = json.loads(proc.stdout)
        self.oracle(result, env, scenario)
        self.assertTrue(result["findings"]["findings"][0]["proven_by"])
        self.assertEqual([], api.reviews)
        self.assertTrue(all(row[0] == "GET" for row in api.calls))

    def test_new_head_is_refused_before_any_post(self):
        proc, api, _, _, _, _ = self.execute("review-planted-defects", post=True, drift=True)
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("head changed before posting", proc.stderr)
        self.assertEqual([], api.reviews)

    def test_proven_unrelated_patch_is_refused_even_after_ordinary_review_accepts_it(self):
        proc, api, _, out, env, _ = self.execute("review-vacuous-tests", post=True, unrelated_patch=True)
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("fetched head", proc.stderr)
        self.assertEqual([], api.reviews)
        record = json.loads((out / "review-pr.json").read_text())
        view = TaskRun(Path(record["workspace"]), Path(record["run_dir"]), env=env).status(inspect_evidence=True)
        self.assertTrue(view["done"])  # ordinary review proved this valid, unrelated scratch patch
        artifact = Path(record["workspace"]) / "review/findings.json"
        raw = artifact.read_bytes()
        report = json.loads(raw)
        self.assertEqual("review/unrelated.patch", report["change_patch"])
        self.assertTrue(report["findings"][0]["proven_by"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), view["evidence"]["workflow_result"]["artifact_sha256"])

    def test_vacuous_delivered_test_cannot_make_a_publishable_review(self):
        proc, api, _, _, _, _ = self.execute("review-vacuous-tests", post=True, variant="vacuous-targeted-test")
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("Review stopped", proc.stderr)
        self.assertEqual([], api.reviews)

    @unittest.skipIf(os.geteuid() == 0, "POSIX cache-write prevention requires an unprivileged process")
    def test_keeper_ignoring_no_bytecode_env_does_not_dirty_or_refuse_the_review(self):
        proc, api, head, _, env, scenario = self.execute("review-planted-defects", generated_cache=True)
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout[-3000:])
        result = json.loads(proc.stdout)
        self.assertEqual(head, result["head_sha"])
        self.oracle(result, env, scenario)
        workspace = Path(result["workspace"])
        self.assertEqual([], list((workspace / "__pycache__").iterdir()))
        self.assertEqual(0o555, (workspace / "__pycache__").stat().st_mode & 0o777)
        self.assertEqual("", self.git(workspace, "diff", "HEAD", "--name-only"))
        self.assertEqual([], api.reviews)
        self.assertTrue(all(row[0] == "GET" for row in api.calls))
