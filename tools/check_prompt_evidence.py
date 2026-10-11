"""Read-only CI disclosure gate for versioned prompt resource changes.

The PR body must contain a ``Live run evidence`` Markdown section. Each scoped
block names ``Resources: `tools/prompts/...md`, ...`` and either ``Exempt reason``
or all of: Command, Provider, Models, Verdict, Duration, Run directory, Reach.
A run may cover several resources. These are reviewer-readable declarations,
not an attestation that a command ran or that an exemption is justified.

Only trusted base code runs this gate. PR code, patches and body commands are
never evaluated; GitHub metadata is fetched with bounded, read-only requests.
"""

from __future__ import annotations

import html
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = "https://api.github.com"
PAGE_SIZE = 100
MAX_FILES = 3000  # GitHub's files endpoint is capped here; hitting the cap is uncertain.
MAX_BYTES = 8 * 1024 * 1024
FIELDS = ("command", "provider", "models", "verdict", "duration", "run directory", "reach")
PLACEHOLDERS = {"", "-", "?", "???", "n/a", "na", "none", "not run", "not yet", "placeholder", "example"}
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
STATUSES = {"added", "removed", "modified", "renamed", "copied", "changed", "unchanged"}


class GateError(ValueError):
    """Missing disclosure or uncertain API inventory; fail closed."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GateError("GitHub metadata redirect refused")


class GitHub:
    def __init__(self, token: str):
        if not token:
            raise GateError("Missing read-only GitHub token")
        self.token = token
        self.opener = build_opener(NoRedirect())

    def get(self, path: str) -> Any:
        request = Request(
            API + path,
            headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer " + self.token},
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                if response.getcode() != 200:
                    raise GateError("GitHub metadata did not return HTTP 200")
                data = response.read(MAX_BYTES + 1)
        except HTTPError as error:
            raise GateError(f"GitHub metadata request failed (HTTP {error.code})") from error
        except (URLError, TimeoutError, OSError) as error:
            raise GateError("GitHub metadata request failed") from error
        if len(data) > MAX_BYTES:
            raise GateError("GitHub metadata response exceeds the size bound")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise GateError("GitHub metadata is not valid UTF-8 JSON") from error


def _metadata(value: Any, repository: str, number: int) -> tuple[str, str, str, int]:
    try:
        if not isinstance(value, dict) or type(value["number"]) is not int or value["number"] != number:
            raise GateError("GitHub returned the wrong PR")
        body = value["body"]
        if body is None:
            body = ""
        if not isinstance(body, str):
            raise GateError("PR body is not text")
        head, base = value["head"]["sha"], value["base"]["sha"]
        if any(not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (head, base)):
            raise GateError("PR revision identity is malformed")
        if value["base"]["repo"]["full_name"] != repository:
            raise GateError("PR base is not the trusted repository")
        count = value["changed_files"]
        if type(count) is not int or not 0 <= count < MAX_FILES:
            raise GateError("PR file inventory is invalid or hits GitHub's file cap")
        return head, base, body, count
    except (KeyError, TypeError) as error:
        raise GateError("Incomplete PR metadata") from error


def _filename(value: Any) -> str:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise GateError("Malformed PR filename")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise GateError("Noncanonical PR filename")
    return value


def changed_resources(files: Sequence[Any]) -> tuple[str, ...]:
    resources: set[str] = set()
    for row in files:
        if not isinstance(row, dict) or row.get("status") not in STATUSES:
            raise GateError("Unknown PR file status")
        names = [_filename(row.get("filename"))]
        if row["status"] == "renamed":
            names.append(_filename(row.get("previous_filename")))
        resources.update(name for name in names if name.startswith("tools/prompts/") and name.endswith(".md"))
    return tuple(sorted(resources))


def pull_request(repository: str, number: int, get: Callable[[str], Any]) -> tuple[str, tuple[str, ...]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or type(number) is not int or number < 1:
        raise GateError("Invalid repository or PR number")
    endpoint = f"/repos/{repository}/pulls/{number}"
    initial = _metadata(get(endpoint), repository, number)
    files: list[Any] = []
    seen: set[str] = set()
    for page in range(1, MAX_FILES // PAGE_SIZE + 1):
        batch = get(f"{endpoint}/files?per_page={PAGE_SIZE}&page={page}")
        if not isinstance(batch, list) or len(batch) > PAGE_SIZE:
            raise GateError("Malformed PR files page")
        for row in batch:
            if not isinstance(row, dict):
                raise GateError("Malformed PR file record")
            name = _filename(row.get("filename"))
            if name in seen:
                raise GateError("Duplicate PR filename across pages")
            seen.add(name)
        files.extend(batch)
        if len(files) > initial[3]:
            raise GateError("PR file count changed or inventory is inconsistent")
        if len(batch) < PAGE_SIZE:
            break
    else:
        raise GateError("PR file pagination reached the bound")
    if len(files) != initial[3]:
        raise GateError("PR file inventory is incomplete")
    resources = changed_resources(files)
    if _metadata(get(endpoint), repository, number) != initial:
        raise GateError("PR head, base, body or file count changed during the check; rerun")
    return initial[2], resources


def _section(body: str) -> list[str]:
    # Ignore examples and HTML comments: only an actual rendered section counts.
    body = re.sub(r"<!--.*?(?:-->|$)", "", body, flags=re.S)
    rendered: list[tuple[str, tuple[int, str] | None]] = []
    fence: str | None = None
    for line in body.splitlines():
        marker = FENCE.match(line)
        if marker:
            text = marker[1]
            if fence is None:
                fence = text
            elif text[0] == fence[0] and len(text) >= len(fence):
                fence = None
            continue
        if fence is not None or line.startswith(("    ", "\t")):
            continue
        heading = HEADING.match(line)
        rendered.append((line, (len(heading[1]), heading[2].strip().casefold()) if heading else None))
    starts = [i for i, (_, heading) in enumerate(rendered) if heading and heading[1] == "live run evidence"]
    if len(starts) != 1:
        raise GateError("Expected exactly one Live run evidence section")
    start = starts[0]
    start_heading = rendered[start][1]
    assert start_heading is not None
    level = start_heading[0]
    end = len(rendered)
    for i in range(start + 1, len(rendered)):
        rendered_heading = rendered[i][1]
        if rendered_heading is not None and rendered_heading[0] <= level:
            end = i
            break
    return [line for line, _ in rendered[start + 1 : end]]


def _blocks(lines: Sequence[str]) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = [{}]
    for line in lines:
        if HEADING.match(line):
            blocks.append({})
            continue
        prefix, separator, value = line.partition(":")
        if not separator:
            continue
        label = prefix.lstrip(" -*").replace("**", "").strip().casefold()
        if label not in {*FIELDS, "resources", "exempt reason"}:
            continue
        if label in blocks[-1]:
            raise GateError("Duplicate evidence field: " + label)
        blocks[-1][label] = value.lstrip("*").strip()
    return blocks


def _scopes(value: str) -> set[str]:
    quoted = re.findall(r"`([^`]+)`", value)
    if quoted:
        if re.sub(r"`[^`]+`", "", value).strip(" ,;"):
            raise GateError("Resources must list exact comma-separated paths")
        return set(quoted)
    return {part.strip() for part in value.split(",") if part.strip()}


def _meaningful(value: str) -> bool:
    value = value.strip().strip("`*").strip().casefold()
    return value not in PLACEHOLDERS and not re.match(r"^(todo|tbd|pending)(?:\W|$)", value)


@dataclass(frozen=True)
class Disclosure:
    resources: tuple[str, ...]
    covered: Mapping[str, str]
    errors: tuple[str, ...]


def disclosure(body: str, resources: Sequence[str]) -> Disclosure:
    resources = tuple(sorted(set(resources)))
    if not resources:
        return Disclosure(resources, {}, ())
    covered: dict[str, str] = {}
    errors: list[str] = []
    try:
        blocks = _blocks(_section(body))
        for block in blocks:
            scope = _scopes(block.get("resources", "")) & set(resources)
            if not scope:
                continue
            reason = block.get("exempt reason")
            if reason is not None:
                missing = [] if _meaningful(reason) else ["Exempt reason"]
                kind = "Scoped exemption disclosed"
            else:
                missing = [field for field in FIELDS if not _meaningful(block.get(field, ""))]
                if not missing:
                    duration = re.search(
                        r"\b(\d+(?:\.\d+)?)\s*(?:s|seconds?|m|minutes?|h|hours?)\b", block["duration"], re.I
                    )
                    if not duration or float(duration[1]) <= 0:
                        missing.append("positive Duration with units")
                    if re.match(r"(?i)^(fake|mock|simulated)(?:\W|_|$)", block["provider"].strip("` ")) or re.search(
                        r"--fake(?:\W|$)", block["command"]
                    ):
                        missing.append("real-provider qualification (or a scoped exemption)")
                kind = "Live qualification disclosed"
            if missing:
                errors.append(
                    "Missing/invalid " + ", ".join(missing) + " for " + ", ".join(json.dumps(x) for x in sorted(scope))
                )
            else:
                covered.update({name: kind for name in scope})
    except GateError as error:
        errors.append(str(error))
    omitted = set(resources) - covered.keys()
    if omitted:
        errors.append("No scoped disclosure for " + ", ".join(json.dumps(x) for x in sorted(omitted)))
    return Disclosure(resources, covered, tuple(errors))


def summary(result: Disclosure | None, error: str | None = None) -> str:
    lines = [
        "## Prompt resource evidence disclosure",
        "",
        "This checks PR disclosure only; it does not attest execution or exemption validity.",
        "",
    ]
    if result is None:
        lines.append("Changed resource inventory could not be established; failing closed.")
    elif not result.resources:
        lines.append("No changed tools/prompts/**/*.md resources.")
    else:
        lines.extend(["| Changed resource | Disclosure |", "| --- | --- |"])
        for name in result.resources:
            safe = html.escape(name).replace("\n", "&#10;").replace("\r", "&#13;").replace("|", "&#124;")
            lines.append(f"| <code>{safe}</code> | {result.covered.get(name, 'Missing')} |")
        if result.errors:
            lines.extend(
                [
                    "",
                    "Add scoped Resources and either Command, Provider, Models, Verdict, Duration, Run directory, Reach or Exempt reason inside Live run evidence.",
                ]
            )
    if error:
        lines.extend(["", html.escape(error)])
    return "\n".join(lines) + "\n"


def main(environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    result: Disclosure | None = None
    error: str | None = None
    try:
        with Path(environ["GITHUB_EVENT_PATH"]).open("rb") as source:
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise GateError("Event metadata exceeds the size bound")
        event = json.loads(data)
        number = event["pull_request"]["number"]
        body, paths = pull_request(environ["GITHUB_REPOSITORY"], number, GitHub(environ.get("GITHUB_TOKEN", "")).get)
        result = disclosure(body, paths)
        if result.errors:
            error = "; ".join(result.errors)
    except (GateError, KeyError, TypeError, ValueError, OSError) as failure:
        error = "Prompt disclosure check failed: " + str(failure)
    output = summary(result, error)
    if environ.get("GITHUB_STEP_SUMMARY"):
        Path(environ["GITHUB_STEP_SUMMARY"]).write_text(output)
    print(output)
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
