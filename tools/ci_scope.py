#!/usr/bin/env python3
"""Choose PR verification by changed area; master/manual retain every job.

This is CI tooling only. It never configures a provider or changes host admission.
Use the event's pinned commit IDs, including both sides of a rename, rather than
a branch name that can move while a run is queued.
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
from pathlib import Path

import run_suite

ROOT = Path(__file__).resolve().parents[1]
JOBS = (
    "suite",
    "scenarios",
    "dashboard",
    "extension",
    "visual",
    "containers",
    "macos",
    "linux",
    "windows",
    "wheel",
    "docs",
)
PLATFORM = (
    "tools/autocode_process",
    "tools/autocode_supervision",
    "tools/autocode_session_boundary",
    "tools/autocode_provider_launch",
    "tools/provider_conformance",
    "tools/autocode_tool_containment",
    "tools/autocode_toolchain",
    "tools/autocode_python_entrypoints",
    "tools/autocode_elf_dependencies",
    "tools/autocode_util.py",
    "tools/providers/",
    "tests/test_tool_containment",
    "tests/test_toolchain",
    "tests/test_elf_dependencies",
    "tests/test_session_boundary",
    "tests/test_provider_refusal",
    "tests/test_windows",
    "tests/test_supervision",
)
# Native readonly checks are admitted only on macOS (Seatbelt and the Command Line Tools
# Git), so the tests that prepare or run one execute only in macos-tool-containment.
NATIVE_CHECKS = (
    "tools/autocode_native_check_",
    "tools/autocode_native_launch",
    "tools/autocode_verification_copy",
    "tools/autocode_visual_protection",
    "tests/test_native_check_",
    "tests/test_native_initial_callers",
)
# A fake OpenCode CLI run whose stages launch contained (#709): only linux-tool-containment
# requires it to run, under bubblewrap, without skipping. That job runs for every Python file
# this test module loads (contained_flow_files), not a hand list that misses what it imports,
# and for the data below.
CONTAINED_FLOW = "tests/test_contained_fake_cli.py"
# Data the flow reads that no import names: the JSON schemas each stage's report is checked
# against (autocode.SCHEMA_DIR). Prompts are not listed, so a prompt-only change skips the job.
CONTAINED_FLOW_DATA = ("tools/autocode-schemas/",)
# Where an import can load from: tests/__init__.py puts tools/, scenarios/ and the root on sys.path.
IMPORT_ROOTS = ("tools/", "scenarios/", "")
# A script started or copied by its file name, as `TOOLS / "fake_opencode.py"` or
# `Path(__file__).with_name("autocode_builder_worker.py")`.
SCRIPT_NAME = re.compile(r"""["'](\w+\.py)["']""")
LIFECYCLE = (
    *PLATFORM,
    "tools/autocode_completion",
    "tools/autocode_dispatch",
    "tools/autocode_provider_launch",
    "tools/autocode_build_loop",
)
MACHINERY = (
    "tools/ci_scope.py",
    "tools/ci_test_selection.py",
    "tools/run_suite.py",
    "tests/test_ci_scope.py",
    "tests/test_ci_test_selection.py",
    "tests/test_run_suite.py",
    "tests/__init__.py",
    "tests/suite_exclusions.json",
    "tests/suite_slow.json",
)


def module_files(name: str) -> list[str]:
    """Every file an import of ``name`` (and of each package above it) could load from."""
    parts = name.split(".")
    stems = ["/".join(parts[:end]) for end in range(1, len(parts) + 1)]
    return [f"{root}{stem}{suffix}" for stem in stems for root in IMPORT_ROOTS for suffix in (".py", "/__init__.py")]


@functools.cache
def contained_flow_files(root: Path = ROOT) -> frozenset[str]:
    """The repository files the contained fake OpenCode flow loads, followed from CONTAINED_FLOW.

    It follows each file's import statements (run_suite.imported_names, including those inside
    functions) and the scripts it names by file name, beside it or in tools/: the tests start the
    CLI and copy the fake clients that way, and the runtime starts its own workers the same way.
    A module loaded by a name built at run time (importlib.import_module(f"...")), such as a
    subcommand from autocode_subcommands' table, is not followed. An import counts whether or not
    its file exists, so deleting a module the flow still imports, or adding one that would shadow
    it, selects the job too. A file that does not parse stays in, but its imports are not followed.
    A missing CONTAINED_FLOW fails selection instead of quietly skipping the job. Only Python files
    are followed; select() adds CONTAINED_FLOW_DATA."""
    if not (root / CONTAINED_FLOW).is_file():
        raise FileNotFoundError(f"{CONTAINED_FLOW} is missing: name the module linux-tool-containment runs")
    seen: set[str] = set()
    todo = [CONTAINED_FLOW]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        file = root / path
        if not file.is_file():
            continue
        source = file.read_text(encoding="utf-8", errors="replace")
        directory = Path(path).parent
        for name in SCRIPT_NAME.findall(source):
            todo.extend(((directory / name).as_posix(), f"tools/{name}"))
        try:
            names = run_suite.imported_names(source, ".".join(directory.parts))
        except (SyntaxError, ValueError):
            continue
        todo.extend(candidate for name in names for candidate in module_files(name))
    return frozenset(seen)


def select(paths: list[str], *, full: bool = False) -> dict[str, bool]:
    """Return job/step decisions. A workflow edit exercises its own job wiring.

    The contained-flow trigger reads the checked-out sources (contained_flow_files)."""
    selected = dict.fromkeys(JOBS, full)
    for path in paths:
        if path == ".github/workflows/tests.yml" or path in ("pyproject.toml", "setup.py", "setup.cfg"):
            return dict.fromkeys(JOBS, True)
        if path == ".github/workflows/documentation.yml":
            selected["docs"] = True
        elif path == ".github/workflows/local-compose.yml":
            selected["containers"] = True
        elif path.startswith(("docs/", "README", "CHANGELOG", "mkdocs.", "LICENSE")):
            selected["docs"] = True
        elif path.startswith(".github/workflows/"):
            # Other standalone workflows keep their existing triggers.
            continue
        elif path.startswith(("tools/", "tests/", "scenarios/", "test-scenarios/", "macos-app/")):
            selected["suite"] = True
        elif path not in ("AGENTS.md", ".gitignore", ".gitattributes"):
            # An unfamiliar repository input still gets the Python regression gate.
            selected["suite"] = True

        if path.startswith(("scenarios/", "test-scenarios/", *LIFECYCLE, *MACHINERY)) or (
            path in run_suite.SCENARIO_FIXTURES or path in run_suite.SCENARIO_RUNTIME_CONSUMERS
        ):
            selected["scenarios"] = True
        if path.startswith(
            (
                "tools/dashboard/",
                "macos-app/",
                "tests/test_dashboard",
                "tests/test_catalogue_t12.py",
                "tests/test_autocode_stop.py",
                "tests/browser_suite_process.py",
                "tests/browser_",
            )
        ) or path in (
            "tools/autocode_run_view.py",
            "tools/autocode_roles.py",
            "tools/autocode_legacy_process.py",
        ):
            selected["dashboard"] = True
        if path.startswith("tests/test_") and "browser" in path:
            selected["dashboard"] = True
        # The VS Code client is TypeScript over the CLI, so it has its own Node gate
        # rather than the dashboard's (#884).
        if path.startswith(("vscode-extension/", "tools/autocode_chat_")) or path in (
            "tools/autocode_run_view.py",
            "tools/autocode_quota_route.py",
            "tools/autocode_roles.py",
            "tools/autocode_usage.py",
            "tools/autocode_progress_view.py",
        ):
            selected["extension"] = True
        if path.startswith(("tools/autocode_visual", "tools/autocode_image", "tests/test_visual", "tests/test_image")):
            selected["visual"] = True
        if path.startswith(
            (
                "tools/autocode_local_compose",
                "tools/autocode_compose_file.py",
                "tools/fixtures/local_compose/",
                "tools/fixtures/fake_docker.py",
                "tests/test_local_compose",
                "tests/test_compose_file.py",
                "scenarios/catalog/local-compose",
                "scenarios/harness/component_services.py",
                "scenarios/harness/processes.py",
                "examples/local-compose/",
            )
        ):
            selected["containers"] = True
        if path.startswith(PLATFORM):
            for job in ("macos", "linux", "windows"):
                selected[job] = True
        if path.startswith(NATIVE_CHECKS):
            selected["macos"] = True
        if path.startswith(CONTAINED_FLOW_DATA) or (path.endswith(".py") and path in contained_flow_files()):
            selected["linux"] = True
        if (
            path.startswith("tools/")
            or path.startswith("tests/installed_")
            or path
            in (
                "MANIFEST.in",
                "requirements.txt",
            )
        ):
            selected["wheel"] = True
    return selected


def changed_paths(base: str, head: str) -> list[str]:
    for revision in (base, head):
        if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise ValueError("CI scope requires full event commit IDs")
    result = subprocess.run(
        ["git", "diff", "--no-renames", "--name-only", "-z", f"{base}...{head}"],
        check=True,
        capture_output=True,
    )
    return [path.decode("utf-8", errors="surrogateescape") for path in result.stdout.split(b"\0") if path]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    if not args.full and (not args.base or not args.head):
        parser.error("a PR requires --base and --head, or use --full")
    selected = select([] if args.full else changed_paths(args.base, args.head), full=args.full)
    if args.github_output:
        with args.github_output.open("a") as output:
            for job, enabled in selected.items():
                output.write(f"{job}={str(enabled).lower()}\n")
    print(json.dumps(selected, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
