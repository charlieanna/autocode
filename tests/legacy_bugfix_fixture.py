"""Emulate a historical launch for retained Investigator compatibility tests.

Only the initial configuration boundary omits the new policy marker, as an old
runtime did. The real controller creates the run; resumes use its saved policy.
No checkpoint is fabricated or edited and no production option is added.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND = (sys.executable, str(Path(__file__).resolve()))


def install(runner):
    configure = runner.configure

    def legacy_configuration(args, state, **kwargs):
        settings = configure(args, state, **kwargs)
        if not args.run_dir:
            settings.pop("implementation_flow", None)
        return settings

    runner.configure = legacy_configuration


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "tools"))
    import autocode

    install(autocode)
    raise SystemExit(autocode.cli())
