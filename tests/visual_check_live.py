#!/usr/bin/env python3
"""Opt-in real-model/Chromium qualification of exported-reference acceptance.

Never imported by the routine test gate as a live test. No Figma service is used.
The CLI-driving deadline excludes bounded fixture setup, scoring and cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scenarios.harness import processes, profiles
from tests.visual_check_fixtures import BROWSER_CAPTURE_SOURCE, BROWSER_HTML

CAPS = (
    "--max-seconds",
    "720",
    "--max-stage-seconds",
    "300",
    "--max-iterations",
    "3",
    "--max-tool-seconds",
    "60",
    "--max-idle-seconds",
    "90",
    "--max-milestone-seconds",
    "720",
)
ALLOWED_CHANGES = ("app.html", "test_visual_acceptance.py")

FUNCTIONAL_TEST_SOURCE = '''"""Preserved behavior, independent of the visual reference comparison."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid


class BrowserBehaviorTests(unittest.TestCase):
    def test_run_button_updates_count(self):
        root = Path(__file__).resolve().parent
        output = root / ".autocode" / "functional-tests" / uuid.uuid4().hex
        output.mkdir(parents=True)
        environment = {**os.environ, "AUTOCODE_VISUAL_OUTPUT": str(output),
                       "AUTOCODE_VISUAL_MANIFEST": str(root / "design/manifest.json")}
        # The enclosing suite runner owns the deadline and descendant cleanup.
        result = subprocess.run([sys.executable, "capture.py"], cwd=root, env=environment,
                                capture_output=True, text=True)
        (output / "capture.stdout").write_text(result.stdout, encoding="utf-8")
        (output / "capture.stderr").write_text(result.stderr, encoding="utf-8")
        print("Functional capture: " + str(output))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Browser click assertion passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
