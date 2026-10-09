#!/usr/bin/env python3
"""Pass-through wrapper around the repo's offline fake Codex provider.

Two hooks the base fixture lacks:

1. Hold a stage in flight for a configurable window
   (AUTOCODE_FIXTURE_SLOW_STAGE / AUTOCODE_FIXTURE_SLOW_SECONDS) so crash
   scenarios can SIGKILL the runner while a build stage is genuinely underway.

2. When cart.py is present and the stage is a validation stage, delegate to
   cart_fixture.py — a spec-aware Validator that executes cart behaviors,
   checks requirement coverage, and refuses builder-claimed evidence. This is
   what the mutation scenarios (09/10/11) score. Without it the canned
   fixture only judges greet.py and every cart mutant would be invisible.

With neither hook active this is pure pass-through.

`codex login status` must not touch stdin (the base fixture exits before
reading it, and the runner invokes it with the chat stdin inherited —
slurping it here would starve the chat loop).
"""
import json
import os
import runpy
import sys
import tempfile
import time
from pathlib import Path

here = Path(__file__).resolve().parent
real = str(here / "fake_codex_real.py")

if sys.argv[1:] == ["login", "status"]:
    runpy.run_path(real, run_name="__main__")  # prints status, exits; stdin untouched
    raise SystemExit(0)

buffer = sys.stdin.buffer.read()
stage = None
data = {}
# The prompt prose also contains the phrase "CURRENT HANDOFF DATA"; the real
# marker is followed by a newline and the JSON packet. Match the base fixture.
parts = buffer.split(b"CURRENT HANDOFF DATA\n", 1)
if len(parts) == 2:
    try:
        data = json.loads(parts[1])
        stage = data.get("stage")
    except Exception:
        stage = None

slow = os.environ.get("AUTOCODE_FIXTURE_SLOW_STAGE")
if slow and stage == slow:
    time.sleep(float(os.environ.get("AUTOCODE_FIXTURE_SLOW_SECONDS", "45")))

# Cart-aware Validator: only for validation stages, only when cart.py is the
# artifact under test. Planning/Builder stages fall through to the stock
# fixture (which writes greet.py and never touches cart.py).
if (
    stage in ("sol", "astra_checkpoint")
    and not data.get("report_repair")
    and Path("cart.py").is_file()
    and (here / "cart_fixture.py").is_file()
):
    handle, path = tempfile.mkstemp()
    os.write(handle, buffer)
    os.close(handle)
    os.dup2(os.open(path, os.O_RDONLY), 0)
    sys.path.insert(0, str(here))
    import cart_fixture
    cart_fixture.main()
    raise SystemExit(0)

# Re-feed the consumed stdin to the real fixture, then execute it in-process.
handle, path = tempfile.mkstemp()
os.write(handle, buffer)
os.close(handle)
os.dup2(os.open(path, os.O_RDONLY), 0)
sys.path.insert(0, str(here))
runpy.run_path(real, run_name="__main__")
