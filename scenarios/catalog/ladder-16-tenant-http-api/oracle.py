import argparse
import fnmatch
import re
import shlex
from pathlib import Path

from harness.oracle import Check, python_change_checks, run_checks


def documents_test_command(text, project):
    # Parse documented commands, but never import tests or execute README content.
    python = argparse.ArgumentParser(add_help=False, allow_abbrev=False, exit_on_error=False)
    for flag in ("B", "E", "I", "s", "S", "u", "O", "q", "b", "d", "v", "P"):
        python.add_argument("-" + flag, action="count")
    for flag in ("-W", "-X"):
        python.add_argument(flag, action="append")
    python.add_argument("--check-hash-based-pycs", choices=("default", "always", "never"))
    for fragment in re.findall(r"`([^`]+)`", text) + text.splitlines():
        try:
            args = shlex.split(fragment.strip().removeprefix("$").strip())
            if not args or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(args[0]).name):
                continue
            module = args.index("-m")
            _, unknown = python.parse_known_args(args[1:module])
            if unknown or args[module + 1:module + 2] != ["unittest"]:
                continue
            args = args[module + 2:]
            discover = bool(args and args[0] == "discover")
            parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False, exit_on_error=False)
            for short, long in (("-v", "--verbose"), ("-q", "--quiet"), ("-b", "--buffer"),
                                ("-f", "--failfast"), ("-c", "--catch")):
                parser.add_argument(short, long, action="store_true")
            parser.add_argument("--locals", action="store_true")
            parser.add_argument("--durations", type=int)
            parser.add_argument("-k", action="append")
            parser.set_defaults(start=".", top=".", pattern="test*.py", tests=[])
            if discover:
                parser.add_argument("-s", "--start-directory", dest="start")
                parser.add_argument("-t", "--top-level-directory", dest="top")
                parser.add_argument("-p", "--pattern")
                for name in ("start", "pattern", "top"):
                    parser.add_argument(name, nargs="?", default=argparse.SUPPRESS)
            else:
                parser.add_argument("tests", nargs="*")
            options, unknown = parser.parse_known_args(args[1:] if discover else args)
        except (ValueError, argparse.ArgumentError):
            continue
        if unknown:
            continue
        if options.tests:
            names = [name.removeprefix("./").removesuffix(".py").replace("/", ".") for name in options.tests]
            if all(re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", name)
                   and (any(project.joinpath(*name.split(".")[:size]).with_suffix(".py").is_file()
                            for size in range(1, len(name.split(".")) + 1))
                        or project.joinpath(*name.split("."), "__init__.py").is_file()) for name in names):
                return True
            continue
        start, top = (project / options.start).resolve(), (project / options.top).resolve()
        if (start.is_relative_to(project.resolve()) and top.is_relative_to(project.resolve())
                and start.is_dir() and top.is_dir()
                and any(path.is_file() and fnmatch.fnmatch(path.name, options.pattern)
                        for path in start.rglob("*.py"))):
            return True
    return False


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, "app")
    readme = project / "README.md"
    text = readme.read_text(encoding="utf-8", errors="replace") if readme.is_file() else ""
    required = [("make_server callable", r"\bmake_server\b"), ("X-Tenant header", r"\bX-Tenant\b")]
    collection = r"/items(?![\w/])"
    item = r"/items/(?:\w+|\{[^}\s]+\}|<[^>\s]+>|:\w+)(?![\w/])"
    # Accept method-first prose and route-first tables, including grouped methods.
    gap = r"(?:[\s`|,/*:]|\b(?:GET|POST|PATCH|DELETE)\b)*"
    for method, route in (("POST", collection), ("GET", collection),
                          ("GET", item), ("PATCH", item), ("DELETE", item)):
        required.append((f"{method} /items" + ("/ID" if route == item else ""),
                         rf"(?:\b{method}\b{gap}{route}|{route}{gap}\b{method}\b)"))
    missing = [label for label, pattern in required if not re.search(pattern, text, re.IGNORECASE)]
    if not documents_test_command(text, project):
        missing.append("executable unittest command")
    checks.append(Check("readme_delivered", not missing, "missing: " + ", ".join(missing) if missing else ""))
    return checks + run_checks(run, workflow="build")
