"""Interpret known reference-check behavior from the original diagnostic output."""

from __future__ import annotations

import hashlib
import re
import secrets
from collections import Counter, deque
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

_PYTHON = re.compile(r"python(?:[23](?:\.\d+)?)?(?:\.exe)?", re.IGNORECASE)
_ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
_SPHINX_START = re.compile(rb"^Running Sphinx v\d+\.", re.MULTILINE)
_SPHINX_CACHED = re.compile(rb"^loading pickled environment\.\.\.\s+done\s*$", re.MULTILINE)
_SPHINX_FINISHED = re.compile(rb"^build (?:succeeded\.|finished with problems, \d+ warnings?\.)$", re.MULTILINE)
_LINKED_DOCUMENT = re.compile(r"\b(?:toctree|link(?:s|ed|ing)?)\b", re.IGNORECASE)
_PYTHON_ROLE = re.compile(r"(?<![\\\w:]):(?:py:)?(func|class|meth|attr|mod|exc|data|obj):`[^`\n]+`")
_SECTION_ROLE = re.compile(r"(?<![\\\w:]):(?:std:)?ref:`[^`\n]+`")
_MAX_LOG = 4 * 1024 * 1024
_MAX_DOC = 1024 * 1024
_MAX_DOCS = 8
_MAX_RENDERED_BYTES = 32 * 1024 * 1024
SPHINX_OPTIONS = ["-E", "-a", "-n", "-W", "--keep-going", "-b", "html"]


def linked_sphinx_documents(workspace, criteria: dict[str, str], changed_paths) -> dict[str, list[str]]:
    """Find changed Sphinx documents explicitly named in linked-page criteria.

    Both the approved criterion and an existing, ordinary source document
    must be present. A framework name or a generated report alone is not scope.
    """
    linked_criteria = {cid: text for cid, text in criteria.items() if _LINKED_DOCUMENT.search(text)}
    if not workspace or not linked_criteria or not isinstance(changed_paths, list):
        return {}
    try:
        root = Path(workspace).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return {}
    documents: dict[str, list[str]] = {}
    for raw in sorted({p for p in changed_paths if isinstance(p, str)}):
        path = PurePosixPath(raw)
        if path.is_absolute() or path.suffix != ".rst" or ".." in path.parts or path.as_posix() != raw:
            continue
        name = re.compile(r"(?<![\w./-])" + re.escape(raw) + r"(?![\w./-])")
        matched = [cid for cid, text in linked_criteria.items() if name.search(text)]
        if not matched:
            continue
        try:
            doc = root.joinpath(*path.parts)
            if doc.resolve(strict=True) != doc or not doc.is_file():
                continue
            configs = (root.joinpath(*parent.parts, "conf.py") for parent in path.parents)
            if not any(config.is_file() and not config.is_symlink() for config in configs):
                continue
        except (OSError, ValueError, RuntimeError):
            continue
        for cid in matched:
            documents.setdefault(cid, []).append(raw)
    return documents


def _sphinx(command: object) -> bool:
    if not isinstance(command, list) or not command or not all(isinstance(word, str) for word in command):
        return False
    executable = Path(command[0]).name
    return executable in {"sphinx-build", "sphinx-build.exe"} or (
        _PYTHON.fullmatch(executable) is not None and command[1:3] == ["-m", "sphinx"]
    )


def _repository_path(raw: str) -> PurePosixPath:
    path = PurePosixPath(raw)
    if (
        not path.parts
        or path.is_absolute()
        or path.as_posix() != raw
        or ".." in path.parts
        or any(character in raw for character in ("\\", "\0", "\n", "\r", ":"))
    ):
        raise ValueError("Reference checks need literal repository-relative paths")
    return path


