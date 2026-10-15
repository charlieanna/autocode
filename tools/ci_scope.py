#!/usr/bin/env python3
"""Choose PR verification by changed area; master/manual retain every job.

This is CI tooling only. It never configures a provider or changes host admission.
Use the event's pinned commit IDs, including both sides of a rename, rather than
a branch name that can move while a run is queued.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

JOBS = ("suite", "scenarios", "dashboard", "visual", "containers", "macos", "linux", "windows", "wheel", "docs")
PLATFORM = (
    "tools/autocode_process",
    "tools/autocode_supervision",
    "tools/autocode_session_boundary",
    "tools/autocode_provider_launch",
    "tools/provider_conformance",
    "tools/autocode_tool_containment",
    "tools/autocode_toolchain",
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


def select(paths: list[str], *, full: bool = False) -> dict[str, bool]:
    """Return job/step decisions. A workflow edit exercises its own job wiring."""
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

        if path.startswith(("scenarios/", "test-scenarios/", *LIFECYCLE, *MACHINERY)):
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
        ) or path in ("tools/autocode_run_view.py", "tools/autocode_roles.py"):
            selected["dashboard"] = True
        if path.startswith("tests/test_") and "browser" in path:
            selected["dashboard"] = True
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
