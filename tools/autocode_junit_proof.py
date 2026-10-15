"""Pinned base inventories and suite-definition boundaries for explicit JUnit proof.

The verifier owns Git and scratch trees. This module receives immutable tree
entries and readers; it never discovers policy from the candidate's report.
"""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

try:
    from . import autocode_junit_first_suite as first
    from . import autocode_project_inventory as inventory
    from . import autocode_suite_definition_inputs as inputs
    from . import autocode_suite_definition_policy as policy
except ImportError:
    import autocode_junit_first_suite as first
    import autocode_project_inventory as inventory
    import autocode_suite_definition_inputs as inputs
    import autocode_suite_definition_policy as policy


_INPUTS = {
    "maven": (
        (
            "--file",
            "--settings",
            "--global-settings",
            "--toolchains",
            "--global-toolchains",
            "--projects",
            "--resume-from",
        ),
        ("-gs", "-gt", "-pl", "-rf", "-f", "-s", "-t"),
    ),
    "gradle": (
        ("--build-file", "--settings-file", "--init-script", "--project-dir", "--include-build", "--gradle-user-home"),
        ("-b", "-c", "-I", "-p", "-g"),
    ),
    "dotnet": (
        ("--settings", "--project", "--solution", "--test-adapter-path", "--test-modules", "--extension"),
        ("-s",),
    ),
    "nextest": (
        (
            "--manifest-path",
            "--config-file",
            "--config",
            "--archive-file",
            "--binaries-metadata",
            "--cargo-metadata",
            "--workspace-remap",
        ),
        (),
    ),
    "rspec": (("--options", "--load-path"), ("-O", "-I")),
}
_MAVEN_NONSELECTORS = {"-fae", "-ff", "-fn"}
_DOTNET_VALUE_OPTIONS = {
    "--framework",
    "-f",
    "--logger",
    "-l",
    "--configuration",
    "-c",
    "--filter",
    "--verbosity",
    "-v",
    "--arch",
    "--os",
}
_DOTNET_PROJECT_SUFFIXES = {".csproj", ".fsproj", ".vbproj", ".sln", ".slnx"}
_SAFE_MAVEN_TEST = re.compile(r"test=(?:[A-Za-z_$][A-Za-z0-9_$]*\.)*[A-Za-z_$][A-Za-z0-9_$]*\Z")


def _input_option(word, long, short):
    key = word.partition("=")[0]
    return (
        word.startswith("--")
        and any(option.startswith(key) for option in long)
        or any(word == option or word.startswith(option + "=") for option in long)
        or any(word.startswith(option) for option in short)
    )


def _value(words, index, short, long):
    word = words[index]
    if word in (short, long):
        if index + 1 >= len(words) or words[index + 1].startswith("-"):
            return None, index
        return words[index + 1], index + 1
    if word.startswith(long + "="):
        return word[len(long) + 1 :], index
    if word.startswith(short) and word != short:
        return word[len(short) :].removeprefix("="), index
    return None, index


def command_boundary(command, kind, *, local_paths=()):
    """Veto unbound input selectors without refusing the trusted execution.

    This is not a complete argv validator. Known custom input selectors and
    properties cannot borrow the ordinary filename-based definition boundary.
    """
    if kind not in _INPUTS:
        return "the JUnit command's build-system input binding is not established"
    try:
        words = shlex.split(command)
    except (TypeError, ValueError):
        return "the JUnit command's build-system input binding is not established"
    long, short = _INPUTS[kind]
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--" or word.startswith("@"):
            return "runner settings/response-file inputs are not bound to the base definition"
        if kind == "maven" and word in _MAVEN_NONSELECTORS:
            index += 1
            continue
        if kind == "dotnet" and word in _DOTNET_VALUE_OPTIONS:
            if index + 1 >= len(words) or words[index + 1].startswith("-"):
                return "the .NET command option value is not established"
            index += 2
            continue
        if _input_option(word, long, short):
            return f"command-selected {kind} build/test input is not bound ({word})"
        if kind == "maven" and _input_option(word, ("--define",), ("-D",)):
            value, index = _value(words, index, "-D", "--define")
            if not value or not _SAFE_MAVEN_TEST.fullmatch(value):
                return "Maven user properties may select unbound build/test inputs"
        elif kind == "gradle" and _input_option(
            word, ("--project-prop", "--project-property", "--system-prop", "--system-property"), ("-P", "-D")
        ):
            return "Gradle properties may select unbound build/test inputs"
        elif kind == "dotnet" and (
            word.lower().startswith(("-p:", "/p:", "-p=", "/p=", "-property", "/property", "--property"))
            or word.lower() in ("-p", "/p")
        ):
            return "MSBuild properties may select unbound build/test inputs"
        elif kind == "dotnet" and index > 1 and not word.startswith("-"):
            project = PurePosixPath(word)
            if (
                project.is_absolute()
                or ".." in project.parts
                or "\\" in word
                or project.suffix.lower() not in _DOTNET_PROJECT_SUFFIXES
            ):
                return "the .NET positional project input is not canonically bound"
        elif kind == "rspec" and _input_option(word, ("--require",), ("-r",)):
            value, index = _value(words, index, "-r", "--require")
            if value != "rspec_junit_formatter" or any(
                PurePosixPath(path).name == "rspec_junit_formatter.rb" for path in local_paths
            ):
                return "RSpec local/unknown require input is not bound to the base definition"
        index += 1
    return ""