'''


class Blocked(RuntimeError):
    """An unavailable prerequisite, unsupported gate or exhausted allowance."""


class ProofFailure(RuntimeError):
    """The delivered evidence did not establish the qualification."""


def require(condition, message):
    if not condition:
        raise ProofFailure(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def source_files(root):
    """Account for ignored/untracked additions too, not merely git diff."""
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        if Path(directory) == root:
            dirs[:] = [name for name in dirs if name not in (".git", ".autocode")]
        for name in [*dirs, *names]:
            path = Path(directory) / name
            require(not path.is_symlink(), f"Source symlink is not allowed: {path.relative_to(root)}")
        for name in names:
            path = Path(directory) / name
            require(path.is_file(), f"Not a regular source file: {path.relative_to(root)}")
            files[str(path.relative_to(root))] = {"sha256": sha256(path), "mode": path.stat().st_mode & 0o777}
    return files


def approved_criterion(criteria, command):
    """Only structured public records authorize approval; rendered prose never does."""
    require(isinstance(criteria, list) and criteria, "Structured acceptance criteria are missing")
    matches, identities = [], set()
    for row in criteria:
        require(isinstance(row, dict), "Malformed structured criterion")
        identity = row.get("id")
        require(
            isinstance(identity, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", identity)
            and identity not in identities,
            "Criterion identity is missing or ambiguous",
        )
        identities.add(identity)
        require(row.get("human_review") is False, "Every actual human_review must be the boolean false")
        verification = row.get("verification_method")
        require(isinstance(verification, str) and verification.strip(), "Criterion verification_method is missing")
        if verification == command:
            matches.append(identity)
    require(len(matches) == 1, "Exact pinned command must verify one executable non-human criterion")
    return matches[0]


def approval_token(display, expected):
    tokens = re.findall(r"^Approval token: ([^\r\n]*)\r?$", display, re.MULTILINE)
    require(
        isinstance(expected, str) and re.fullmatch(r"r[1-9][0-9]*:[0-9a-f]{64}", expected) and tokens == [expected],
        "Displayed approval token is missing, ambiguous or stale",
    )
    return expected


def approved_plan(status, display, command, expected_token):
    view = status.get("view") or {}
    plan = view.get("displayed_plan")
    if not isinstance(plan, dict):
        raise Blocked(
            "Public status does not expose structured displayed_plan; rendered text cannot authorize approval"
        )
    token = approval_token(display, expected_token)
    revision, digest = plan.get("revision"), plan.get("hash")
    require(
        type(revision) is int and revision > 0 and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest),
        "Displayed plan revision/hash is missing or invalid",
    )
    require(
        plan.get("token") == token == f"r{revision}:{digest}",
        "Displayed plan identity does not match its approval token",
    )
    need = view.get("needs") or {}
    require(
        need.get("kind") == "approve_plan" and need.get("token") == token and status.get("contract_token") == token,
        "Public contract revision/hash or approval need changed after display",
    )
    return approved_criterion(plan.get("acceptance_criteria"), command)


def public_run_directory(project, value):
    """Accept only an existing, canonical run beneath this attempt's own runs root."""
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise Blocked("Public CLI did not name an absolute run directory")
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise Blocked("Public CLI named an unavailable run directory") from error
    if path != resolved or not resolved.is_dir() or resolved.parent != project.resolve() / ".autocode/runs":
        raise Blocked("Public CLI run directory is not a real run contained in this attempt")
    return resolved


class Attempt:
    def __init__(self, args):
        args.out.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="attempt-", dir=args.out.resolve()))
        self.project = self.root / "project"
        self.project.mkdir()
        self.logs = self.root / "commands"
        self.logs.mkdir()
        self.args, self.run_dir, self.deadline = args, None, None
        self.calls, self.drive_calls = [], 0
        self.profile = profiles.resolve(args.profile)
        self.flags = [*profiles.flags(self.profile), *CAPS]
        self.test_argv = [sys.executable, "-m", "unittest", "discover", "-v", "-s", ".", "-p", "test_*.py"]
        self.test_command = shlex.join(self.test_argv)
        # Pairwise proof needs both the new fail-to-pass test and the preserved pass-to-pass guard.
        self.regression_command = self.test_command
        self.env = {**os.environ, "AUTOCODE_HOME": str(self.root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.summary = {
            "version": 1,
            "verdict": "ERROR",
            "evidence": str(self.root),
            "profile": args.profile,
            "profile_detail": self.profile,
            "authorized_model_spend": True,
            "model_launch_attempted": False,
            "limits": {
                "driving_seconds": args.timeout_minutes * 60,
                "max_cli_calls": args.max_steps,
                "runtime_flags": list(CAPS),
            },
            "scope": "Local HTML exported-reference gate; no live Figma access",
            "allowed_source_changes": list(ALLOWED_CHANGES),
            "trusted_test_commands": {"suite": self.test_command, "regression": self.regression_command},
            "independent_visual_review": "NOT_PERFORMED",
            "capture_provenance": "project-owned fixture, not authenticated browser provenance",
            "usage": {"cost_usd": {"reported": None, "estimated": None, "complete": False}},
            "usage_scope": "Last public status observation; later stages may only appear in retained activity.",
            "cost_note": "Unknown cost is not zero; time limits are not a dollar cap.",
        }

    def call(self, label, command, *, cwd=None, env=None, drive=False, timeout=60):
        if drive:
            if self.drive_calls >= self.args.max_steps:
                raise Blocked("CLI call allowance exhausted")
            timeout = self.deadline - time.monotonic()
            if timeout <= 0:
                raise Blocked("CLI-driving deadline exhausted")
            self.drive_calls += 1
        number = len(self.calls) + 1
        record = {
            "label": label,
            "command": list(map(str, command)),
            "cwd": str(cwd or self.project),
            "timeout_seconds": timeout,
            "stdout": str(self.logs / f"{number:03d}.stdout"),
            "stderr": str(self.logs / f"{number:03d}.stderr"),
        }
        self.calls.append(record)
        started = time.monotonic()
        try:
            proc = processes.run_cli(
                record["command"], env={**self.env, **(env or {})}, cwd=cwd or self.project, timeout=timeout
            )
            record["exit_code"] = proc.returncode
            Path(record["stdout"]).write_text(proc.stdout, encoding="utf-8")
            Path(record["stderr"]).write_text(proc.stderr, encoding="utf-8")
            record.update(stdout_sha256=sha256(record["stdout"]), stderr_sha256=sha256(record["stderr"]))
            return proc
        except processes.CallTimeout as error:
            record.update(timed_out=True, cleanup_errors=error.cleanup_errors)
            raise Blocked(f"{label} exceeded its deadline; cleanup errors: {error.cleanup_errors}") from error
        finally:
            record["seconds"] = round(time.monotonic() - started, 3)
            with (self.root / "commands.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")

    def git(self, root, *arguments):
        proc = self.call("git", ["git", *arguments], cwd=root)
        require(proc.returncode == 0, "Git fixture/identity command failed; inspect command logs")
        return proc.stdout.strip()

    def commit_fixture(self, root):
        self.git(root, "init", "-q")
        self.git(root, "add", ".")
        self.git(
            root,
            "-c",
            "user.name=Visual Qualification",
            "-c",
            "user.email=visual@example.test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "visual qualification seed",
        )

    def runtime_files(self):
        names = self.git(ROOT, "ls-files", "--", "tools", "pyproject.toml").splitlines()
        names += [
            "tests/visual_check_live.py",
            "tests/visual_check_fixtures.py",
            "scenarios/harness/profiles.py",
            "scenarios/harness/processes.py",
            "tools/autocode_visual_check.py",
            "tools/autocode_visual_policy.py",
            "tools/autocode_visual_diff.py",
        ]
        return {name: sha256(ROOT / name) for name in sorted(set(names)) if (ROOT / name).is_file()}

    def setup(self):
        if any(
            name in self.env
            for name in (
                "OPENAI_API_KEY",
                "CODEX_API_KEY",
                "OPENAI_BASE_URL",
                "SCENARIO_FAKE_CONFIG",
                "AUTOCODE_FIXTURE_MODE",
            )
        ):
            raise Blocked("API billing overrides or scripted-provider environment present; no fallback allowed")
        provider = self.call("OpenCode version (no model)", ["opencode", "--version"])
        if provider.returncode or not re.fullmatch(r"1\.\d+\.\d+(?:[-+].*)?", provider.stdout.strip()):
            raise Blocked("Existing profile requires a ready OpenCode 1.x runtime")
        self.runtime = self.runtime_files()
        save(
            self.root / "runtime.json",
            {
                "root": str(ROOT),
                "python": sys.executable,
                "opencode": {"executable": shutil.which("opencode"), "version": provider.stdout.strip()},
                "commit": self.git(ROOT, "rev-parse", "HEAD"),
                "status": self.git(ROOT, "status", "--short"),
                "files": self.runtime,
                "packages": {name: importlib.metadata.version(name) for name in ("Pillow", "playwright", "psutil")},
            },
        )
        self.summary["runtime_identity"] = {
            "path": str(self.root / "runtime.json"),
            "sha256": sha256(self.root / "runtime.json"),
        }
        design = self.project / "design"
        design.mkdir()
        (self.project / ".gitignore").write_text(".autocode/\n__pycache__/\n", encoding="utf-8")
        (self.project / "app.html").write_text(BROWSER_HTML, encoding="utf-8")
        # Assertions augment the shared browser fixture, without claiming an authenticated collector.
        capture = BROWSER_CAPTURE_SOURCE.replace(
            'page.evaluate("document.fonts.ready")',
            'assert page.url == (root / "app.html").as_uri()\n'
            '                assert page.viewport_size == {"width": viewport["width"], "height": viewport["height"]}\n'
            '                assert page.locator("#count").inner_text() == "0"\n'
            '                page.evaluate("document.fonts.ready")',
        )
        capture = capture.replace("    try:\n", '    print("Chromium version: " + browser.version)\n    try:\n', 1)
        (self.project / "capture.py").write_text(capture, encoding="utf-8")
        (self.project / "test_behavior.py").write_text(FUNCTIONAL_TEST_SOURCE, encoding="utf-8")
        (design / "context.json").write_text(
            json.dumps(
                {"source": "local HTML fixture, not Figma", "state": "ready", "reference": "original BROWSER_HTML"}
            ),
            encoding="utf-8",
        )
        case = {
            "id": "desktop",
            "file_key": "LocalFixture",
            "node_id": "1:2",
            "state": "ready",
            "route": "/",
            "implementation_paths": ["app.html", "capture.py", "test_behavior.py"],
            "export_scale": 1,
            "viewport": {"width": 240, "height": 160, "device_scale_factor": 1},
            "artifacts": {
                "screenshot": {"path": "desktop.png", "sha256": "0" * 64},
                "design_context": {"path": "context.json", "sha256": sha256(design / "context.json")},
            },
        }
        manifest = {"version": 1, "files": [{"key": "LocalFixture", "nodes": ["1:2"]}], "cases": [case]}
        manifest_path = design / "manifest.json"
        save(manifest_path, manifest)
        baseline = self.root / "baseline-capture"
        baseline.mkdir()
        proc = self.call(
            "reference Chromium capture",
            [sys.executable, "capture.py"],
            timeout=45,
            env={"AUTOCODE_VISUAL_OUTPUT": str(baseline), "AUTOCODE_VISUAL_MANIFEST": str(manifest_path)},
        )
        require(
            proc.returncode == 0 and "Browser click assertion passed" in proc.stdout, "Reference browser capture failed"
        )
        shutil.copyfile(baseline / "desktop.png", design / "desktop.png")
        case["artifacts"]["screenshot"]["sha256"] = sha256(design / "desktop.png")
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        save(
            self.project / "visual-policy.json",
            {
                "version": 1,
                "manifest": {"path": "design/manifest.json", "sha256": sha256(manifest_path)},
                "capture_command": [sys.executable, "capture.py"],
                "timeout_seconds": 30,
                "cases": [{"id": "desktop", "channel_tolerance": 0, "max_changed_ratio": 0.0, "regions": []}],
            },
        )
        self.policy_hash = sha256(self.project / "visual-policy.json")
        self.check = [
            sys.executable,
            str(ROOT / "tools/autocode_visual_check.py"),
            "--workspace",
            ".",
            "--policy",
            "visual-policy.json",
            "--policy-sha256",
            self.policy_hash,
        ]
        self.command = shlex.join(self.check)
        frozen = self.root / "frozen"
        shutil.copytree(self.project, frozen)
        self.protected = {
            name: entry for name, entry in source_files(self.project).items() if name not in ALLOWED_CHANGES
        }
        save(self.root / "protected-inputs.json", self.protected)
        broken = BROWSER_HTML.replace("left: 0px", "left: 11px").replace("#extra {", "#extra { visibility: hidden;")
        (self.project / "app.html").write_text(broken, encoding="utf-8")
        self.commit_fixture(self.project)
        self.seed_commit = self.git(self.project, "rev-parse", "HEAD")
        self.seed = source_files(self.project)
        save(self.root / "seed-source.json", self.seed)
        self.summary["seed_source"] = {
            "commit": self.seed_commit,
            "inventory": str(self.root / "seed-source.json"),
            "sha256": sha256(self.root / "seed-source.json"),
        }
        self.summary["protected_inputs"] = {
            "inventory": str(self.root / "protected-inputs.json"),
            "sha256": sha256(self.root / "protected-inputs.json"),
            "original_files": str(frozen),
            "policy_sha256": self.policy_hash,
        }
        self.summary["approved_command"] = self.command
        require(
            "test_visual_acceptance.py" not in self.seed, "The visual regression must be authored by the live Builder"
        )
        functional = self.call("initial functional suite", self.test_argv, timeout=60)
        require(
            functional.returncode == 0 and "Ran 1 test" in functional.stderr,
            "The preserved functional suite must run and pass before any model call",
        )
        self.summary["initial_functional_suite"] = dict(self.calls[-1])
        self.protected_unchanged()
        self.summary["initial_control"] = self.compare(self.project, "initial", passing=False)

    def protected_unchanged(self, *, final=False):
        current = source_files(self.project)
        require(
            {name: entry for name, entry in current.items() if name not in ALLOWED_CHANGES} == self.protected,
            "A protected input changed or source outside app.html/test_visual_acceptance.py was added/removed",
        )
        require("app.html" in current, "app.html is missing")
        if final:
            require(
                "test_visual_acceptance.py" not in self.seed
                and "test_visual_acceptance.py" in current
                and (self.project / "test_visual_acceptance.py").stat().st_size > 0,
                "The Builder must add a nonempty test_visual_acceptance.py regression",
            )
        return current

    def compare(self, project, label, *, passing):
        output = f".autocode/visual-checks/{label}"
        proc = self.call(label + " visual check", [*self.check, "--output", output], cwd=project, timeout=60)
        report = json.loads(proc.stdout)
        require(
            proc.returncode == (0 if passing else 1) and report.get("status") == ("PASS" if passing else "FAIL"),
            f"{label} did not have the required visual verdict",
        )
        require(
            report.get("required_cases") == ["desktop"] and len(report.get("cases", [])) == 1,
            "Visual case inventory changed",
        )
        changed = report["cases"][0].get("changed_pixels")
        require(
            type(changed) is int and (changed == 0 if passing else changed > 0), "Missing/incorrect pixel measurement"
        )
        require(report.get("policy_sha256") == self.policy_hash, "Visual report did not use the approved policy")
        require(
            isinstance(report.get("source_revision"), str)
            and report["source_revision"]
            and report["source_revision"] == report.get("source_revision_after"),
            "Capture source identity missing or changed",
        )
        destination = project / output
        require(
            json.loads((destination / "report.json").read_text()) == report, "Saved visual report differs from stdout"
        )
        log = destination / report["capture_log"]["path"]
        require(sha256(log) == report["capture_log"]["sha256"], "Capture log hash changed")
        require("Browser click assertion passed" in log.read_text(), "Capture lacks its functional click assertion")
        return {
            "report": str(destination / "report.json"),
            "sha256": sha256(destination / "report.json"),
            "output": str(destination),
            "changed_pixels": changed,
            "source_revision": report["source_revision"],
        }

    def cli(self, label, *arguments, advancing=False, start=False):
        if (start and self.run_dir is not None) or (not start and self.run_dir is None):
            raise Blocked("Start must discover one run before any subsequent public CLI command")
        self.protected_unchanged()
        if self.summary.get("last_observed_view_observation"):
            self.summary["last_observed_view_observation"]["may_be_stale"] = True
        command = [sys.executable, str(ROOT / "tools/autocode.py"), *arguments, "--workspace", str(self.project)]
        if self.run_dir:
            command += ["--run-dir", str(self.run_dir)]
        if advancing:
            command += ["--no-chat", *self.flags]
            self.summary["model_launch_attempted"] = True
        if start:
            command += [
                "--in-place",
                "--workflow",
                "build",
                "--test-command",
                self.test_command,
                "--regression-command",
                self.regression_command,
            ]
        proc = self.call(label, command, drive=True)
        if start:
            paths = re.findall(r"^Run: ([^\r\n]+)\r?$", proc.stdout, re.MULTILINE)
            if len(paths) != 1:
                raise Blocked(f"Public start must report exactly one Run: directory; found {len(paths)}")
            # Retain valid run evidence even when the start subsequently reports an error.
            self.run_dir = public_run_directory(self.project, paths[0])
            self.summary["run_dir"] = str(self.run_dir)
        rejected = any(
            line.startswith(("Input rejected:", "autocode:"))
            for text in (proc.stdout, proc.stderr)
            for line in text.splitlines()
        )
        if proc.returncode not in ((0, 2) if advancing else (0,)) or proc.stderr.startswith("usage:") or rejected:
            raise Blocked(f"Public CLI rejected {label} (exit {proc.returncode}); inspect command logs")
        self.protected_unchanged()
        return proc

    def status(self):
        data = json.loads(self.cli("status", "--status").stdout)
        run_dir = public_run_directory(self.project, data["run_dir"])
        require(run_dir == self.run_dir, "Status changed the run identity reported by start")
        self.summary.update(
            run_dir=str(run_dir),
            last_observed_view=data["view"],
            usage=data["view"].get("usage", self.summary["usage"]),
            last_observed_view_observation={
                "log": self.calls[-1]["stdout"],
                "sha256": self.calls[-1]["stdout_sha256"],
                "command_index": len(self.calls),
                "observed_at_unix": time.time(),
                "may_be_stale": False,
            },
        )
        return data

    def drive(self):
        self.deadline = time.monotonic() + self.args.timeout_minutes * 60
        brief = (
            "Repair the supplied local exported-reference appearance in app.html and add its regression test. This is "
            "a local UI bug fix, "
            "not live Figma access. The sidebar is offset and the small More options control is hidden. Restore "
            "their reference appearance while preserving the Run button click behavior. Modify app.html and add "
            "exactly test_visual_acceptance.py. Do not commit or change/add any other source, test_behavior.py, "
            "capture.py, reference, context, manifest or policy. The supplied test_behavior.py is a frozen "
            "functional click test which already passes despite the visual defects. All dependencies and capture "
            "infrastructure are supplied; no installs or network assets are needed. "
            "Use one milestone. Include this EXACT executable command as a non-human acceptance criterion's "
            "verification_method, unwrapped and without prose, shell substitutions or alternate commands: "
            + "\n"
            + self.command
            + "\nThe command freshly renders Chromium, asserts the click works, and checks exact "
            "pixels. Author test_visual_acceptance.py as a real unittest test that executes this EXACT pinned "
            "command from the current workspace and asserts exit code 0 AND JSON status PASS. The test must "
            "run and fail on the original defective HTML, then pass on the repair; never catch and suppress "
            "its failure, skip it, depend on absolute candidate source paths, or regenerate references. "
            "Use the command's fresh default output, not a reused output directory. The runner receives the "
            "trusted full-suite command "
            + self.test_command
            + " and targeted regression command "
            + self.regression_command
            + " for independent fail-before/pass-after proof. Run them successfully; retain the "
            "ordinary workspace visual-check artifacts. Do not update "
            "the baseline or weaken the command. No human visual review or additional product decisions are required."
        )
        (self.root / "brief.txt").write_text(brief, encoding="utf-8")
        self.cli("start", brief, advancing=True, start=True)
        previous = None
        while True:
            data = self.status()
            view = data["view"]
            if view.get("done") is True:
                require(self.summary.get("approval"), "Run completed without a recorded exact-command approval")
                require(data.get("completion_current") is True, "Completion is not current for this source")
                self.summary["final_view"] = view
                self.summary["final_view_observation"] = dict(self.summary["last_observed_view_observation"])
                return view
            need = view.get("needs") or {}
            signature = tuple(view.get(key) for key in ("status", "phase", "next_stage", "iteration"))
            if need.get("kind") == "approve_plan":
                shown = self.cli("show-goal", "--show-goal").stdout
                display_log = self.calls[-1]["stdout"]
                token = need["token"]
                approval_token(shown, token)
                current = self.status()
                criterion = approved_plan(current, shown, self.command, token)
                receipt = {
                    "token": token,
                    "criterion": criterion,
                    "command": self.command,
                    "display_log": display_log,
                    "display_sha256": sha256(display_log),
                    "structured_status_log": self.calls[-1]["stdout"],
                    "structured_status_sha256": self.calls[-1]["stdout_sha256"],
                    "displayed_plan": current["view"]["displayed_plan"],
                }
                self.cli("approve-plan", "--approve-goal", token)
                receipt["approval_log"] = self.calls[-1]["stdout"]
                receipt["approval_sha256"] = sha256(receipt["approval_log"])
                save(self.root / f"approval-{len(self.calls):03d}.json", receipt)
                self.summary["approval"] = receipt
                previous = None
            elif need.get("kind") == "continue":
                if signature == previous:
                    raise Blocked("Public status made no progress after advance")
                previous = signature
                self.cli("advance", advancing=True)
            else:
                raise Blocked(
                    f"Unsupported gate: {need.get('kind')}; status={view.get('status')}; {view.get('stop_reason')}"
                )

    def model_receipts(self):
        activity = self.run_dir / "activity.jsonl"
        rows = [json.loads(line) for line in activity.read_text().splitlines() if line.strip()]
        finished = [row for row in rows if row.get("event") == "stage_finished" and row.get("model")]
        require(
            all(row["model"] in set(self.profile["models"].values()) for row in finished),
            "Unexpected model substitution",
        )
        receipts = {}
        for stage, slug, role in (("terra", "builder", "builder"), ("sol", "validator", "validator")):
            require(
                all(row["model"] == self.profile["models"][role] for row in finished if row.get("stage") == stage),
                f"Unexpected {role} model substitution",
            )
            records = [
                row
                for row in finished
                if row.get("stage") == stage
                and row.get("exit_code") == 0
                and not row.get("timed_out")
                and not row.get("rejected")
                and row.get("engine") == "opencode"
                and row.get("model") == self.profile["models"][role]
            ]
            require(records, f"No successful real {role} model receipt")
            artifacts = []
            for path in sorted((self.run_dir / "iterations").glob(f"*/{slug}-*.jsonl")):
                if not re.fullmatch(slug + r"-\d+\.jsonl", path.name):
                    continue
                events = [json.loads(line) for line in path.read_text().splitlines() if line.strip().startswith("{")]
                report = path.with_suffix(".json")
                if (
                    any(row.get("type") == "step_finish" for row in events)
                    and any(row.get("type") == "text" and (row.get("part") or {}).get("text") for row in events)
                    and report.is_file()
                    and isinstance(json.loads(report.read_text()), dict)
                ):
                    artifacts.append(
                        {
                            "events": str(path),
                            "events_sha256": sha256(path),
                            "report": str(report),
                            "report_sha256": sha256(report),
                        }
                    )
            require(artifacts, f"No retained real {role} transport and report artifacts")
            receipts[role] = {"activity": records, "artifacts": artifacts}
        self.summary.update(
            stage_model_receipts=receipts,
            stage_models=finished,
            activity={"path": str(activity), "sha256": sha256(activity)},
        )

    def finish(self, view):
        current = self.protected_unchanged(final=True)
        self.model_receipts()
        evidence = view.get("evidence") or {}
        proof = evidence.get("regression_proof") or {}
        require(
            proof.get("verdict") == "PASS" and bool(proof.get("fail_to_pass")),
            "No public runner-owned fail-before/pass-after regression proof",
        )
        require(
            (proof.get("commands") or {}).get("regression") == self.regression_command
            and (proof.get("commands") or {}).get("suite") == self.test_command,
            "Regression proof did not use the trusted suite and targeted test commands",
        )
        self.summary["regression_proof"] = proof
        replay = evidence.get("check_replay") or {}
        require(replay.get("verdict") == "PASS", "No passing runner-owned clean replay")
        checks = [
            row
            for row in replay.get("checks", [])
            if row.get("command") == self.command and row.get("exit_code") == 0 and not row.get("timed_out")
        ]
        require(checks, "Runner replay did not execute the exact approved command successfully")
        replay_output = Path(checks[-1]["output"]).resolve()
        require(replay_output.is_relative_to(self.run_dir), "Replay output escaped the retained run")
        replay_report = json.loads(replay_output.read_text())
        require(
            replay_report.get("status") == "PASS" and replay_report.get("policy_sha256") == self.policy_hash,
            "Replay JSON does not establish the approved pixel policy",
        )
        self.summary["replay"] = {"view": replay, "output": str(replay_output), "output_sha256": sha256(replay_output)}
        require(current["app.html"] != self.seed["app.html"], "Builder made no application change")
        final = self.compare(self.project, "final", passing=True)
        require(
            replay.get("source_revision") == final["source_revision"],
            "Final candidate differs from runner-validated source",
        )
        require(proof.get("source_revision") == final["source_revision"], "Regression proof is for a different source")
        self.summary["final_control"] = final
        self.summary["source_diff"] = self.git(self.project, "diff", self.seed_commit, "--", "app.html")
        self.summary["source_commits"] = {
            "seed": self.seed_commit,
            "final": self.git(self.project, "rev-parse", "HEAD"),
        }
        snapshot = self.root / "accepted-source"
        shutil.copytree(self.project, snapshot, ignore=shutil.ignore_patterns(".git", ".autocode"))
        save(self.root / "accepted-source.json", current)
        self.summary["accepted_source"] = {
            "path": str(snapshot),
            "inventory": str(self.root / "accepted-source.json"),
            "sha256": sha256(self.root / "accepted-source.json"),
        }
        self.summary["regression_test"] = {
            "path": str(self.project / "test_visual_acceptance.py"),
            "snapshot": str(snapshot / "test_visual_acceptance.py"),
            "sha256": current["test_visual_acceptance.py"]["sha256"],
        }
        self.summary["negative_controls"] = {}
        for name, css in (
            ("sidebar-offset", "aside { transform: translateX(11px) !important; }"),
            ("missing-control", "#extra { visibility: hidden !important; }"),
        ):
            copy = self.root / ("negative-" + name)
            shutil.copytree(snapshot, copy)
            app = copy / "app.html"
            app.write_text(app.read_text() + "\n<style>" + css + "</style>\n", encoding="utf-8")
            self.commit_fixture(copy)
            self.summary["negative_controls"][name] = self.compare(copy, name, passing=False)
        require(self.protected_unchanged() == current, "Negative controls changed the accepted workspace")
        require(source_files(snapshot) == current, "Retained accepted snapshot changed")
        require(self.runtime_files() == self.runtime, "Runtime source changed during qualification")
        self.summary["verdict"] = "PASS"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--i-authorize-live-model-spend", action="store_true")
    parser.add_argument("--profile", choices=("codex-only",), default="codex-only")
    parser.add_argument("--out", type=Path, default=ROOT / ".scenario-runs/visual-check-live")
    parser.add_argument("--timeout-minutes", type=int, choices=range(1, 16), default=15)
    parser.add_argument("--max-steps", type=int, choices=range(1, 25), default=24)
    args = parser.parse_args(argv)
    if not args.i_authorize_live_model_spend:
        print(json.dumps({"verdict": "BLOCKED", "reason": "Requires --i-authorize-live-model-spend; nothing launched"}))
        return 2
    attempt = None
    started = time.monotonic()
    try:
        attempt = Attempt(args)
        attempt.setup()
        attempt.finish(attempt.drive())
    except (Blocked, processes.SupervisionUnavailable) as error:
        result = {"verdict": "BLOCKED", "reason": str(error)}
    except ProofFailure as error:
        result = {"verdict": "FAIL", "reason": str(error)}
    except (Exception, KeyboardInterrupt) as error:
        result = {"verdict": "ERROR", "reason": f"{type(error).__name__}: {error}"}
    else:
        result = {"verdict": "PASS"}
    if attempt:
        if attempt.run_dir and (attempt.run_dir / "activity.jsonl").is_file():
            try:
                activity = attempt.run_dir / "activity.jsonl"
                rows = [json.loads(line) for line in activity.read_text().splitlines() if line.strip()]
                attempt.summary["stage_model_activity"] = [
                    row for row in rows if row.get("event") in ("stage_started", "stage_finished")
                ]
                attempt.summary["activity"] = {"path": str(activity), "sha256": sha256(activity)}
            except (OSError, ValueError, TypeError, AttributeError) as error:
                result = {"verdict": "ERROR", "reason": f"Cannot retain model activity receipts: {error}"}
        attempt.summary.update(
            result,
            elapsed_seconds=round(time.monotonic() - started, 3),
            cli_driving_calls=attempt.drive_calls,
            command_count=len(attempt.calls),
        )
        save(attempt.root / "summary.json", attempt.summary)
        result["summary"] = str(attempt.root / "summary.json")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == "PASS" else 2 if result["verdict"] == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
