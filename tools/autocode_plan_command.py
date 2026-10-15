"""`autocode plan`: a saved run's plan as a file a team reviews in a pull request (#720).

`--export PATH` writes the run's current contract (approved or waiting for approval) as Markdown
with a front matter that holds the contract exactly (autocode_plan_file). `--check PATH` reads such
a file back and prints its goal hash, and exits 2 when the file cannot be read or its text no longer
matches its front matter. `--render PATH` regenerates that text after the front matter was edited.

Read-only toward the run: like `autocode events` it reads the saved checkpoint, takes no run lock,
records nothing and launches nothing. Only the named file is written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import argparse
import os
import sys
import tempfile
from pathlib import Path

try:
    from . import autocode_bundle as bundle
    from . import autocode_contract_identity as contract_identity
    from . import autocode_events_command as events
    from . import autocode_plan_file as plan_file
    from . import autocode_registry as registry
except ImportError:
    import autocode_bundle as bundle
    import autocode_contract_identity as contract_identity
    import autocode_events_command as events
    import autocode_plan_file as plan_file
    import autocode_registry as registry


def export(run_dir: Path, workspace: Path, path: Path) -> str:
    """Write the run's current contract to ``path``; the line to print."""
    availability, payload = registry.read_checkpoint(run_dir / "state.json", workspace)
    if availability != "available":
        raise ValueError(str(payload.get("message") or availability))
    state = cast("RunState", payload["state"])
    contract = state.get("goal_contract") or {}
    if not isinstance(contract.get("body"), dict) or type(contract.get("revision")) is not int:
        raise ValueError(f"{run_dir} has no plan yet; export it once the run shows a plan for approval")
    approved = contract_identity.approved(state)
    text = plan_file.export(
        contract["body"], revision=contract["revision"], approved=approved, secret_values=bundle.secret_values()
    )
    write(path, text)
    goal = plan_file.goal_hash(contract["body"])
    line = f"Wrote plan revision {contract['revision']} ({'approved' if approved else 'not approved'}) to {path}"
    written = plan_file.read(text).goal_hash
    if written == goal:
        return f"{line}; goal hash {goal}"
    return f"{line} with credential-shaped text masked, so its goal hash {written} differs from the run's plan, {goal}"


def check(path: Path) -> tuple[int, str]:
    """The exit code and line to print for a plan file."""
    plan = plan_file.read(read(path))
    if plan.stale_line is not None:
        return 2, (
            f"{path}: the text under the front matter no longer matches it (line {plan.stale_line}). "
            f"The front matter is the plan: run `autocode plan --render {path}` to regenerate the text"
        )
    same = plan.goal_hash == plan.exported_hash
    return 0, (
        f"{path}: goal hash {plan.goal_hash}; "
        + (
            f"the plan exported from revision {plan.revision}"
            if same
            else f"differs from revision {plan.revision} as exported, goal hash {plan.exported_hash}"
        )
    )


def render(path: Path) -> str:
    text = plan_file.rendered(read(path))
    write(path, text)
    return f"Regenerated the text of {path} from its front matter; goal hash {plan_file.read(text).goal_hash}"


def read(path: Path) -> str:
    """The file's text as written: a carriage return inside a plan's text is not a line break."""
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            return stream.read()
    except UnicodeDecodeError as error:
        raise ValueError(f"{path} is not UTF-8 text: {error}") from None


def write(path: Path, text: str) -> None:
    """Replace ``path`` whole, so an interrupted write never leaves half a plan."""
    path = path.expanduser()
    if path.is_dir():
        raise ValueError(f"{path} is a directory; name the plan file to write")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="autocode plan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    action = result.add_mutually_exclusive_group(required=True)
    action.add_argument("--export", type=Path, metavar="PATH", help="write the run's current plan to this file")
    action.add_argument("--check", type=Path, metavar="PATH", help="read a plan file and print its goal hash")
    action.add_argument(
        "--render", type=Path, metavar="PATH", help="regenerate a plan file's text from its edited front matter"
    )
    result.add_argument("--workspace", type=Path, help="with --export: project whose saved run to export")
    result.add_argument(
        "--run-dir", type=Path, help="with --export: this saved run; otherwise the workspace's only run"
    )
    return result


def cli(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.export is None and (args.workspace is not None or args.run_dir is not None):
        arguments.error("--workspace and --run-dir select the run to export; they go with --export")
    try:
        if args.export is not None:
            run_dir, workspace = events.resolve(args.workspace, args.run_dir)
            print(export(run_dir, workspace, args.export))
            return 0
        if args.render is not None:
            print(render(args.render))
            return 0
        code, line = check(args.check)
        print(line, file=sys.stdout if code == 0 else sys.stderr)
        return code
    except (OSError, ValueError) as error:
        print(f"autocode plan: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
