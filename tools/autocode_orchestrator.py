"""Compatibility name for the Autopilot controller."""

try:
    from .autopilot import *  # noqa: F403 - compatibility re-export
except ImportError:
    from autopilot import *  # noqa: F403 - compatibility re-export


if __name__ == "__main__":
    from autopilot import cli  # explicit for the script entry; the star import above is the compat API
    raise SystemExit(cli())