def sphinx_scope(command: list[str], documents: list[str]) -> list[str]:
    """Only qualify a direct fresh build of the whole built-in HTML tree."""
    options_start = 3 if command[1:3] == ["-m", "sphinx"] else 1
    if not _sphinx(command) or command[options_start:-2] != SPHINX_OPTIONS:
        raise ValueError("Reference checks require sphinx-build -E -a -n -W --keep-going -b html SOURCE OUTPUT")
    source, output = map(_repository_path, command[-2:])
    if (
        len(output.parts) < 3
        or output.parts[:2] != (".autocode", "scratch")
        or source.parts[0] in {".autocode", ".git"}
    ):
        raise ValueError("Reference check output must be under .autocode/scratch and outside the source tree")
    if not 0 < len(documents) <= _MAX_DOCS or len(set(documents)) != len(documents):
        raise ValueError("Reference checks need one to eight distinct source documents")
    for doc in documents:
        path = _repository_path(doc)
        if path.suffix != ".rst" or not path.is_relative_to(source):
            raise ValueError("Each reference document must be an .rst file in the Sphinx source tree")
    return sorted(documents)


def controls(root: Path, documents: list[str]) -> list[dict[str, str]]:
    """Insert one deliberately unresolved reference per supported role in a private copy."""
    rows: list[dict[str, str]] = []
    for name in documents:
        doc = root / name
        if doc.resolve(strict=True) != doc or not doc.is_file() or doc.stat().st_size > _MAX_DOC:
            raise ValueError(f"Reference control cannot read the ordinary source document {name}")
        original = doc.read_text(encoding="utf-8")
        roles = set(_PYTHON_ROLE.findall(original))
        if not roles or _SECTION_ROLE.search(original):
            roles.add("ref")
        additions = []
        for role in sorted(roles):
            marker = "autocode_reference_control_" + secrets.token_hex(16)
            directive = "ref" if role == "ref" else f"py:{role}"
            additions.append(f"Reference check control: :{directive}:`{marker}`.")
            rows.append({"document": name, "role": role, "target": marker})
        doc.write_text(original + "\n\n" + "\n\n".join(additions) + "\n", encoding="utf-8")
    return rows


class _UnlinkedReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchor_depth = 0
        self.current: list[str] | None = None
        self.unlinked: list[str] = []
        self.toctree_depth = 0
        self.linked_pages: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "div" and (self.toctree_depth or "toctree-wrapper" in classes):
            self.toctree_depth += 1
        if tag == "a" and (href := attributes.get("href")):
            self.anchor_depth += 1
            if self.toctree_depth and {"reference", "internal"} <= classes:
                self.linked_pages.append(href)
        if tag == "code" and not self.anchor_depth and {"xref", "py"} <= classes:
            self.current = []

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.toctree_depth:
            self.toctree_depth -= 1
        if tag == "a" and self.anchor_depth:
            self.anchor_depth -= 1
        if tag == "code" and self.current is not None:
            self.unlinked.append(" ".join("".join(self.current).split()))
            self.current = None


def _sphinx_page_source(source: Path, output: Path, page: Path, href: str) -> Path | None:
    """Follow local rendered toctree links to ordinary source pages in this build."""
    link = urlsplit(href)
    if link.scheme or link.netloc or link.query or not link.path.endswith(".html"):
        return None
    relative = Path(unquote(link.path))
    if relative.is_absolute():
        return None
    target = (page.parent / relative).resolve()
    if not target.is_relative_to(output):
        return None
    document = source / target.relative_to(output).with_suffix(".rst")
    if document.resolve() != document or not document.is_file():
        return None
    return document


def unlinked_output(root: Path, command: list[str], documents: list[str], *, base: bool = False) -> list[str]:
    """Read references on scoped pages and the pages their rendered toctrees actually link."""
    source, output = map(PurePosixPath, command[-2:])
    pending = deque(root / document for document in documents if not base or (root / document).is_file())
    seen: set[Path] = set()
    total_size = 0
    diagnostics: list[str] = []
    while pending:
        document = pending.popleft()
        if document in seen:
            continue
        seen.add(document)
        page = root / output / document.relative_to(root / source).with_suffix(".html")
        name = document.relative_to(root).as_posix()
        if page.resolve(strict=True) != page or not page.is_file():
            raise ValueError(f"Reference comparison has no complete rendered page for {name}")
        size = page.stat().st_size
        total_size += size
        if size > _MAX_LOG or total_size > _MAX_RENDERED_BYTES:
            raise ValueError(f"Reference comparison has no complete rendered page for {name}")
        parser = _UnlinkedReferences()
        parser.feed(page.read_text(encoding="utf-8"))
        parser.close()
        diagnostics.extend(f"{name}: WARNING: rendered Python reference has no link: {ref}" for ref in parser.unlinked)
        for href in parser.linked_pages:
            if linked := _sphinx_page_source(root / source, root / output, page, href):
                pending.append(linked)
    return diagnostics


