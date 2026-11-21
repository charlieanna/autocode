"""Prepare an offline quota-stopped job with model-effort drift for the real editor."""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import autocode_job_failure as job_failure  # noqa: E402
import autocode_job_source as job_source  # noqa: E402
import autocode_registry as registry  # noqa: E402
import autocode_support as support  # noqa: E402

from tests.status_retry_host_fixture import relocate  # noqa: E402
from tests.test_job_route import JobModelRouteTests  # noqa: E402


def prepare(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    JobModelRouteTests.setUpClass()
    case = JobModelRouteTests("runTest")
    case.setUp()
    try:
        run = case.refused("quota")
        case.stopped(run)
        original = json.loads((run.run_dir / "state.json").read_text())
        original["settings"]["roles"]["architect"]["reasoning_effort"] = "high"
        original["job_failure"]["route"].pop("candidates", None)
        project = root / "project"
        shutil.copytree(case.workspace, project, symlinks=True)
        copied_run = project / run.run_dir.relative_to(case.workspace.resolve())
        state = relocate(copy.deepcopy(original), run.run_dir, copied_run, case.workspace.resolve(), project)
        state["workspace"] = str(project)
        state["run_dir"] = str(copied_run)
        state["task"] = "Offline synthetic Kilo effort recovery"
        state["task_id"] = "kilo-effort-host"
        model = "zhipuai-coding-plan/glm-5.3"
        kilo = project / ".autocode" / "bin" / "kilo"
        kilo.write_text(
            "#!/usr/bin/env python3\n"
            "import os,sys\nfrom pathlib import Path\n"
            "with Path(os.environ['KILO_AUDIT']).open('a') as stream: stream.write(' '.join(sys.argv[1:])+'\\n')\n"
            "if sys.argv[1:] == ['--version']: print('7.8.27'); raise SystemExit(0)\n"
            f"if sys.argv[1:] == ['models']: print({model!r}); raise SystemExit(0)\n"
            "if sys.argv[1:] == ['auth','list']: print('connected'); raise SystemExit(0)\n"
            "raise SystemExit(7)\n"
        )
        kilo.chmod(0o755)
        state["settings"].update(engine="opencode", provider="kilocode")
        state["settings"]["roles"]["builder"]["model"] = "openai/gpt-6-sol"
        state["settings"]["roles"]["tester"]["model"] = model
        state["settings"]["roles"]["completion"]["model"] = "anthropic/claude-sonnet-4-6"
        route = state["settings"]["roles"]["architect"]
        route.update(model=model, reasoning_effort="medium")
        state["job_failure"]["configuration"] = job_failure.configuration(state)
        state["job_failure"]["route"].update(stopped_model=model, current_model=model, engine="opencode")
        state["job_failure"]["route"].pop("candidates", None)
        route["reasoning_effort"] = "high"
        state["job_failure"]["source_identity"] = support.digest(job_source.identity(project))
        (copied_run / "state.json").write_text(json.dumps(state))
        workspace_id = registry._identity("workspace", project)
        run_id = registry._identity("run", copied_run)
        store = root / "registry"
        store.mkdir()
        (store / "registry.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "workspaces": {workspace_id: {"id": workspace_id, "workspace": str(project)}},
                    "runs": {
                        run_id: {
                            "id": run_id,
                            "workspace_id": workspace_id,
                            "workspace": str(project),
                            "run_dir": str(copied_run),
                            "task_id": "kilo-effort-host",
                        }
                    },
                }
            )
        )
        return {
            "workspace": str(project),
            "run_dir": str(copied_run),
            "registry": str(store),
            "calls": str(project / ".autocode/calls"),
            "audit": str(root / "kilo-audit"),
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
