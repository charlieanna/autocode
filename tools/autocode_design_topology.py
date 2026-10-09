"""Deterministic page topology derived from complete original getter receipts.

This is a same-source representation, never independent connector metadata or
visual acceptance. Inventory/source/resource validation remains mandatory.
"""
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
try:
    from . import autocode_design_sources as sources
except ImportError:
    import autocode_design_sources as sources


KIND = 'collector-derived'
HASH = re.compile(r'[a-f0-9]{64}\Z')
NODE_ID = re.compile(r'I?[0-9]+:[0-9]+(?:;[0-9]+:[0-9]+)*\Z')


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate key in original Figma source JSON')
        value[key] = item
    return value


def _nonfinite(value):
    raise ValueError('Nonfinite original Figma source JSON: ' + value)


def _receipt(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > sources.TRANSPORT_LIMIT:
        raise ValueError('Figma topology requires bounded complete source bytes')
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
                or value.get('source_format') != sources.PLUGIN_SOURCE
                or value.get('property_profile') not in (sources.PROFILE_NAME, sources.NATIVE_CAPABILITY_PROFILE)
                or value.get('api_version') != '1.0.0' or value.get('read_only') is not True
                or value.get('complete') is not True or value.get('errors') != []
                or not isinstance(value.get('file_key'), str) or not value['file_key']
                or not isinstance(value.get('page_id'), str) or not re.fullmatch(r'[0-9]+:[0-9]+', value['page_id'])):
            raise ValueError('Figma topology requires a complete original Plugin API receipt')
        return value
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('Unreadable original Figma source JSON') from error


def _nodes(receipt):
    rows = receipt.get('nodes')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Figma topology has no complete node list')
    nodes = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Malformed original Figma topology node')
        ident, kind, visual = row.get('id'), row.get('type'), row.get('visual')
        if (not isinstance(ident, str) or not NODE_ID.fullmatch(ident) or ident in nodes
                or not isinstance(kind, str) or kind not in sources.NODE_GROUPS or not isinstance(visual, dict)
                or not isinstance(row.get('name'), str)
                or any(visual.get(key) != row.get(key) for key in ('id', 'type', 'name'))
                or not isinstance(visual.get('parent_id'), str)
                or not NODE_ID.fullmatch(visual['parent_id'])):
            raise ValueError('Invalid, duplicate or contradictory original Figma node identity')
        for key in ('x', 'y', 'width', 'height'):
            if kind != 'PAGE' and key not in visual:
                raise ValueError('Missing original Figma scene geometry: ' + ident + ':' + key)
            if key in visual:
                try:
                    finite = type(visual[key]) in (int, float) and math.isfinite(visual[key])
                except OverflowError:
                    finite = False
                if not finite:
                    raise ValueError('Nonfinite or unreadable original Figma geometry: ' + ident + ':' + key)
        if any(key in row and row[key] != visual.get(key) for key in ('width', 'height')):
            raise ValueError('Contradictory original Figma node dimensions')
        if kind in sources.CHILD_TYPES:
            children = visual.get('child_ids')
            if (not isinstance(children, list) or any(not isinstance(child, str) for child in children)
                    or len(children) != len(set(children))):
                raise ValueError('Missing or duplicate original Figma ordered children')
        elif 'child_ids' in visual:
            raise ValueError('Original Figma leaf node declares children')
        nodes[ident] = row
    page_id = receipt['page_id']
    if page_id not in nodes or nodes[page_id]['type'] != 'PAGE' or any(
            row['type'] == 'PAGE' and row['id'] != page_id for row in rows):
        raise ValueError('Original Figma topology has the wrong page root')
    if nodes[page_id]['visual']['parent_id'] in nodes:
        raise ValueError('Original Figma page root has an internal parent')
    owners = {}
    for ident, row in nodes.items():
        for child in row['visual'].get('child_ids', []):
            if child not in nodes or child == page_id or child in owners:
                raise ValueError('Original Figma topology has an unknown or multiply owned child')
            if nodes[child]['visual']['parent_id'] != ident:
                raise ValueError('Original Figma parent and ordered children disagree')
            owners[child] = ident
    if set(owners) != set(nodes) - {page_id}:
        raise ValueError('Original Figma topology has an orphan node')
    seen, todo = set(), [page_id]
    while todo:
        ident = todo.pop()
        if ident in seen:
            raise ValueError('Original Figma topology has a cycle')
        seen.add(ident)
        todo.extend(nodes[ident]['visual'].get('child_ids', []))
    if seen != set(nodes):
        raise ValueError('Original Figma topology has an unreachable cycle')
    return nodes


def generate(source_bytes):
    """Return canonical typed XML bound to these exact original source bytes.

    Only topology is derived here. This does not validate original source
    properties/resources, authenticate hand-edited bytes, or grant acceptance.
    """
    receipt = _receipt(source_bytes)
    nodes = _nodes(receipt)
    digest = hashlib.sha256(source_bytes).hexdigest()
    output, todo = [], [(receipt['page_id'], False)]
    while todo:
        ident, closing = todo.pop()
        if closing:
            output.append('</node>\n' if ident != receipt['page_id'] else '</canvas>\n')
            continue
        row, visual = nodes[ident], nodes[ident]['visual']
        attributes = {key: row[key] for key in ('id', 'type', 'name')}
        if ident == receipt['page_id']:
            attributes.update(metadata_kind=KIND, source_sha256=digest)
        attributes.update({key: str(visual[key]) for key in ('x', 'y', 'width', 'height') if key in visual})
        tag = 'canvas' if ident == receipt['page_id'] else 'node'
        element = ET.tostring(ET.Element(tag, attributes), encoding='unicode')
        children = visual.get('child_ids', [])
        if children:
            output.append(element[:-3] + '>\n')
            todo.append((ident, True))
            todo.extend((child, False) for child in reversed(children))
        else:
            output.append(element + '\n')
    try:
        raw = ''.join(output).encode('utf-8')
        ET.fromstring(raw)
    except (UnicodeError, ET.ParseError) as error:
        raise ValueError('Original Figma topology cannot be represented as valid XML') from error
    return raw


def verify(metadata_bytes, source_bytes, source_sha256):
    """Verify exact canonical derivation and hash binding; return its XML tree."""
    if (not isinstance(source_sha256, str) or not HASH.fullmatch(source_sha256)
            or not isinstance(source_bytes, bytes)
            or hashlib.sha256(source_bytes).hexdigest() != source_sha256):
        raise ValueError('Derived Figma topology differs from its hash-bound source artifact')
    expected = generate(source_bytes)
    if metadata_bytes != expected:
        raise ValueError('Figma topology is not the canonical collector-derived source representation')
    return ET.fromstring(expected)
