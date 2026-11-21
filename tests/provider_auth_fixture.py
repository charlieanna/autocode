"""An offline run whose Planner launch the provider rejects with HTTP 401 (#3860).

One scripted run, shared by the CLI regression (tests/test_provider_auth_cli.py) and the
VS Code Extension Host suite (vscode-extension/test/host), so both read the same stopped run
rather than two copies of how to make one. No model is called: the scripted Codex provider
answers every stage, and only its first Planner launch reports the rejected login.

As a script it builds the run under ROOT and prints, as JSON, the project, the run directory,
the argument array that runs this checkout's CLI, the environment its reads need (the run's
registry and the empty user configuration it was started with), and the stopped status view.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REJECTED = "SCENARIO_FAKE_LOGIN_REJECTED"
TIMEOUT = "SCENARIO_FAKE_LOGIN_TIMEOUT"


def _harness():
    """Import the scenario harness the way its own runner initializes it."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from scenarios import run  # noqa: F401, I001 - initialize the harness before importing it
    from harness import catalog, driver, project

    return catalog, driver, project


def _reject_once(bindir: Path, provider: Path) -> None:
    # This error belongs to this fixture, not the shared scenario fault catalog. Keep the
    # ordinary fake, timeout behavior and real scenario checks unchanged after the one
    # rejected login.
    (bindir / "codex").write_text(
        "#!/usr/bin/env python3\n"
        "import os\nimport runpy\nfrom pathlib import Path\n"
        f"fixture = runpy.run_path({str(provider)!r})\n"
        "owner = fixture['main'].__globals__\n"
        "ordinary_timeout = owner['auth_timeout_once']\n"
        "def reject_once(stage):\n"
        "    marker = Path(os.environ['SCENARIO_FAKE_CONFIG']).with_name('fake-login-rejected-draft_plan')\n"
        "    if stage != 'draft_plan' or marker.exists():\n"
        "        return ordinary_timeout(stage)\n"
        "    marker.touch()\n"
        "    owner['emit']({'type': 'error', 'sessionID': 'ses_rejected_login',\n"
        "        'error': {'name': 'APIError', 'data': {'statusCode': 401, 'message': 'Invalid API key'}}})\n"
        "    return 1\n"
        "owner['auth_timeout_once'] = reject_once\n"
        "raise SystemExit(fixture['main']())\n"
    )


def stopped_planner_run(root: Path, fault: str = REJECTED, *, inherit_homes: bool = False):
    """Start the scripted run under ``root`` and drive it until it stops at the Planner.

    Returns the driver (its ``run_dir`` set) and the stopped status view.
    """
    catalog, harness, layout = _harness()
    root = Path(root).resolve()
    scenario = catalog.load("bugfix-iso-weeks")
    for name in ("config", "codex-home"):
        (root / name).mkdir(parents=True, exist_ok=True)
    project = layout.materialize(scenario.seed, root / "project")
    flags, env = harness.fake_setup(scenario, root, scenario.reference)
    if inherit_homes:
        env.pop("CODEX_HOME", None)
    else:
        env.update(CODEX_HOME=str(root / "codex-home"))
    if fault == REJECTED:
        _reject_once(root / "bin", harness.FAKE_PROVIDER)
    else:
        env[fault] = "draft_plan:1"
    driver = harness.Driver(
        project, root, flags, env, autocode=harness.default_autocode(), max_steps=40, timeout_seconds=600
    )
    driver.call("start", task=scenario.brief)
    candidates = sorted((project / ".autocode" / "runs").glob("*/state.json"), key=lambda path: path.stat().st_mtime)
    if not candidates:
        last = driver.steps[-1]
        raise harness.DriveError(
            "the first CLI call did not create a run: " + (last["stderr_tail"] or last["stdout_tail"]).strip()[-500:]
        )
    driver.run_dir = candidates[-1].parent
    return driver, driver.until_stopped()


def main(argv: list[str]) -> int:
    inherit_homes = len(argv) == 2 and argv[1] == "--inherit-homes"
    if len(argv) != 1 and not inherit_homes:
        print("usage: python tests/provider_auth_fixture.py ROOT [--inherit-homes]", file=sys.stderr)
        return 2
    driver, view = stopped_planner_run(Path(argv[0]), inherit_homes=inherit_homes)
    _, harness, _ = _harness()
    print(
        json.dumps(
            {
                "workspace": str(driver.project),
                "runDir": str(driver.run_dir),
                "cli": harness.default_autocode(),
                "env": {
                    name: driver.env[name]
                    for name in ("AUTOCODE_HOME", "XDG_CONFIG_HOME", "CODEX_HOME")
                    if name in driver.env and (name != "CODEX_HOME" or not inherit_homes)
                },
                "view": view,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
