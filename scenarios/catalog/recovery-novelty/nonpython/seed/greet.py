"""Unchanged test adapter; the product implementation is JavaScript."""

import subprocess
import sys

raise SystemExit(subprocess.call(["node", "greet.js", *sys.argv[1:]]))
