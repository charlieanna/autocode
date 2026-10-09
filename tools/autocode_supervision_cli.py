"""Internal inherited-pipe admission for a black-box qualification harness.

Only the parent owns the pipe writer. This module never reads run state or starts
provider work. The CLI uses the returned argv for its existing dispatch path.
"""
import json
import math
import os
import select
import stat
import time
from contextlib import contextmanager
from pathlib import Path

try:
    from . import autocode_supervision as supervision
except ImportError:
    import autocode_supervision as supervision


FLAG = '--owner-lifeline-fd'
MAX_DECLARATION_BYTES = 4096


def declaration(fd):
    if isinstance(fd, bool) or not isinstance(fd, int) or fd <= 2:
        raise ValueError('Owner lifeline must be an inherited pipe descriptor')
    if not stat.S_ISFIFO(os.fstat(fd).st_mode):
        raise ValueError('Owner lifeline must be an inherited pipe')
    data = bytearray()
    read_deadline = time.monotonic() + 5
    while len(data) <= MAX_DECLARATION_BYTES:
        remaining = read_deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise ValueError('Owner lifeline declaration was not received')
        chunk = os.read(fd, 1)
        if not chunk:
            raise ValueError('Owner disappeared before CLI admission')
        if chunk == b'\n':
            break
        data.extend(chunk)
    else:
        raise ValueError('Owner lifeline declaration exceeded its limit')
    value = json.loads(data)
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ValueError('Owner lifeline schema is invalid')
    owner = value.get('owner')
    if (not isinstance(owner, dict) or owner.get('pid') != os.getppid()
            or isinstance(owner.get('pid'), bool)
            or not isinstance(owner.get('birth_identity'), (int, float))
            or isinstance(owner.get('birth_identity'), bool)):
        raise ValueError('Owner lifeline does not identify this CLI parent')
    nonce = value.get('nonce')
    if not isinstance(nonce, str) or len(nonce) != 32 or any(c not in '0123456789abcdef' for c in nonce):
        raise ValueError('Owner lifeline nonce is invalid')
    receipt = value.get('receipt')
    if not isinstance(receipt, str) or not Path(receipt).is_absolute():
        raise ValueError('Owner lifeline receipt must have an absolute path')
    for name in ('deadline', 'timeout_seconds'):
        number = value.get(name)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError('Owner lifeline deadline is invalid')
    remaining = min(value['timeout_seconds'], value['deadline'] - time.monotonic())
    if remaining <= 0:
        raise ValueError('Owner lifeline deadline has expired')
    return value, remaining


@contextmanager
def guard(argv):
    argv = list(argv)
    if FLAG not in argv:
        yield argv
        return
    at = argv.index(FLAG)
    if argv.count(FLAG) != 1 or at + 1 == len(argv):
        raise ValueError('One inherited owner lifeline is required')
    try:
        fd = int(argv[at + 1])
    except ValueError as error:
        raise ValueError('Owner lifeline descriptor is invalid') from error
    if fd <= 2:
        raise ValueError('Owner lifeline must be an inherited pipe descriptor')
    del argv[at:at + 2]
    try:
        packet, remaining = declaration(fd)
        with supervision.protect_owner(fd, receipt_path=packet['receipt'], timeout=remaining,
                                       owner_identity=packet['owner'], nonce=packet['nonce']):
            yield argv
    finally:
        os.close(fd)
