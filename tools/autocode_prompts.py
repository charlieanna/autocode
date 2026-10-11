"""Import-cached, versioned prompt bytes shared by every model-facing layer.

Only assembly and state stay in Python. The hash binds names as well as contents,
so a rename cannot silently reuse a prompt identity. Resources are loaded once;
a long-running process keeps one coherent prompt set even if files change.
Dynamic fragments use ordinary str.format placeholders; the assembling module
supplies only the runtime values.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

_ROOT = Path(__file__).with_name("prompts")
if not _ROOT.is_dir():
    raise FileNotFoundError(f"Prompt resources are missing: {_ROOT}")

CONTENTS = MappingProxyType(
    {path.relative_to(_ROOT).as_posix(): path.read_bytes() for path in sorted(_ROOT.rglob("*.md"))}
)
if not CONTENTS:
    raise ValueError(f"Prompt resource set is empty: {_ROOT}")

FILES = tuple(CONTENTS)
_TEXT = MappingProxyType({name: value.decode("utf-8") for name, value in CONTENTS.items()})


def content_hash(contents: Mapping[str, bytes]) -> str:
    """Hash exact named bytes with explicit lengths and a versioned domain."""
    digest = hashlib.sha256(b"autocode-prompts-v1\x00")
    for name, value in sorted(contents.items()):
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


HASH = content_hash(CONTENTS)


def get(name: str) -> str:
    """Return a cached resource without filesystem reads or newline rewriting."""
    return _TEXT[name]
