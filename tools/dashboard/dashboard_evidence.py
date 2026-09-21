"""Bounded reads of saved diffs and explicitly recorded changed project files."""
import json
import os
from pathlib import Path
import stat

MAX_DIFF_BYTES = 192 * 1024


def stage_evidence(run, index=None, workspace=None, file_index=None):
    run = Path(run).resolve(strict=True)
    state = json.loads((run / 'state.json').read_text())
    stages = state.get('stages', [])
    if not isinstance(stages, list):
        stages = []
    if index is None:
        if file_index is not None:
            raise ValueError('Select a stage before a file')
        return {'stages': [
            {'index': i, 'stage': row.get('stage'), 'role': row.get('role'),
             'finished_at': row.get('finished_at'), 'changed_files': row.get('changed_files', []),
             'source_revision': row.get('source_revision'), 'rejected': bool(row.get('rejected')),
             'interrupted': bool(row.get('interrupted')), 'has_diff': bool(row.get('diff_ref'))}
            for i, row in enumerate(stages) if isinstance(row, dict) and row.get('diff_ref')
            and (row.get('changed_files') or 'changed_files' not in row)
        ]}
    if type(index) is not int or index < 0 or index >= len(stages):
        raise ValueError('Choose an existing saved stage')
    row = stages[index]
    if file_index is not None:
        files = row.get('changed_files', []) if isinstance(row, dict) else []
        if workspace is None or not isinstance(files, list) or type(file_index) is not int or not 0 <= file_index < len(files):
            raise ValueError('Choose a file recorded by this stage')
        ref = files[file_index]
        if not isinstance(ref, str) or any(part in ('.git', '.autocode') for part in Path(ref).parts):
            raise ValueError('Only recorded project files can be viewed')
        result = read_task_file(Path(workspace).resolve(strict=True), ref)
        return {**result, 'index': index, 'file': ref, 'current_contents': True}
    ref = row.get('diff_ref') if isinstance(row, dict) else None
    if not isinstance(ref, str) or not ref:
        raise ValueError('This stage has no saved diff')
    return {**read_task_file(run, ref), 'index': index}


def read_task_file(root, ref):
    candidate = Path(ref)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        parts = candidate.relative_to(root).parts
    except ValueError as error:
        raise ValueError('Saved diff must stay inside this task') from error
    if not parts or any(part in ('.', '..') for part in parts):
        raise ValueError('Invalid saved diff path')
    # Walk relative to directory descriptors: reject symlinks at every level,
    # including a parent exchanged while a worker writes a checkpoint.
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Saved diff is not a regular file')
            raw = stream.read(MAX_DIFF_BYTES + 1)
    except OSError as error:
        raise ValueError('Saved diff is unavailable or contains a symlink') from error
    finally:
        os.close(fd)
    if b'\x00' in raw:
        return {'text': '', 'binary': True, 'truncated': len(raw) > MAX_DIFF_BYTES}
    return {'text': raw[:MAX_DIFF_BYTES].decode('utf8', errors='replace'),
            'truncated': len(raw) > MAX_DIFF_BYTES}
