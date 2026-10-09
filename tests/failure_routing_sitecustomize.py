"""Test-owned crash barrier after a real Investigator attempt snapshot publication."""
import os
import time
from pathlib import Path

_replace = os.replace


def replace(source, target, *args, **kwargs):
    result = _replace(source, target, *args, **kwargs)
    boundary = os.environ.get('FAILURE_ROUTING_BOUNDARY')
    if boundary and str(target).endswith('/stuck-investigation-01.after.json'):
        Path(boundary).write_text(str(os.getpid()))
        deadline = time.monotonic() + 30
        while not Path(boundary + '.release').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        if not Path(boundary + '.release').exists():
            raise RuntimeError('Test-owned Investigator publication barrier was not released')
    return result


os.replace = replace
