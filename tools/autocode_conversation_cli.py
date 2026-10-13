"""Public core Requirements conversation commands, using the TaskRun interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from . import autocode_conversation_policy as policy
    from .autocode_control_tokens import resolve_placeholders
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    import autocode_conversation_policy as policy
    from autocode_control_tokens import resolve_placeholders
    from autocode_taskrun import TaskRun, TaskRunError


def creation_options(values):
    """CLI and Python callers use the same bounded creation authority."""
    return policy.creation_options(values)


def command():
    """Use the same running package/interpreter, never a global CLI fallback."""
    if __package__ == "autocode_cli":
        return (sys.executable, "-m", "autocode_cli")
    return (sys.executable, str(Path(__file__).resolve().parent / "autocode.py"))


def parser():
    root = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    actions = root.add_subparsers(dest="action", required=True)
    for name in ("start", "attach", "read", "send", "refresh", "handoff"):
        child = actions.add_parser(name, allow_abbrev=False)
        child.add_argument("--workspace", required=True, type=Path)
        child.add_argument("--timeout", type=float, help="Deadline for each public task-run CLI invocation")
        if name != "start":
            child.add_argument("--run-dir", required=True, type=Path)
        if name in ("start", "send"):
            child.add_argument("--text", required=True)
        if name == "start":
            child.add_argument("--title")
        if name in ("start", "send", "refresh"):
            child.add_argument("--request-id", required=True)
        if name in ("send", "refresh", "handoff"):
            child.add_argument("--expected-revision", required=True, type=int)
        if name == "send":
            child.add_argument("--supersedes-request-id", help="Pending human request with a retained runner rejection")
        if name == "refresh":
            child.add_argument("--question-id")
            child.add_argument("--resolver-token", help="Inspected resolver token; '-' reads AUTOCODE_RESOLVER_TOKEN")
        if name == "handoff":
            child.add_argument(
                "--expected-plan-token",
                required=True,
                help="Inspected plan token; '-' reads AUTOCODE_EXPECTED_GOAL_TOKEN",
            )
    return root


def cli(argv=None):
    values = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in values:
        offset = values.index("--")
        values, extra = values[:offset], values[offset + 1 :]
    cli_parser = parser()
    args = cli_parser.parse_args(values)
    if extra and args.action != "start":
        cli_parser.error("Only conversation start accepts creation options after --")
    resolve_placeholders(
        args,
        cli_parser,
        os.environ,
        fields={
            "resolver_token": "AUTOCODE_RESOLVER_TOKEN",
            "expected_plan_token": "AUTOCODE_EXPECTED_GOAL_TOKEN",
        },
    )
    try:
        if args.action == "start":
            run = TaskRun.start_conversation(
                args.workspace,
                args.text,
                request_id=args.request_id,
                title=args.title,
                start_options=creation_options(extra),
                command=command(),
                timeout=args.timeout,
            )
            result = run.read_conversation()
        else:
            run = TaskRun.attach_conversation(
                args.workspace,
                run_dir=args.run_dir,
                command=command(),
                timeout=args.timeout,
            )
            if run is None:
                raise TaskRunError("No conversation run exists in the selected workspace")
            if args.action in ("attach", "read"):
                result = run.read_conversation()
            elif args.action == "send":
                result = run.send_conversation(
                    args.text,
                    request_id=args.request_id,
                    expected_revision=args.expected_revision,
                    supersedes_request_id=args.supersedes_request_id,
                )
            elif args.action == "refresh":
                result = run.refresh_conversation(
                    request_id=args.request_id,
                    expected_revision=args.expected_revision,
                    question_id=args.question_id,
                    resolver_token=args.resolver_token,
                )
            else:
                result = run.handoff_conversation(
                    expected_revision=args.expected_revision,
                    expected_plan_token=args.expected_plan_token,
                )
    except (OSError, ValueError, TaskRunError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
