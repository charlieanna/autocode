"""Create a genuine offline held Tester report through the public CLI.

An isolated old runtime deliberately rejects the Tester's valid output. The
candidate core, not the fixture, later revalidates the retained attempt. No
saved run state or runner receipt is synthesized or edited by this fixture.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tests.retained_report_fixture_support import scripted_provider  # noqa: E402


def replace_once(path: Path, before: str, after: str) -> None:
    text = path.read_text()
    if text.count(before) != 1:
        raise ValueError(f"The offline fixture's expected source changed: {path.name}")
    path.write_text(text.replace(before, after))


def invoke(root: Path, label: str, command, *, env, cwd, timeout=300):
    result = subprocess.run(command, env=env, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    (root / f"{label}.stdout").write_text(result.stdout)
    (root / f"{label}.stderr").write_text(result.stderr)
    return result


def main(argv):
    if len(argv) != 1:
        raise SystemExit("usage: retained_report_vscode_fixture.py ROOT")
    root = Path(argv[0]).resolve(strict=True)
    old = root / "old-runtime"
    old.mkdir()
    for name in ("tools", "scenarios"):
        shutil.copytree(REPO / name, old / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (old / "tests/fixtures").mkdir(parents=True)
    shutil.copytree(REPO / "tests/fixtures/python-semantic-host", old / "tests/fixtures/python-semantic-host")
    fixture = old / "tests/python_semantic_vscode_fixture.py"
    shutil.copy2(REPO / "tests/python_semantic_vscode_fixture.py", fixture)
    fake = old / "scenarios/harness/fake_codex.py"
    fake.write_text(scripted_provider(fake.read_text()))
    loader = old / "tools/autocode.py"
    signature = "def load_stage_report(record, workspace=None, evidence_record=None, state: RunState | None = None):\n"
    replace_once(
        loader,
        signature,
        signature
        + '    if str(record.get("stage", "")).startswith("tester"):\n'
        + '        raise RuntimeError("Offline old validator rejects the saved Tester report")\n',
    )
    replace_once(
        old / "tools/autocode_investigation_policy.py",
        "    return max(MAX_CALLS, len(milestones) if isinstance(milestones, list) else 0)\n",
        "    return 0  # Offline fixture: no Investigator calls.\n",
    )
    setup = "    flags, env = driver.fake_setup(scenario, root, reference)\n"
    replace_once(
        fixture,
        setup,
        setup
        + '    (root / "provider-manifest.json").write_text(json.dumps({"config_sha256": '
        + 'hashlib.sha256((root / "fake-config.json").read_bytes()).hexdigest()}))\n',
    )
    replace_once(
        fixture,
        'autocode=[sys.executable, str(REPO / "tools/autocode.py")],',
        'autocode=[sys.executable, str(REPO / "cli_guard.py")],',
    )
    replace_once(
        fixture, '[*flags, "--max-seconds", "1200"],', '[*flags, "--max-seconds", "1200", "--max-cost-usd", "5"],'
    )
    guard = old / "cli_guard.py"
    guard.write_text(
        "import os, shutil, sys\nfrom pathlib import Path\n"
        f"repo = Path({str(REPO)!r})\nsys.path.insert(0, str(repo))\n"
        "from tests.retained_report_fixture_support import verify\n"
        "root = Path(os.environ['AUTOCODE_REPORT_FIXTURE_ROOT']).resolve(strict=True)\n"
        "verify(root, repo, os.environ)\n"
        "bindir = root / 'bin'\n"
        "os.environ['PATH'] = os.pathsep.join((str(bindir), str(Path(sys.executable).parent), '/usr/bin', '/bin'))\n"
        "if shutil.which('codex') != str(bindir / 'codex'): raise SystemExit('Offline provider selection changed')\n"
        f"os.execv(sys.executable, [sys.executable, {str(old / 'tools/autocode.py')!r}, *sys.argv[1:]])\n"
    )
    trace = root / "provider-stages.jsonl"
    trace.write_text("")
    env = dict(os.environ)
    env.update(
        AUTOCODE_REPORT_FIXTURE_ROOT=str(root),
        AUTOCODE_REPORT_FAKE_TRACE=str(trace),
        AUTOCODE_SOURCE_HOST="0",
    )
    for key in ("HOME", "CODEX_HOME"):
        if (key in env) != (key in os.environ) or env.get(key) != os.environ.get(key):
            raise ValueError("The fixture changed the operator's home")
    started = invoke(root, "fixture-plan", [sys.executable, str(fixture), str(root)], env=env, cwd=root, timeout=300)
    if started.returncode:
        raise RuntimeError(f"The offline plan fixture stopped: {started.stderr}")
    data = json.loads(started.stdout)
    env.update(data["env"])
    review = json.loads(Path(data["plan_review"]).read_text())
    where = ["--run-dir", data["run_dir"], "--workspace", data["workspace"]]
    old_cli = [sys.executable, str(guard), *where]

    def status(cli, label):
        read = invoke(root, label, [*cli, "--status", "--json"], env=env, cwd=root, timeout=60)
        if read.returncode:
            raise RuntimeError(f"The offline status read failed: {read.stderr}")
        return json.loads(read.stdout)["view"]

    before = status(old_cli, "plan-before")
    plan = before.get("displayed_plan") or {}
    encoded = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if (
        (before.get("needs") or {}).get("kind") != "approve_plan"
        or (before.get("needs") or {}).get("token") != review.get("token")
        or plan != review.get("displayed_plan")
        or hashlib.sha256(encoded).hexdigest() != review.get("plan_sha256")
    ):
        raise ValueError("The public plan differs from the agent-reviewed fixture plan")
    approved = invoke(root, "approval", [*old_cli, "--approve-goal", review["token"], "--no-chat"], env=env, cwd=root)
    if approved.returncode:
        raise RuntimeError(f"The exact plan approval failed: {approved.stderr}")
    ready = status(old_cli, "approved-status")
    if ready.get("status") != "RUNNING" or (ready.get("needs") or {}).get("kind") != "continue":
        raise ValueError("The approved fixture did not offer its expected continuation")
    advance = invoke(root, "old-advance", [*old_cli, "--no-chat"], env=env, cwd=root, timeout=600)
    held = status(old_cli, "old-held-status")
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    testers = [row for row in rows if row.get("stage") == "tester"]
    if (
        advance.returncode != 2
        or held.get("status") != "PAUSED_REPEATED_FAILURE"
        or held.get("next_command") is not None
        or [row.get("repair") for row in testers] != [False, True, True]
    ):
        raise ValueError(f"The old validator did not produce the expected held attempt: {held.get('status')}")
    # The current core's read authenticates its real source, pins, completed
    # attempt and incident; it cannot mutate the run or launch a provider.
    env["AUTOCODE_HOST_READS"] = str(root / "reads")
    env["AUTOCODE_HOST_HOLD"] = str(root / "no-hold")
    current_cli = [sys.executable, str(REPO / "tests/retained_report_vscode_cli.py"), *where]
    current = status(current_cli, "candidate-held-status")
    if (
        current.get("status") != "PAUSED_REPEATED_FAILURE"
        or (current.get("needs") or {}).get("automatic_reconciliation") is not True
        or not current.get("next_command")
        or [json.loads(line) for line in trace.read_text().splitlines()] != rows
    ):
        raise ValueError("The current core did not offer the exact model-free recheck")
    output_env = {
        key: env[key]
        for key in (
            "AUTOCODE_HOME",
            "XDG_CONFIG_HOME",
            "PATH",
            "SCENARIO_FAKE_CONFIG",
            "TMPDIR",
            "PYTHONDONTWRITEBYTECODE",
            "AUTOCODE_SOURCE_HOST",
            "AUTOCODE_REPORT_FAKE_TRACE",
        )
    }
    print(
        json.dumps(
            {
                "workspace": data["workspace"],
                "run_dir": data["run_dir"],
                "registry": data["registry"],
                "env": output_env,
                "brief": "Extend labels() to append the exact string added",
                "trace": str(trace),
                "initial_tester_calls": testers,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
