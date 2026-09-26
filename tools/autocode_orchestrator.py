"""Compatibility name for the Autopilot controller."""
try:
    from .autopilot import *
except ImportError:
    from autopilot import *


if __name__ == "__main__":
    raise SystemExit(cli())
