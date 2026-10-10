"""Compatibility name for the Autopilot controller."""

try:
    from .autopilot import *  # noqa: F403 - compatibility re-export
except ImportError:
    from autopilot import *  # noqa: F403 - compatibility re-export


if __name__ == "__main__":
    raise SystemExit(cli())  # noqa: F405 - provided by the compatibility star import
