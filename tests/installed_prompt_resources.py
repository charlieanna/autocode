"""Load every packaged prompt outside the checkout and bind it to source bytes."""

import sys
from pathlib import Path

from autocode_cli import autocode_prompts as prompts


def main():
    source = Path(__file__).resolve().parents[1] / "tools/prompts"
    installed = Path(prompts.__file__).resolve()
    if not installed.is_relative_to(Path(sys.prefix).resolve()):
        raise SystemExit(f"Expected installed prompt loader under {sys.prefix}, found {installed}")
    if Path.cwd().resolve().is_relative_to(source.parent.parent):
        raise SystemExit("Run installed prompt smoke outside the checkout")
    expected = {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*.md")}
    if not expected or dict(prompts.CONTENTS) != expected:
        raise SystemExit("Installed prompt resources do not match the reviewed checkout")
    if prompts.content_hash(expected) != prompts.HASH:
        raise SystemExit("Installed prompt identity does not bind the reviewed bytes")
    print(f"Installed {len(expected)} prompt resources match source bytes and identity")


if __name__ == "__main__":
    main()
