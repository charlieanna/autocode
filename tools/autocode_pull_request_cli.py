"""`autocode pr`: open a pull request for a finished run's branch, through the task-run interface.

    autocode pr --run-dir RUN [--workspace WS] [--open] [--ready] [--base BRANCH] [--remote NAME] [--json]

Reads the run only through TaskRun (its ``--status --inspect-evidence`` view and evidence report)
and hands both to ``autocode_pull_request.open_pull_request``. Without ``--open`` it checks everything
and prints the pull request it would open; ``--open`` pushes the branch and opens a draft
pull request (``--ready`` for one ready for review). No model is called and no stage runs. The status
view names ``pull_request.next_command`` for a run this applies to, and a client takes it when the
person presses its button.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from . import autocode_github as github
    from . import autocode_pull_request as pull_request
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    import autocode_github as github
    import autocode_pull_request as pull_request
    from autocode_taskrun import TaskRun, TaskRunError


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="autocode pr",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    top.add_argument("--run-dir", required=True, help="the finished run")
    top.add_argument("--workspace", help="the run's workspace (default: the folder that holds its .autocode)")
    top.add_argument("--open", action="store_true", help="push the run's branch and open the pull request")
    top.add_argument("--ready", action="store_true", help="open it ready for review instead of as a draft")
    top.add_argument("--base", help="branch the pull request targets (default: the repository's default branch)")
    top.add_argument("--remote", default=pull_request.REMOTE, help="the remote to push to (default: origin)")
    top.add_argument("--json", action="store_true", help="print the result, or the refusal, as JSON")
    return top


def _workspace(run_dir: Path, given: str | None) -> Path:
    if given:
        return Path(given)
    # RUN is WORKSPACE/.autocode/runs/NAME.
    return run_dir.resolve().parents[2]


def cli(argv=None) -> int:
    args = parser().parse_args(argv)
    run_dir = Path(args.run_dir)
    workspace = _workspace(run_dir, args.workspace)
    try:
        run = TaskRun(workspace, run_dir)
        view = run.status(inspect_evidence=True)
        if not view.get("done"):
            raise pull_request.PullRequestError(
                f"the run has not completed (status {view.get('status')}); "
                "a pull request needs its completion gate to pass"
            )
        report = run.evidence_report(require_current=False)  # open_pull_request checks it (pull_request.checked)
        result = pull_request.open_pull_request(
            view,
            report,
            workspace=workspace,
            run_dir=run_dir,
            remote=args.remote,
            base=args.base,
            ready=args.ready,
            push=args.open,
        )
    except (TaskRunError, pull_request.PullRequestError, github.GitHubError, OSError, ValueError) as error:
        if args.json:
            print(json.dumps({"error": str(error)}))
        else:
            print(f"autocode pr: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({key: value for key, value in result.items() if key != "body"}))
    elif not args.open:
        print(f"Ready to propose {result['branch']} ({result['commit'][:12]}) to {result['repository']}.")
        print(f"Title: {result['title']}\n\n{result['body']}")
        print("Nothing has been pushed. To push the branch and open a draft pull request, add --open.")
    elif result["opened"]:
        print(f"Opened a {'' if args.ready else 'draft '}pull request: {result['url']}")
    else:
        print(f"Pushed {result['branch']}. Its pull request is {result['url']}")
    return 0
