"""Stdlib-first CLI entry point that serves eligible read-only polls from the warm host (#3618)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

try:
    from . import autocode_source_host as source_host
except ImportError:
    import autocode_source_host as source_host


def _autocode_module():
    here = Path(__file__).resolve().parent
    if __package__:
        return importlib.import_module(f"{__package__}.autocode")
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        from . import autocode
    except ImportError:
        import autocode
    return autocode


def run_in_host() -> int:
    """Execute one CLI request inside the warm host without exiting the host process."""
    runner = _autocode_module()
    try:
        return runner.main()
    except (RuntimeError, ValueError, OSError) as error:
        print(f"autocode: {error}", file=sys.stderr)
        return 2


def cli(unit=None):
    source_host.drop_git_repository_variables()
    if unit is None:
        served = source_host.cli_fast_path(sys.argv[1:])
        if served is not None:
            return served
    return _autocode_module().cli(unit=unit)


if __name__ == "__main__":
    raise SystemExit(cli())