def no_project(command, listing, copied):
    """A linked/submodule/opaque inventory cannot establish an absent project."""
    kind = first.family(command)
    if kind is None:
        return False
    paths = inventory.regular_paths(listing, executable=True)
    return (
        paths is not None
        and not command_boundary(command, kind, local_paths=[*paths, *copied])
        and not first.contains_project(kind, [*paths, *copied])
    )


def _product(path, kind):
    name = PurePosixPath(path)
    if kind in ("maven", "gradle"):
        return name.suffix in (".java", ".kt", ".scala") or path.startswith("src/main/")
    if kind == "dotnet":
        return name.suffix in (".cs", ".fs", ".vb")
    if kind == "nextest":
        return name.suffix == ".rs"
    if kind == "rspec":
        return name.suffix == ".rb" and (path.startswith("lib/") or path.startswith("app/"))
    return False


def _test_source(text, kind):
    # Markers are conservative ambiguity. Examine raw source as well as a
    # comment-masked view: a URL inside a string must not hide a later test.
    # Unicode expansion over-approximates Java/C# identifier escapes; it never
    # executes source, and a false positive keeps preservation unverified.
    patterns = {
        "maven": r"@\s*(?:[\w.]*)(?:Test|ParameterizedTest|RepeatedTest)|extends\s+TestCase|"
        r"\b(?:junit|testng|kotlin\s*\.\s*test|kotest|scalatest|specs2)\b",
        "dotnet": r"[\[<]\s*(?:[\w.]*)(?:Test|Fact|Theory)|"
        r"\b(?:xunit|nunit|mstest|Microsoft\s*\.\s*VisualStudio\s*\.\s*TestTools)\b",
        "rspec": r"\b(?:rspec|describe|context|specify|it|test|shared_examples|include_examples)\b",
    }
    pattern = patterns["maven" if kind == "gradle" else kind]

    def expand(match):
        value = int(match[1] or match[2], 16)
        return chr(value) if value <= 0x10FFFF else match[0]

    expanded = re.sub(r"\\(?:u+([0-9a-fA-F]{4})|U([0-9a-fA-F]{8}))", expand, text)
    for view in (text, expanded):
        if re.search(pattern, view, re.I) or re.search(
            pattern, re.sub(r"/\*.*?\*/|//[^\n]*", "", view, flags=re.S), re.I
        ):
            return True
    return False


def definition(workspace, command, entries, read, patched, changes, *, is_test):
    """Keep the base's tests and build selectors over candidate product code.

    Build systems can import arbitrary selector code. Any changed input outside
    the recognized product/test/config boundary remains unverified. Unknown
    commands do not acquire a portable definition merely by emitting XML.
    """
    kind = first.family(command)
    found = policy.Definition(set(), "", patched)
    if kind is None:
        found.boundary = "the JUnit command's build-system definition is not established"
        return found
    measured = inputs.reviewed_changes(workspace, changes or {}, patched)
    found.boundary = command_boundary(command, kind, local_paths=[*entries, *measured])
    if found.boundary:
        return found
    retained = {p for p in entries if is_test(p) or first.definition_file(p, kind)}
    found.paths = inputs.linked_inputs(entries, read, retained)
    for path in measured:
        if path in found.paths or is_test(path) or first.definition_file(path, kind):
            continue
        if not _product(path, kind):
            found.boundary = f"JUnit suite input {path} is outside the pinned {kind} definition"
            return found
        if path in entries and (kind == "nextest" or _test_source(read(path), kind)):
            found.boundary = f"Product code and a possible nonstandard test definition share {path}"
            return found
    found.changed = any(p in found.paths or is_test(p) or first.definition_file(p, kind) for p in measured)
    return found


def report_intact(receipt, junit, *, root=None):
    """A missing report is only an owned absence observation, never positive proof."""
    if receipt.get("report_status") == "missing":
        return receipt.get("results") is None and receipt.get("test_report") is None
    return junit.intact(receipt, root=root)


def judge_definition(retained, base_suite, fail, unverified, notes, *, judge):
    """Preservation compares the base test bodies as well as named identities."""
    if retained.get("boundary"):
        unverified.append(
            "The base JUnit suite definition could not be established ("
            + retained["boundary"]
            + "); preservation is unproven"
        )
    elif retained.get("run") is not None:
        judge(retained["run"], base_suite, fail, unverified, notes)
