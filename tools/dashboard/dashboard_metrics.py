"""Opt-in, read-only project counters. Not runner state or completion authority.

`.autocode/dashboard.json` declares a bounded projection of an existing JSON
artifact. Only the compact projection is cached; raw artifacts never reach HTTP.
"""
from collections import OrderedDict
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

MAX_SOURCE_BYTES = 256 * 1024 * 1024
_cache = OrderedDict()
_lock = threading.Lock()


def contained_file(workspace, raw):
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
        raise ValueError('Metric sources must be workspace-relative files')
    path = (workspace / raw).resolve(strict=True)
    path.relative_to(workspace.resolve(strict=True))
    if not path.is_file():
        raise ValueError('Metric source is not a file')
    return path


def pointer(document, raw):
    if raw == '':
        return document
    if not isinstance(raw, str) or not raw.startswith('/'):
        raise ValueError('groups_pointer must be a JSON pointer')
    for token in raw[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        document = document[token]
    return document


def count(value):
    if isinstance(value, list):
        return len(value)
    if type(value) is int and value >= 0:
        return value
    raise ValueError('Counters must be nonnegative integers or lists')


def project_metrics(workspace, run):
    workspace, run = Path(workspace), Path(run)
    config = workspace / '.autocode/dashboard.json'
    if not config.exists():
        return []
    try:
        config = contained_file(workspace, '.autocode/dashboard.json')
        if config.stat().st_size > 65536:
            raise ValueError('Dashboard configuration is too large')
        settings = json.loads(config.read_text())
        if settings.get('version') != 1 or not isinstance(settings.get('metrics'), list) or len(settings['metrics']) > 8:
            raise ValueError('Expected dashboard version 1 with at most eight metrics')
    except (OSError, ValueError, TypeError, AttributeError):
        return [{'label': 'Project metrics', 'error': 'Dashboard metric configuration is unavailable or invalid'}]
    results = []
    for spec in settings['metrics']:
        if not isinstance(spec, dict):
            results.append({'label': 'Project metrics', 'error': 'Invalid metric definition'})
            continue
        # A run-specific artifact must never be presented as another task's progress.
        if not isinstance(spec.get('runs'), list) or run.name not in spec['runs']:
            continue
        row = {key: str(spec.get(key, ''))[:2000] for key in ('id', 'label', 'description', 'source')}
        try:
            path = contained_file(workspace, spec.get('source'))
            stat = path.stat()
            if stat.st_size > MAX_SOURCE_BYTES:
                raise ValueError('Metric artifact exceeds the 256 MiB read limit')
            key = (str(path), stat.st_mtime_ns, stat.st_size, json.dumps(spec, sort_keys=True))
            with _lock:
                if key not in _cache:
                    raw = json.loads(path.read_text())
                    groups = pointer(raw, spec.get('groups_pointer'))
                    if not isinstance(groups, dict) or not groups or len(groups) > 250:
                        raise ValueError('Metric groups must be a nonempty object with at most 250 entries')
                    areas = []
                    for name, group in groups.items():
                        done = count(group[spec['completed_field']])
                        total = count(group[spec['total_field']])
                        if done > total:
                            raise ValueError('Completed count exceeds total')
                        areas.append({'name': name, 'done': done, 'total': total})
                    # A concurrent rewrite cannot produce an apparently current snapshot.
                    after = path.stat()
                    if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                        raise ValueError('Metric artifact changed during reading; retry')
                    _cache[key] = {'areas': areas, 'done': sum(a['done'] for a in areas),
                                   'total': sum(a['total'] for a in areas),
                                   'updated': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}
                    while len(_cache) > 16:
                        _cache.popitem(last=False)
                row.update(_cache[key])
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            row['error'] = 'Metric artifact unavailable or invalid; no progress is inferred. Check the declared source and fields.'
        results.append(row)
    return results
