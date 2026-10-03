"""Immutable exact bytes and per-operation display measurements, never proof reuse."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


def store_root(value=None):
    workspace = Path.cwd().resolve()
    root = Path(value or workspace / '.autocode/output').resolve()
    if not root.is_relative_to(workspace / '.autocode'):
        raise ValueError('Output store must be inside this workspace\'s .autocode directory')
    return root


def digest(data):
    return hashlib.sha256(data).hexdigest()


def directory(root, name):
    path = Path(root) / name
    if path.is_symlink() or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Exact output directory must not be a symlink')
    return path


def retrieve(root, sha256):
    if not isinstance(sha256, str) or not re.fullmatch('[0-9a-f]{64}', sha256):
        raise ValueError('An exact SHA-256 artifact identity is required')
    path = directory(root, 'blobs') / (sha256 + '.bin')
    if path.is_symlink():
        raise ValueError('Exact output artifact must not be a symlink')
    data = path.read_bytes()
    if digest(data) != sha256:
        raise ValueError('Exact output artifact hash changed')
    return data


def retain(root, data):
    sha256 = digest(data)
    folder = directory(root, 'blobs')
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (sha256 + '.bin')
    fd, temporary = tempfile.mkstemp(prefix='.capture-', dir=folder)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            pass
        if retrieve(root, sha256) != data:
            raise ValueError('Exact output artifact differs from original bytes')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'sha256': sha256, 'bytes': len(data), 'path': str(target)}


def record(root, *, operation, raw_bytes, displayed_bytes, mode, omitted=0, **extra):
    """Best-effort observation must never interrupt a healthy command or retrieval."""
    row = {'version': 1, 'operation': operation, 'mode': mode, 'raw_bytes': raw_bytes,
           'displayed_bytes': displayed_bytes, 'omitted_sections': omitted,
           'attempt': os.environ.get('AUTOCODE_OUTPUT_ATTEMPT'), **extra}
    try:
        folder = directory(root, 'operations')
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / (uuid.uuid4().hex + '.json')).open('x') as stream:
            json.dump(row, stream, sort_keys=True)
    except (OSError, ValueError, TypeError):
        return False
    return True


def summary(root, *, attempt=None):
    rows = []
    unreadable = 0
    for path in directory(root, 'operations').glob('*.json'):
        try:
            if path.is_symlink():
                raise ValueError('invalid measurement path')
            row = json.loads(path.read_text())
            if not isinstance(row, dict) or row.get('operation') not in ('read', 'retrieve', 'capture'):
                raise ValueError('invalid measurement')
            if attempt is None or row.get('attempt') == attempt:
                if not all(type(row.get(key)) is int and row[key] >= 0 for key in ('raw_bytes', 'displayed_bytes')):
                    raise ValueError('invalid measurement')
                rows.append(row)
        except (OSError, ValueError, TypeError):
            unreadable += 1
    raw = sum(row['raw_bytes'] for row in rows)
    shown = sum(row['displayed_bytes'] for row in rows)
    return {'operations': len(rows), 'raw_bytes': raw, 'displayed_bytes': shown,
            'byte_difference': raw - shown, 'retrieval_calls': sum(row['operation'] == 'retrieve' for row in rows),
            'unreadable_records': unreadable, 'tokens': None, 'cost_savings_usd': None,
            'measurement': 'Tool display bytes including metadata; not request tokens or billing savings'}