def _read_log(path: Path, digest) -> bytes | None:
    with path.open("rb") as stream:
        data = stream.read(_MAX_LOG + 1)
    if len(data) > _MAX_LOG or hashlib.sha256(data).hexdigest() != digest:
        return None
    return _ANSI.sub(b"", data)


def _fresh_finished(log: bytes | None) -> bool:
    return bool(log and _SPHINX_START.search(log) and _SPHINX_FINISHED.search(log) and not _SPHINX_CACHED.search(log))


def sensitivity_passed(receipt: dict, workspace: Path) -> bool:
    """Confirm every injected marker produced one new missing-reference warning at its own source."""
    proof = receipt.get("sphinx_reference_control")
    if not isinstance(proof, dict) or receipt.get("base_exit_code") not in (0, 1):
        return False
    if receipt.get("candidate_exit_code") not in (0, 1) or proof.get("exit_code") != 1:
        return False
    try:
        paths = [Path(receipt[key]) for key in ("base_output", "full_output")] + [Path(proof["output"])]
        owned = workspace.resolve() / ".autocode"
        if len(set(paths)) != 3 or any(not path.resolve().is_relative_to(owned) for path in paths):
            return False
        logs = [
            _read_log(path, digest)
            for path, digest in zip(
                paths, [receipt["base_output_sha256"], receipt["full_output_sha256"], proof["sha256"]], strict=True
            )
        ]
        if not all(_fresh_finished(log) for log in logs):
            return False
        control = (logs[2] or b"").decode("utf-8", errors="replace")
        candidate = logs[1] or b""
        rows = proof["controls"]
        scope = sphinx_scope(receipt["command"], proof["documents"])
        control_root = Path(proof["control_root"])
        if not control_root.is_absolute() or not control_root.is_relative_to(owned / "scratch"):
            return False
        if not rows or {row["document"] for row in rows} != set(scope):
            return False
        if any(count != 1 for count in Counter(row["target"] for row in rows).values()):
            return False
        for row in rows:
            marker, doc, role = row["target"], row["document"], row["role"]
            if (
                not re.fullmatch(r"autocode_reference_control_[a-f0-9]{32}", marker)
                or marker.encode() in candidate
                or marker.encode() in (logs[0] or b"")
            ):
                return False
            prefix = (
                r"undefined label:\s+['\"]?" if role == "ref" else rf"py:{re.escape(role)} reference target not found: "
            )
            warning = re.compile(
                rf"^{re.escape(str(control_root / doc))}:\d+: WARNING: {prefix}{marker}['\"]?(?:\s+\[[^\]]+\])?$",
                re.MULTILINE,
            )
            if len(warning.findall(control)) != 1:
                return False
        return True
    except (KeyError, OSError, TypeError, ValueError, RuntimeError):
        return False


def sphinx_cache_reused(receipt: dict) -> bool:
    """Identify affirmative reuse in either retained Sphinx build.

    False means reuse was not established. It does not certify freshness,
    source coverage, or the absence of unresolved references.
    """
    if not _sphinx(receipt.get("command")):
        return False
    for field in ("base_output", "full_output"):
        try:
            log = _read_log(Path(receipt[field]), receipt.get(field + "_sha256"))
        except (KeyError, OSError, TypeError, ValueError):
            continue
        if log and _SPHINX_START.search(log) and _SPHINX_CACHED.search(log):
            return True
    return False
