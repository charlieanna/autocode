"""One parent-interpreter policy for captured bytes and their reusable identity.

Standard library only: generic process capture and provider preflights can both
use this lower-layer helper without depending on each other.
"""

from __future__ import annotations

import locale
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Decoding:
    encoding: str
    errors: str | None

    def identity(self) -> tuple[str, str | None]:
        # None permits the existing replacement fallback on truncated timeouts;
        # explicit strict must retain its refusal even for partial output.
        return self.encoding, self.errors

    def decode(self, raw: bytes, *, lossy: bool = False) -> str:
        try:
            text = raw.decode(self.encoding, self.errors or "strict")
        except UnicodeDecodeError:
            if not lossy or self.errors is not None:
                raise
            text = raw.decode(self.encoding, "replace")
        return text.replace("\r\n", "\n").replace("\r", "\n")


def resolve(encoding: str | None = None, errors: str | None = None) -> Decoding:
    """Snapshot the effective codec without validating or canonicalizing caller names."""
    codec = encoding or ("utf-8" if sys.flags.utf8_mode else locale.getencoding())
    return Decoding(codec, errors)


def validate_text_aliases(text, universal_newlines) -> None:
    """Reject contradictory subprocess aliases before a reusable answer can bypass validation."""
    if text is not None and universal_newlines is not None and bool(text) != bool(universal_newlines):
        raise subprocess.SubprocessError(
            "Cannot disambiguate when both text and universal_newlines are supplied but different. "
            "Pass one or the other."
        )
