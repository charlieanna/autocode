"""Move bulky historical material to an immutable, retrievable handoff artifact."""
import copy
import json
from pathlib import Path


def compact(base, state_path):
    try:
        from . import autocode_util as util
    except ImportError:
        import autocode_util as util
    result = copy.deepcopy(base)
    moved = {}
    # Never remove requirements, saved answers, human decisions or current findings.
    for key in ('evidence_locations', 'deferred_backlog',
                'preserved_checkpoint', 'prior_validation_reports', 'source_snapshot'):
        value = result.get(key)
        if not isinstance(value, (dict, list)) or len(json.dumps(value).encode()) <= 1200:
            continue
        moved[key] = value
        if key == 'source_snapshot':
            result[key] = {k: value[k] for k in ('revision', 'head') if k in value}
        elif isinstance(value, list):
            result[key] = value[-3:]
        else:
            result.pop(key, None)
    _archive_validation_replay(result, moved)
    _drop_restated_contract_criteria(result)
    if moved:
        archive = Path(state_path).parent / 'context' / (util.digest(moved) + '.json')
        if not archive.exists():
            util.atomic_json(archive, moved)
        elif util.read(archive) != moved:
            raise ValueError('Saved context artifact changed: ' + str(archive))
        result['context_artifact'] = {'path': str(archive), 'sha256': util.file_hash(archive),
                                      'fields': {key: {'total': len(value) if isinstance(value, (dict, list)) else None}
                                                 for key, value in moved.items()},
                                      'instruction': 'Earlier material is preserved here. Retrieve relevant fields before relying on historical evidence. This index does not change requirements or authorize skipping checks.'}
    return result, list(moved)


def _archive_validation_replay(result, moved):
    """The replay record restates validation.checks with runner provenance; keep its verdict."""
    validation = result.get('validation')
    replay = validation.get('check_replay') if isinstance(validation, dict) else None
    if not isinstance(replay, dict) or len(json.dumps(replay).encode()) <= 1200:
        return
    moved['validation_check_replay'] = replay
    validation['check_replay'] = {k: replay[k] for k in ('verdict', 'source_revision') if k in replay}


def _drop_restated_contract_criteria(result):
    """The contract body repeats the packet's acceptance criteria without their AC ids."""
    contract = result.get('goal_contract')
    body = contract.get('body') if isinstance(contract, dict) else None
    if not isinstance(body, dict):
        return
    packet_criteria = _criteria_texts(result.get('acceptance_criteria'))
    if packet_criteria and _criteria_texts(body.get('acceptance_criteria')) == packet_criteria:
        body.pop('acceptance_criteria')


def _criteria_texts(items):
    if not isinstance(items, list):
        return None
    return [item.get('criterion') if isinstance(item, dict) else item for item in items]
