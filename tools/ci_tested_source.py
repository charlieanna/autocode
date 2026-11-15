"""The immutable checkout CI tested, distinct from a pull request's selection base.

Capture this before the suite. Attribution verifies the same checkout afterward;
an integration merge compares with its observed first parent only when its second
parent is the explicitly expected PR head. This record grants no passing result.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import autocode_util as util

OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
UNAVAILABLE = "Git source observation is unavailable."
CHANGED = "The tested checkout changed after its pre-suite capture."


@dataclass(frozen=True)
class SourceContext:
    expected_head: str
    event_base: str
    tested_revision: str | None
    parents: tuple[str, ...]
    tracked_clean: bool
    error: str | None = None

    def __post_init__(self):
        ids = (self.expected_head, self.event_base, *self.parents)
        if self.tested_revision is not None:
            ids += (self.tested_revision,)
        if any(not isinstance(value, str) or not OID.fullmatch(value) for value in ids):
            raise ValueError("source identities must be full commit OIDs")
        if type(self.tracked_clean) is not bool or self.error not in (None, UNAVAILABLE, CHANGED):
            raise ValueError("invalid source observation")
        if self.tested_revision is None and (self.parents or self.tracked_clean or self.error != UNAVAILABLE):
            raise ValueError("missing tested revision")

    def resolution(self) -> tuple[str | None, str, str | None]:
        """One comparison rule used by both capture and reporting."""
        if self.error:
            return None, "unresolved", self.error
        if not self.tracked_clean:
            return None, "unresolved", "The tested checkout has tracked changes."
        if len(self.parents) > 2:
            return None, "unresolved", "The tested checkout has more than two parents."
        if self.tested_revision == self.expected_head:
            return self.event_base, "direct_head", None
        if len(self.parents) == 2 and self.parents[1] == self.expected_head and self.parents[0] != self.expected_head:
            return self.parents[0], "integration_first_parent", None
        return (
            None,
            "unresolved",
            "The tested checkout does not bind the expected PR head in the required parent order.",
        )

    def as_dict(self) -> dict:
        base, resolution, reason = self.resolution()
        return {
            "version": 1,
            "expected_head": self.expected_head,
            "event_base": self.event_base,
            "tested_revision": self.tested_revision,
            "parents": list(self.parents),
            "tracked_clean": self.tracked_clean,
            "error": self.error,
            "comparison_base": base,
            "resolution": resolution,
            "reason": reason,
        }

    @classmethod
    def load(cls, path: Path, *, expected_head: str, event_base: str) -> SourceContext:
        record = json.loads(path.read_text())
        if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1:
            raise ValueError("invalid source context")
        parents = record.get("parents")
        if not isinstance(parents, list) or len(parents) > 64:
            raise ValueError("invalid source parents")
        head, base, clean = record.get("expected_head"), record.get("event_base"), record.get("tracked_clean")
        if not isinstance(head, str) or not isinstance(base, str) or not isinstance(clean, bool):
            raise ValueError("invalid source observation types")
        context = cls(
            head,
            base,
            record.get("tested_revision"),
            tuple(parents),
            clean,
            record.get("error"),
        )
        if record != context.as_dict() or context.expected_head != expected_head or context.event_base != event_base:
            raise ValueError("source context does not match the explicit invocation")
        return context

    def check_current(self, repo: Path) -> SourceContext:
        if self.resolution()[0] is None:
            return self
        current = capture(self.expected_head, self.event_base, repo=repo)
        if current != self:
            return replace(self, error=CHANGED)
        return self

    def markdown(self) -> str:
        base, _, reason = self.resolution()
        parents = ", ".join(f"`{parent}`" for parent in self.parents) or "none observed"
        return "\n".join(
            [
                "### Tested source",
                f"- Tested revision: `{self.tested_revision or 'unavailable'}`; ordered parents: {parents}.",
                f"- Expected PR head: `{self.expected_head}`.",
                f"- Event selection base: `{self.event_base}`.",
                f"- Comparison base: `{base}`." if base else f"- Comparison unresolved: {reason}",
            ]
        )


def capture(expected_head: str, event_base: str, *, repo: Path = Path(".")) -> SourceContext:
    """Observe source without changing checkout or consulting a mutable branch."""
    try:
        environment = util.git_environment()
        row = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        ).stdout
        if len(row) > 8192:
            raise ValueError("oversized source observation")
        revision, *parents = row.split()
        clean = subprocess.run(
            ["git", "diff-index", "--quiet", "HEAD", "--"],
            cwd=repo,
            capture_output=True,
            timeout=10,
            env=environment,
        ).returncode
        if clean not in (0, 1):
            raise ValueError("tracked source observation unavailable")
        return SourceContext(expected_head, event_base, revision, tuple(parents), clean == 0)
    except (OSError, subprocess.SubprocessError, ValueError):
        return SourceContext(expected_head, event_base, None, (), False, UNAVAILABLE)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--event-base", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        context = capture(args.expected_head, args.event_base)
        args.out.write_text(json.dumps(context.as_dict(), indent=2) + "\n")
        print(context.markdown())
    except (OSError, ValueError):
        print("Tested-source capture is unavailable; failure attribution will remain unresolved.", file=sys.stderr)
    return 0  # informational; this must not prevent the suite itself from running


if __name__ == "__main__":
    raise SystemExit(main())
