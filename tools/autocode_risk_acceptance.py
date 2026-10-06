"""Pure binding of two explicit Python lifecycle APIs to human promises.

The caller authenticates human sources and the retained initial public targets.
A Reviewer selects only a declaration, criteria and an inventoried module. API
names, lifecycle protocol and expectations always come from the human source.
No files, processes, state, controller or model are imported or launched here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import PurePosixPath
import re

HUMAN_KINDS = frozenset({'task', 'conversation_user', 'user_answer', 'user_feedback',
                         'user_intervention', 'user_cli_edit'})
PROTOCOLS = frozenset({'lease_queue_lifecycle_v1', 'transactional_outbox_lifecycle_v1'})
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_]*\Z')
_MODULE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*\Z')
_CONSTRUCTOR = re.compile(r'\b(?P<name>[A-Z][A-Za-z0-9_]*)\s*\(\s*path\s*\)')
_IMPORT = re.compile(r'\bfrom\s+(?P<module>[A-Za-z][A-Za-z0-9_.]*)\s+import\s+'
                     r'(?P<name>[A-Za-z][A-Za-z0-9_]*)(?:\s+as\s+(?P<alias>[A-Za-z][A-Za-z0-9_]*))?')
_METHODS = {
    'lease_queue_lifecycle_v1': {key: key for key in ('enqueue', 'claim', 'ack', 'nack', 'pending')},
    'transactional_outbox_lifecycle_v1': {key: key for key in ('create_order', 'orders', 'pending', 'publish')},
}
_PROMISES = {
    'lease_queue_lifecycle_v1': ['durable_restart', 'fresh_claim_token', 'exact_deadline_reclaim',
                                 'stale_token_fencing', 'completed_idempotency'],
    'transactional_outbox_lifecycle_v1': ['durable_order_event', 'ack_after_callback',
                                         'stable_retry_event_id', 'durable_reopen', 'at_least_once_delivery'],
}
_DECLARATION_KEYS = frozenset({'id', 'source_id', 'source_sha256', 'source_span', 'source_quote',
                              'protocol', 'constructor', 'class_name', 'methods', 'promises',
                              'supported', 'missing', 'allowed_modules'})
_OBSERVATION_KEYS = frozenset({'declaration', 'proposal', 'protocol', 'target', 'criterion_ids',
                              'source_bindings', 'hash'})
_MANIFEST_KEYS = frozenset({'version', 'inventory_hash', 'public_targets_hash', 'observations', 'hash'})
PROPOSAL_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['declaration_id', 'criterion_ids', 'module'],
    'properties': {
        'declaration_id': {'type': 'string', 'minLength': 1},
        'criterion_ids': {'type': 'array', 'minItems': 1, 'uniqueItems': True,
                          'items': {'type': 'string', 'minLength': 1}},
        'module': {'type': 'string', 'pattern': r'^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)*$'},
    },
}
PROPOSALS_SCHEMA = {'type': 'array', 'items': PROPOSAL_SCHEMA}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def _text(value, label, *, limit=131072):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise ValueError(f'{label} needs bounded nonempty text')
    return value


def normalize_public_targets(rows):
    """Canonicalize caller-authenticated initial public Python module records."""
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError('Risk public targets need a bounded independent inventory')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'module', 'path', 'kind', 'sha256'}:
            raise ValueError('Risk targets need the exact original module, path, kind and content hash')
        module, path = row['module'], row['path']
        if (not isinstance(module, str) or not _MODULE.fullmatch(module)
                or not isinstance(path, str) or not path or len(path) > 512 or '\x00' in path
                or row['kind'] not in {'initial_public_import', 'initial_public_definition'}
                or not isinstance(row['sha256'], str) or not _HASH.fullmatch(row['sha256'])):
            raise ValueError('Risk target is not an authenticated original public module')
        name = module.replace('.', '/')
        permitted = {name + '.py', name + '/__init__.py', 'src/' + name + '.py',
                     'src/' + name + '/__init__.py'}
        parts = PurePosixPath(path).parts
        if (path not in permitted or PurePosixPath(path).is_absolute()
                or any(part in {'.', '..', 'tests', 'test', 'scenarios', '.autocode', '.git'} for part in parts)
                or module in seen):
            raise ValueError('Risk target path escapes, is private, or duplicates the original public module')
        seen.add(module)
        result.append(copy.deepcopy(row))
    return sorted(result, key=lambda row: row['module'])


def _sources(rows):
    if not isinstance(rows, list) or len(rows) > 128:
        raise ValueError('Risk source records need a bounded authenticated list')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'kind', 'text'}:
            raise ValueError('Risk source records need exact id, kind and text fields')
        ident = _text(row['id'], 'Risk source identity', limit=256)
        text = _text(row['text'], 'Risk source')
        if ident in seen:
            raise ValueError('Duplicate risk source identity')
        seen.add(ident)
        if not isinstance(row['kind'], str):
            raise ValueError('Risk source kind must identify its authenticated speaker')
        if row['kind'] in HUMAN_KINDS:
            result.append({'id': ident, 'kind': row['kind'], 'text': text})
    return result


def _signature(text, name, parameters):
    for match in re.finditer(r'\b' + re.escape(name) + r'\s*\(([^()]*)\)', text):
        args = [part.strip().split('=', 1)[0].strip() for part in match.group(1).split(',') if part.strip()]
        if args == list(parameters):
            return True
    return False


def _has(text, pattern):
    return bool(re.search(pattern, text, re.I))


def _facts(text, protocol):
    """Finite positive syntax; missing semantics never become guessed expectations."""
    if protocol == 'lease_queue_lifecycle_v1':
        return {
            'sqlite_storage': _has(text, r'\bSQLite\b'),
            'enqueue_api': _signature(text, 'enqueue', ('id', 'payload')),
            'claim_api': _signature(text, 'claim', ('now', 'lease_seconds')),
            'ack_api': _signature(text, 'ack', ('id', 'token', 'now')),
            'nack_api': _signature(text, 'nack', ('id', 'token', 'now')),
            'pending_api': _signature(text, 'pending', ()),
            'enqueue_idempotency': _has(text, r'returns\s+True\s+once.{0,100}?False.{0,60}?identical\s+replay')
                and _has(text, r'ValueError.{0,80}?conflict.{0,80}?after\s+completion'),
            'claim_fields': _has(text, r'dict\s+with\s+id\s*,\s*payload\s*,\s*token\s*,\s*deadline'),
            'earliest_unfinished': _has(text, r'earliest(?:-enqueued)?\s+unfinished\s+job'),
            'fresh_opaque_token': _has(text, r'token.{0,60}?fresh.{0,60}?opaque.{0,60}?every\s+claim'),
            'exact_deadline': _has(text, r'including\s+exactly\s+at\s+its\s+deadline'),
            'unexpired_ack': _has(text, r'ack\s*\([^)]*\).{0,100}?True\s+only.{0,60}?current\s+unexpired\s+lease')
                and _has(text, r'otherwise\s+False\s+with\s+no\s+effect'),
            'nack_release': _has(text, r'nack\s*\([^)]*\).{0,100}?(?:releases|release).{0,60}?current\s+unexpired\s+lease'),
            'pending_unfinished': _has(text, r'pending\s*\(\s*\).{0,60}?counts\s+unfinished\s+jobs'),
            'old_token_fencing': _has(text, r'old\s+tokens\s+must\s+never\s+complete\s+a\s+reassigned\s+job'),
            'durable_restart': _has(text, r'\bdurable\b') and _has(text, r'\bsurvive\s+restart\b'),
            'injected_time': _has(text, r'now\s+is\s+a\s+caller(?:-injected)?\s+nonnegative\s+integer')
                and _has(text, r'lease_seconds\s+is\s+a\s+positive\s+integer'),
        }
    return {
        'sqlite_storage': _has(text, r'\bSQLite\b'),
        'create_order_api': _signature(text, 'create_order', ('order_id', 'amount', 'key')),
        'orders_api': _signature(text, 'orders', ()),
        'pending_api': _signature(text, 'pending', ('limit',)),
        'publish_api': _signature(text, 'publish', ('sink', 'limit')),
        'create_idempotency': _has(text, r'returns\s+True\s+on\s+a\s+new\s+order.{0,100}?False.{0,80}?identical\s+idempotent\s+replay'),
        'atomic_durable_event': _has(text, r'atomically\s+commits\s+exactly\s+one\s+durable\s+event\s+together\s+with\s+the\s+order'),
        'positive_amount': _has(text, r'amount\s+is\s+a\s+positive\s+integer'),
        'orders_shape': _has(text, r'orders\s*\(\s*\).{0,60}?returns\s+a\s+dict\s+of\s+order\s+IDs\s+to\s+amounts'),
        'pending_order_fields': _has(text, r'pending\s*\([^)]*\).{0,100}?commit\s+order')
            and _has(text, r'event_id\s*\(\s*stable\s+opaque\s+string\s*\)\s*,\s*order_id\s*,\s*amount'),
        'positive_limit': _has(text, r'limit\s+must\s+be\s+a\s+positive\s+integer'),
        'ack_after_callback': _has(text, r'acknowledg(?:ing|es)\s+only\s+after\s+each\s+successful\s+callback'),
        'stable_retry_event_id': _has(text, r'retry\s+must\s+use\s+the\s+same\s+event_id'),
        'durable_reopen': _has(text, r'Reopening\s+a\s+store\s+preserves\s+orders\s+and\s+events'),
        'at_least_once_scope': _has(text, r'do\s+not\s+claim\s+exactly(?:-|\s)once\s+delivery'),
    }


def _constructor_import(text, constructor):
    candidates = [(match['module'], match['name']) for match in _IMPORT.finditer(text)
                  if (match['alias'] or match['name']) == constructor]
    if len(set(candidates)) > 1:
        raise ValueError('The human source names ambiguous public imports for this lifecycle API')
    return candidates[0] if candidates else (None, constructor)


def _negated_constructor(text, position):
    # A human's explicit exclusion is not an API promise. Earlier sentences or
    # lines cannot negate a later requested API declaration.
    start = max(text.rfind(mark, 0, position) for mark in ('.', ';', '\r', '\n')) + 1
    prefix = text[start:position]
    return _has(prefix, r"\b(?:do\s+not|don't|never)\s+(?:build|implement|create|require|provide)\b")


def _declaration_ranges(text, constructors):
    starts = []
    for index, constructor in enumerate(constructors):
        boundary = text.rfind('\n\n', 0, constructor.start())
        start = boundary + 2 if boundary >= 0 else 0
        if index and start < constructors[index - 1].end():
            previous = constructors[index - 1].end()
            separators = list(re.finditer(r'(?<=[.;])\s+|\r?\n', text[previous:constructor.start()]))
            start = previous + separators[-1].end() if separators else previous
        starts.append(start)
    ranges = []
    for index, constructor in enumerate(constructors):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        if index + 1 < len(starts):
            while end > constructor.end() and text[end - 1].isspace():
                end -= 1
        ranges.append((starts[index], end))
    return ranges


def inventory(sources, public_targets):
    """Recognize the two explicit source-declared API families, including gaps.

    No generic durability/security/performance wording creates a risk battery.
    Assistant/delegated records cannot originate a lifecycle promise. Empty
    target inventory is useful before capture; it never permits execution.
    """
    targets = normalize_public_targets(public_targets)
    modules = [row['module'] for row in targets]
    declarations = []
    for source in _sources(sources):
        text = source['text']
        constructors = list(_CONSTRUCTOR.finditer(text))
        for constructor, (start, end) in zip(constructors, _declaration_ranges(text, constructors)):
            if _negated_constructor(text, constructor.start()):
                continue
            quote = text[start:end]
            api = text[constructor.end():end]
            if _has(api, r'\b(?:enqueue|claim)\s*\(') and _has(quote, r'\b(?:durable|restart|old\s+tokens)\b'):
                protocol = 'lease_queue_lifecycle_v1'
            elif _has(api, r'\b(?:create_order|publish)\s*\(') and _has(quote, r'\b(?:outbox|durable\s+event|Reopening)\b'):
                protocol = 'transactional_outbox_lifecycle_v1'
            else:
                continue
            imported_module, class_name = _constructor_import(text, constructor['name'])
            allowed = modules if imported_module is None else [name for name in modules if name == imported_module]
            facts = _facts(quote, protocol)
            missing = sorted(key for key, present in facts.items() if not present)
            source_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
            identity = {'source_id': source['id'], 'source_sha256': source_hash, 'span': [start, end],
                        'protocol': protocol, 'constructor': constructor['name'], 'class_name': class_name}
            declarations.append({'id': 'risk-' + digest(identity), 'source_id': source['id'],
                'source_sha256': source_hash, 'source_span': [start, end], 'source_quote': quote,
                'protocol': protocol, 'constructor': constructor['name'], 'class_name': class_name,
                'methods': copy.deepcopy(_METHODS[protocol]), 'promises': list(_PROMISES[protocol]),
                'supported': not missing, 'missing': missing, 'allowed_modules': list(allowed)})
    return sorted(declarations, key=lambda row: row['id'])


def _proposal(row):
    if not isinstance(row, dict) or set(row) != {'declaration_id', 'criterion_ids', 'module'}:
        raise ValueError('Risk proposals select only a declaration, criteria and original public module')
    ident = _text(row['declaration_id'], 'Risk declaration identity', limit=256)
    module = row['module']
    criteria = row['criterion_ids']
    if (not isinstance(module, str) or not _MODULE.fullmatch(module) or not isinstance(criteria, list)
            or not criteria or len(criteria) > 256 or any(not isinstance(cid, str) or not cid.strip() or len(cid) > 256
                                                       or '\x00' in cid for cid in criteria)
            or len(set(criteria)) != len(criteria)):
        raise ValueError('Risk proposal needs unique nonempty criterion IDs and an original module name')
    return {'declaration_id': ident, 'criterion_ids': sorted(criteria), 'module': module}


def _observation(declaration, proposal):
    binding = {'source_id': declaration['source_id'], 'source_sha256': declaration['source_sha256'],
               'span': declaration['source_span'], 'quote': declaration['source_quote']}
    row = {'declaration': copy.deepcopy(declaration), 'proposal': copy.deepcopy(proposal),
           'protocol': declaration['protocol'], 'target': {'module': proposal['module'],
               'class_name': declaration['class_name'], 'methods': copy.deepcopy(declaration['methods'])},
           'criterion_ids': list(proposal['criterion_ids']), 'source_bindings': [copy.deepcopy(binding)]}
    return {**row, 'hash': digest(row)}


def bind(sources, proposals, *, public_targets, inactive=()):
    targets = normalize_public_targets(public_targets)
    declarations = inventory(sources, targets)
    known = {row['id']: row for row in declarations}
    inactive = list(inactive)
    if len(inactive) != len(set(inactive)) or not set(inactive) <= set(known):
        raise ValueError('Risk inactive declarations must name exact known authenticated amendments')
    active = {ident: row for ident, row in known.items() if ident not in inactive}
    if not isinstance(proposals, list) or len(proposals) > 256:
        raise ValueError('Risk proposals need a bounded independent Reviewer list')
    selected = [_proposal(row) for row in proposals]
    ids = [row['declaration_id'] for row in selected]
    if len(ids) != len(set(ids)) or set(ids) != set(active):
        raise ValueError('Every original lifecycle risk declaration requires exactly one independent observation')
    observations = []
    for proposal in sorted(selected, key=lambda row: row['declaration_id']):
        declaration = active[proposal['declaration_id']]
        if not declaration['supported']:
            raise ValueError('The source-declared lifecycle API is unsupported: ' + ', '.join(declaration['missing']))
        if proposal['module'] not in declaration['allowed_modules']:
            raise ValueError('Risk target module is absent from the authenticated original public inventory or source import')
        observations.append(_observation(declaration, proposal))
    manifest = {'version': 1, 'inventory_hash': digest(declarations), 'public_targets_hash': digest(targets),
                'observations': observations}
    return {**manifest, 'hash': digest(manifest)}


def normalize_observation(row):
    """Validate a full canonical observation; source reauthentication is verify's job."""
    if not isinstance(row, dict) or set(row) != _OBSERVATION_KEYS:
        raise ValueError('Invalid canonical lifecycle observation')
    declaration = row['declaration']
    if (not isinstance(declaration, dict) or set(declaration) != _DECLARATION_KEYS
            or declaration.get('protocol') not in PROTOCOLS
            or declaration.get('methods') != _METHODS[declaration['protocol']]
            or declaration.get('promises') != _PROMISES[declaration['protocol']]
            or declaration.get('supported') is not True or declaration.get('missing') != []
            or not isinstance(declaration.get('class_name'), str) or not _IDENTIFIER.fullmatch(declaration['class_name'])
            or not isinstance(declaration.get('constructor'), str) or not _IDENTIFIER.fullmatch(declaration['constructor'])
            or not isinstance(declaration.get('source_sha256'), str) or not _HASH.fullmatch(declaration['source_sha256'])):
        raise ValueError('Lifecycle observation changed its source-declared API or promises')
    _text(declaration.get('source_id'), 'Risk source identity', limit=256)
    _text(declaration.get('source_quote'), 'Risk source quote')
    span = declaration.get('source_span')
    if (not isinstance(span, list) or len(span) != 2 or any(type(value) is not int for value in span)
            or not 0 <= span[0] < span[1] or span[1] - span[0] != len(declaration['source_quote'])
            or not isinstance(declaration.get('allowed_modules'), list)
            or any(not isinstance(value, str) or not _MODULE.fullmatch(value) for value in declaration['allowed_modules'])):
        raise ValueError('Lifecycle observation has invalid human source coordinates or modules')
    proposal = _proposal(row['proposal'])
    if (proposal != row['proposal'] or proposal['declaration_id'] != declaration['id']
            or proposal['module'] not in declaration['allowed_modules'] or row != _observation(declaration, proposal)):
        raise ValueError('Lifecycle observation differs from its canonical source and Reviewer binding')
    return copy.deepcopy(row)


