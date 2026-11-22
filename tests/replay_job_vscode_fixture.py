"""Start a real offline recorded-job replay for the extension host.

The original and replay both use the scripted provider; no saved run is copied
or edited. The original public route and retry are exported into the tape.
"""

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import autocode_replay_recording as recording  # noqa: E402
from autocode_taskrun import TaskRun  # noqa: E402

from tests.test_job_route import JobModelRouteTests  # noqa: E402


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    JobModelRouteTests.setUpClass()
    case = JobModelRouteTests("runTest")
    case.setUp()
    try:
        original = case.refused("quota")
        case.stopped(original)
        routed = original.assign_model("architect", "gpt-6-luna@medium")
        final = original.retry_job(routed["needs"]["job_retry_token"])
        if final["status"] != "PAUSED_DESIGN_CONFLICT":
            raise ValueError("The original scripted task did not reach its recorded conflict")
        tape = recording.load(recording.export(original.status(inspect_evidence=True)["replay_source"], root / "tape"))
        if [entry["kind"] for entry in tape.document["actions"]] != ["route_job", "retry_job"]:
            raise ValueError("The recorded task did not retain its job actions")
        workspace = root / "project"
        recording.prepare_workspace(tape, workspace)
        owned_bin = workspace / ".autocode" / "bin"
        owned_bin.mkdir(parents=True)
        shutil.copy2(case.workspace / ".autocode" / "bin" / "codex", owned_bin / "codex")
        wrapper = root / "wrapper.py"
        shutil.copy2(case.wrapper, wrapper)
        env = dict(case.env)
        env.update(
            PATH=str(owned_bin) + os.pathsep + os.environ["PATH"],
            AUTOCODE_HOME=str(root / "registry"),
            XDG_CONFIG_HOME=str(root / "config"),
            JOB_CALLS=str(root / "calls"),
        )
        tape = recording.copy_into(tape, workspace / ".autocode" / "replay" / "recording")
        run = TaskRun.start(
            workspace,
            tape.document["launch"]["task"],
            command=(sys.executable, "-B", str(wrapper)),
            options=(
                "--replay-recording",
                str(tape.manifest_path),
                "--replay-checks",
                "fresh",
                "--evidence-provenance",
                "fake",
            ),
            env=env,
            timeout=90,
        )
        view = run.status()
        if view["status"] != "PAUSED_JOB_FAILURE" or (view.get("needs") or {}).get("kind") != "continue":
            raise ValueError("The real replay did not offer its recorded route action")
        return {
            "workspace": str(workspace),
            "run_dir": str(run.run_dir),
            "registry": env["AUTOCODE_HOME"],
            "calls": env["JOB_CALLS"],
            "wrapper": str(wrapper),
        }
    finally:
        case.doCleanups()
        JobModelRouteTests.tearDownClass()


if __name__ == "__main__":
    for key in list(os.environ):
        if key == "GIT_CONFIG_COUNT" or key.startswith(
            ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "OPENAI_", "ANTHROPIC_", "KILO_API_", "CODEX_API_", "ZAI_API_")
        ):
            os.environ.pop(key, None)
    print(json.dumps(prepare(Path(sys.argv[1]).resolve())))
