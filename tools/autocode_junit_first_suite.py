"""Pure, conservative first-project and definition policy for explicit JUnit proof.

The caller proves inventory completeness, command ownership and report integrity.
These helpers only recognize a known runner's ordinary no-project observation;
missing XML alone is never evidence that the pinned base had no suite.
"""

from __future__ import annotations

import re
import shlex
from pathlib import PurePath, PurePosixPath

FAMILIES = frozenset({"maven", "gradle", "dotnet", "nextest", "rspec"})
_JAVA = frozenset({"maven", "gradle"})
_SHELL = frozenset(";&|<>()#")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_DIAGNOSTICS = {
    "maven": re.compile(
        r"^(?:\[ERROR\]\s*)?The goal you specified requires a project to execute "
        r"but there is no POM in this directory(?:[ .(]|$)",
        re.M,
    ),
    "gradle": re.compile(r"^Directory ['\"][^\n]+['\"] does not contain a Gradle build[.]?\s*$", re.M),
    "dotnet": re.compile(
        r"^MSBUILD\s*:\s*error MSB1003: [^\n]*"
        r"The current working directory does not contain a project or solution file[.]?\s*$",
        re.M,
    ),
    "nextest": re.compile(
        r"^(?:error:\s*)?could not find [`'\"]Cargo[.]toml[`'\"] in [^\n]+ or any parent directory\s*$", re.M
    ),
    "rspec": re.compile(r"^Could not locate Gemfile or [.]bundle/ directory[.]?\s*$", re.M),
}


def family(command):
    """A plain runner argv's family, or None for shell programs/unknown commands.

    Quoted logger arguments may contain semicolons; unquoted shell operators,
    expansion, wrappers and environment-assignment prefixes are unsupported.
    """
    if not isinstance(command, str) or not command.strip() or re.search(r"[\x00-\x1f`$\\]", command):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()#")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return None
    if not words or any(word and set(word) <= _SHELL for word in words):
        return None
    executable, args = PurePosixPath(words[0]).name, words[1:]
    if executable in ("mvn", "mvnw"):
        return "maven"
    if executable in ("gradle", "gradlew"):
        return "gradle"
    if executable == "dotnet" and args[:1] == ["test"]:
        return "dotnet"
    if executable == "cargo" and args[:2] == ["nextest", "run"]:
        return "nextest"
    if executable == "rspec" or (executable == "bundle" and args[:2] == ["exec", "rspec"]):
        return "rspec"
    return None


def _path(path):
    if not isinstance(path, (str, PurePath)) or not str(path) or re.search(r"[\x00-\x1f\\]", str(path)):
        return None
    name = PurePosixPath(path)
    return name if not name.is_absolute() and ".." not in name.parts and name.parts else None


def definition_file(path, family):
    """Pin known build/test selectors and helpers; unknown input is not mutable.

    The caller separately pins ordinary test files and follows selected definition
    dependencies. This list does not claim to discover arbitrary plugin imports.
    """
    name = _path(path)
    if family not in FAMILIES or name is None:
        return True
    leaf, parts = name.name.casefold(), tuple(part.casefold() for part in name.parts)
    if family in _JAVA:
        return bool(
            leaf in {"pom.xml", "build.xml", "ivy.xml", "mvnw", "mvnw.cmd", "settings.xml", ".classpath", ".project"}
            or leaf.startswith("gradle")
            or leaf in {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
            or leaf.endswith((".gradle", ".gradle.kts"))
            or any(part in {".mvn", ".m2", "gradle", "buildsrc"} for part in parts[:-1])
        )
    if family == "nextest":
        return bool(
            leaf in {"cargo.toml", "cargo.lock", "build.rs", "rust-toolchain", "rust-toolchain.toml"}
            or leaf.startswith("nextest")
            or ".cargo" in parts[:-1]
        )
    if family == "dotnet":
        return bool(
            leaf.endswith(("proj", ".sln", ".slnx", ".props", ".targets", ".runsettings", ".ruleset"))
            or leaf in {"global.json", "nuget.config", "packages.config", "testconfig.json"}
            or ".config" in parts[:-1]
        )
    return bool(
        leaf
        in {
            "gemfile",
            "gemfile.lock",
            "gems.rb",
            "gems.locked",
            "rakefile",
            ".rspec",
            ".ruby-version",
            "spec_helper.rb",
            "rails_helper.rb",
            "test_helper.rb",
        }
        or leaf.endswith((".gemspec", ".rake"))
        or ".bundle" in parts[:-1]
        or ("spec" in parts[:-1] and "support" in parts[:-1])
    )


def contains_project(family, paths):
    """Whether inventory could already contain this language/build ecosystem.

    Unknown families, inventory shapes and unsafe paths cannot establish absence.
    Other-language source alone does not prevent a genuinely first project.
    """
    if family not in FAMILIES or paths is None or isinstance(paths, (str, bytes)):
        return True
    suffixes = (
        {".java", ".kt", ".kts", ".groovy", ".scala", ".class", ".jar", ".war", ".ear"}
        if family in _JAVA
        else {".rs", ".rlib", ".rmeta"}
        if family == "nextest"
        else {".cs", ".fs", ".fsx", ".vb", ".dll", ".exe"}
        if family == "dotnet"
        else {".rb"}
    )
    for path in paths:
        name = _path(path)
        if name is None or definition_file(path, family) or name.suffix.casefold() in suffixes:
            return True
    return False


def _ordinary(receipt, exit_code=None):
    code = receipt.get("exit_code")
    return bool(
        type(code) is int
        and (code > 0 if exit_code is None else code == exit_code)
        and receipt.get("timed_out") is False
        and not receipt.get("error")
        and not receipt.get("interrupted")
        and not receipt.get("cancelled")
    )


def reported_absence(receipt, family):
    """A missing report plus this exact runner's explicit no-project diagnostic."""
    if not isinstance(receipt, dict) or family not in FAMILIES:
        return False
    return bool(
        _ordinary(receipt)
        and receipt.get("results_format") == "junit"
        and receipt.get("report_status") == "missing"
        and receipt.get("results") is None
        and receipt.get("test_report") is None
        and _command_family(receipt.get("command")) == family
        and isinstance(receipt.get("tail"), str)
        and _DIAGNOSTICS[family].search(_ANSI.sub("", receipt["tail"]))
    )


_command_family = family


def absent_suite(base, candidate, *, family, no_project):
    """No existing suite to preserve, never a PASS or a new-behavior result.

    ``no_project`` is the caller's proof from pinned inventory, including copied
    ignored inputs. Command ownership/report authentication remain caller duties.
    """
    if no_project is not True or not reported_absence(base, family) or not isinstance(candidate, dict):
        return False
    results = candidate.get("results")
    if not isinstance(results, dict):
        return False
    passed = results.get("passed")
    return bool(
        _ordinary(candidate, 0)
        and candidate.get("results_format") == "junit"
        and _command_family(candidate.get("command")) == family
        and results.get("complete") is True
        and isinstance(passed, list)
        and passed
        and all(isinstance(name, str) and name.strip() for name in passed)
        and len(set(passed)) == len(passed)
        and type(results.get("total")) is int
        and results["total"] == len(passed)
        and all(results.get(key, []) == [] for key in ("failed", "skipped", "collection_errors", "uncollected"))
    )