def _manifest(manifest):
    if (not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS
            or type(manifest.get('version')) is not int or manifest['version'] != 1
            or not isinstance(manifest.get('observations'), list)
            or any(not isinstance(manifest.get(key), str) or not _HASH.fullmatch(manifest[key])
                   for key in ('inventory_hash', 'public_targets_hash', 'hash'))):
        raise ValueError('Invalid protected lifecycle manifest')
    rows = [normalize_observation(row) for row in manifest['observations']]
    identities = [row['proposal']['declaration_id'] for row in rows]
    if (identities != sorted(set(identities))
            or manifest['hash'] != digest({key: value for key, value in manifest.items() if key != 'hash'})):
        raise ValueError('Lifecycle manifest has duplicated cases, noncanonical order or a changed content hash')
    return rows


def verify(sources, manifest, *, public_targets, inactive=()):
    rows = _manifest(manifest)
    expected = bind(sources, [row['proposal'] for row in rows], public_targets=public_targets, inactive=inactive)
    if manifest != expected:
        raise ValueError('Lifecycle manifest changed its original source, public inventory or observation')
    return expected


def preserve(previous, proposed, *, replacements=()):
    """Keep all previous obligations except exact caller-authenticated replacements.

    Caller must establish that each replacement source is a genuine later human
    amendment. A changed CID, target or protocol without that authority fails.
    """
    old = {row['hash']: row for row in _manifest(previous)}
    new = {row['hash']: row for row in _manifest(proposed)}
    authorized = set()
    for replacement in replacements:
        if (not isinstance(replacement, dict) or set(replacement) != {
                'previous_hash', 'replacement_hash', 'replacement_source_sha256'}):
            raise ValueError('Lifecycle replacements need the exact old, new and authenticated source hashes')
        before, after = old.get(replacement['previous_hash']), new.get(replacement['replacement_hash'])
        if (not before or not after or replacement['previous_hash'] in authorized
                or before['hash'] == after['hash'] or before['protocol'] != after['protocol']
                or replacement['replacement_source_sha256'] != after['declaration']['source_sha256']
                or before['declaration']['source_sha256'] == after['declaration']['source_sha256']):
            raise ValueError('Lifecycle replacement is absent, duplicated or lacks an exact new human source')
        authorized.add(before['hash'])
    if set(old) - set(new) - authorized:
        raise ValueError('A plan cannot remove, weaken or retarget an original lifecycle observation')
    return copy.deepcopy(proposed)
