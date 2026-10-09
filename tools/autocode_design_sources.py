"""Reconcile declarations with hash-bound read-only connector page receipts.

The exporter records actual node facts. Empty declarations cannot hide source
fonts, paints, variable modes or prototype actions. This module never calls Figma.
"""
import base64
import hashlib
import json
import math
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


PROPERTY_GROUPS = {
    'base': 'id type name',
    'scene': 'visible locked componentPropertyReferences boundVariables explicitVariableModes resolvedVariableModes animationStyles animations manualKeyframeTracks timelines',
    'dimensions': 'x y width height minWidth maxWidth minHeight maxHeight relativeTransform absoluteTransform absoluteBoundingBox',
    'layout': 'absoluteRenderBounds rotation layoutSizingHorizontal layoutSizingVertical layoutAlign layoutGrow layoutPositioning gridRowAnchorIndex gridColumnAnchorIndex gridRowSpan gridColumnSpan gridChildHorizontalAlign gridChildVerticalAlign',
    'blend': 'opacity blendMode isMask maskType effects effectStyleId',
    'fills': 'fills fillStyleId',
    'strokes': 'strokes strokeStyleId strokeWeight strokeJoin strokeAlign dashPattern strokeGeometry',
    'geometry': 'strokeCap strokeMiterLimit fillGeometry',
    'corners': 'cornerRadius cornerSmoothing',
    'rectangleCorners': 'topLeftRadius topRightRadius bottomLeftRadius bottomRightRadius',
    'individualStrokes': 'strokeTopWeight strokeBottomWeight strokeLeftWeight strokeRightWeight',
    'complexStrokes': 'variableWidthStrokeProperties complexStrokeProperties',
    'frame': 'layoutGrids gridStyleId clipsContent guides inferredAutoLayout layoutMode paddingLeft paddingRight paddingTop paddingBottom primaryAxisSizingMode counterAxisSizingMode strokesIncludedInLayout layoutWrap primaryAxisAlignItems counterAxisAlignItems counterAxisAlignContent itemSpacing counterAxisSpacing itemReverseZIndex gridRowCount gridColumnCount gridRowGap gridColumnGap gridRowSizes gridColumnSizes gridAutoTracks gridItemsPositioning',
    'prototype': 'overflowDirection numberOfFixedChildren overlayPositionType overlayBackground overlayBackgroundInteraction',
    'text': 'hasMissingFont fontSize fontName fontWeight textCase openTypeFeatures letterSpacing hyperlink characters textAlignHorizontal textAlignVertical autoRename textStyleId',
    'paragraph': 'paragraphIndent paragraphSpacing textWrapStyle listSpacing hangingPunctuation hangingList textDecoration textDecorationStyle textDecorationOffset textDecorationThickness textDecorationColor textDecorationSkipInk lineHeight leadingTrim textAutoResize textTruncation maxLines',
}

NODE_GROUPS = {
    'PAGE': ['base'],
    'FRAME': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'corners', 'rectangleCorners', 'individualStrokes', 'frame', 'prototype'],
    'COMPONENT': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'corners', 'rectangleCorners', 'individualStrokes', 'frame', 'prototype'],
    'INSTANCE': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'corners', 'rectangleCorners', 'individualStrokes', 'frame', 'prototype'],
    'COMPONENT_SET': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'corners', 'rectangleCorners', 'individualStrokes', 'frame'],
    'GROUP': ['base', 'scene', 'dimensions', 'layout', 'blend'],
    'TRANSFORM_GROUP': ['base', 'scene', 'dimensions', 'layout', 'blend'],
    'SECTION': ['base', 'scene', 'dimensions', 'fills', 'strokes', 'corners', 'rectangleCorners'],
    'SLICE': ['base', 'scene', 'dimensions', 'layout'],
    'RECTANGLE': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'corners', 'rectangleCorners', 'individualStrokes'],
    'ELLIPSE': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'corners'],
    'LINE': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes'],
    'POLYGON': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'corners'],
    'STAR': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'corners'],
    'VECTOR': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'corners'],
    'BOOLEAN_OPERATION': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'corners'],
    'TEXT': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'text', 'paragraph'],
    'TEXT_PATH': ['base', 'scene', 'dimensions', 'layout', 'blend', 'fills', 'strokes', 'geometry', 'complexStrokes', 'text'],
}

EXTRA_FIELDS = {
    'PAGE': 'backgrounds prototypeBackgrounds flowStartingPoints explicitVariableModes guides',
    'FRAME': 'constraints targetAspectRatio',
    'COMPONENT': 'constraints targetAspectRatio key remote',
    'COMPONENT_SET': 'constraints targetAspectRatio key remote',
    'INSTANCE': 'constraints targetAspectRatio componentProperties scaleFactor overrides',
    'GROUP': 'targetAspectRatio',
    'TRANSFORM_GROUP': 'targetAspectRatio transformModifiers',
    'SECTION': 'sectionContentsHidden targetAspectRatio',
    'RECTANGLE': 'constraints targetAspectRatio',
    'ELLIPSE': 'constraints targetAspectRatio arcData',
    'LINE': 'constraints',
    'POLYGON': 'constraints targetAspectRatio pointCount',
    'STAR': 'constraints targetAspectRatio pointCount innerRadius',
    'VECTOR': 'constraints targetAspectRatio vectorPaths vectorNetwork handleMirroring',
    'BOOLEAN_OPERATION': 'targetAspectRatio booleanOperation',
    'TEXT': 'constraints targetAspectRatio',
    'TEXT_PATH': 'constraints targetAspectRatio vectorPaths vectorNetwork handleMirroring textPathStartData',
}
SEGMENT_FIELDS = ['fontSize', 'fontName', 'fontWeight', 'fontStyle', 'textDecoration', 'textDecorationStyle', 'textDecorationOffset', 'textDecorationThickness', 'textDecorationColor', 'textDecorationSkipInk', 'textCase', 'lineHeight', 'letterSpacing', 'fills', 'textStyleId', 'fillStyleId', 'listOptions', 'listSpacing', 'indentation', 'paragraphIndent', 'paragraphSpacing', 'textWrapStyle', 'hyperlink', 'openTypeFeatures', 'boundVariables', 'textStyleOverrides']


# These properties are explicitly read from supported Plugin API mixins, not a
# synthetic REST document. The collector and validator must agree on this profile.
PLUGIN_SOURCE = 'figma-plugin-api-properties-v1'
PROFILE_NAME = 'autocode-design-properties-v1'
CHILD_TYPES = set(['PAGE', 'FRAME', 'COMPONENT', 'COMPONENT_SET', 'INSTANCE', 'GROUP', 'TRANSFORM_GROUP', 'SECTION', 'BOOLEAN_OPERATION'])
REACTION_TYPES = set(['FRAME', 'COMPONENT', 'INSTANCE', 'GROUP', 'TRANSFORM_GROUP', 'RECTANGLE', 'ELLIPSE', 'LINE', 'POLYGON', 'STAR', 'VECTOR', 'BOOLEAN_OPERATION', 'TEXT', 'TEXT_PATH'])
MIXED = {'__figma_mixed__': True}
UNDEFINED = {'__figma_undefined__': True}


TRANSPORT_LIMIT = 32 * 1024 * 1024


def _lzw_encode(text):
    codes, table, next_code, prefix = [256], {}, 257, text[0]
    for char in text[1:]:
        pair = (prefix, char)
        if pair in table:
            prefix = table[pair]
        else:
            codes.append(prefix)
            if next_code < 65536:
                table[pair], next_code = next_code, next_code + 1
            else:
                codes.append(256)
                table, next_code = {}, 257
            prefix = char
    codes.append(prefix)
    return b''.join(code.to_bytes(2, 'big') for code in codes)


def _lzw_decode(binary, length):
    if not binary or len(binary) % 2 or int.from_bytes(binary[:2], 'big') != 256:
        raise ValueError('Figma source transport has malformed LZW codes')
    table, next_code, previous, output, size = {}, 257, None, [], 0
    for offset in range(2, len(binary), 2):
        code = int.from_bytes(binary[offset:offset + 2], 'big')
        if code == 256:
            if next_code != 65536 or previous is None:
                raise ValueError('Figma source transport has noncanonical dictionary resets')
            table, next_code, previous = {}, 257, None
            continue
        if code < 256:
            entry = bytes((code,))
        elif code in table:
            entry = table[code]
        elif code == next_code and previous is not None:
            entry = previous + previous[:1]
        else:
            raise ValueError('Figma source transport references an unknown LZW code')
        size += len(entry)
        if size > length or size > TRANSPORT_LIMIT:
            raise ValueError('Figma source transport exceeds its decoded size bound')
        output.append(entry)
        if previous is not None and next_code < 65536:
            table[next_code], next_code = previous + entry[:1], next_code + 1
        previous = entry
    text = b''.join(output)
    if size != length or not text.isascii() or _lzw_encode(text) != binary:
        raise ValueError('Figma source transport is truncated or noncanonical')
    return text


def reassemble(parts):
    """Decode all bounded connector parts before retaining an original receipt.

    Part envelopes cannot pass source validation. Missing/drifting parts, corrupt
    compression and a SHA mismatch are blockers, never partial source acceptance.
    """
    try:
        if not isinstance(parts, list) or not parts:
            raise ValueError('Figma source transport has no parts')
        header = parts[0]
        keys = ('transport_version', 'encoding', 'source_format', 'file_key', 'page_id',
                'receipt_sha256', 'receipt_length', 'encoded_length', 'part_count', 'complete', 'error_count')
        if (header['transport_version'] != 1 or header['encoding'] != 'lzw16-base64-json-ascii-v1'
                or header['source_format'] != PLUGIN_SOURCE or type(header['part_count']) is not int
                or type(header['receipt_length']) is not int or not 0 < header['receipt_length'] <= TRANSPORT_LIMIT
                or type(header['encoded_length']) is not int or not 0 < header['encoded_length'] <= TRANSPORT_LIMIT * 3
                or header['part_count'] != (header['encoded_length'] + 13999) // 14000
                or len(parts) != header['part_count']):
            raise ValueError('Figma source transport is incomplete, oversized or unsupported')
        ordered = {}
        for part in parts:
            if any(part[key] != header[key] for key in keys):
                raise ValueError('Figma source transport identity/content changed between reads')
            index, text = part['part_index'], part['json_part']
            if (type(index) is not int or index in ordered or not 0 <= index < header['part_count']
                    or not isinstance(text, str) or not text.isascii()
                    or len(text) != min(14000, header['encoded_length'] - index * 14000)):
                raise ValueError('Figma source transport has duplicate, missing or truncated parts')
            ordered[index] = text
        encoded = ''.join(ordered[index] for index in range(header['part_count']))
        binary = base64.b64decode(encoded, validate=True)
        if base64.b64encode(binary).decode('ascii') != encoded:
            raise ValueError('Figma source transport has noncanonical base64')
        text = _lzw_decode(binary, header['receipt_length'])
        if hashlib.sha256(text).hexdigest() != header['receipt_sha256']:
            raise ValueError('Figma source transport digest differs from its complete receipt')
        receipt = json.loads(text)
        if (receipt['file_key'] != header['file_key'] or receipt['page_id'] != header['page_id']
                or receipt['source_format'] != header['source_format'] or receipt['complete'] != header['complete']
                or len(receipt['errors']) != header['error_count']):
            raise ValueError('Figma source transport disagrees with its original receipt')
        return receipt
    except (KeyError, TypeError, AttributeError, UnicodeError) as error:
        raise ValueError('Malformed Figma source receipt transport') from error


def property_fields(kind):
    if kind not in NODE_GROUPS:
        raise ValueError(f'Unsupported Figma node property profile: {kind}')
    fields = {field for group in NODE_GROUPS[kind] for field in PROPERTY_GROUPS[group].split()} | set(EXTRA_FIELDS.get(kind, '').split())
    # Text glyph outlines are derived; all authored text/range and text-path
    # vector properties remain original mandatory getters.
    return fields - {'fillGeometry', 'strokeGeometry'} if kind in ('TEXT', 'TEXT_PATH') else fields


def _original_values(value):
    """Reject failed getters and lossy/invalid JSON without erasing mixed values."""
    if isinstance(value, dict):
        special = [key for key in value if key.startswith('__figma_')]
        if special and value not in (MIXED, UNDEFINED):
            raise ValueError('Figma source contains an unreadable or invalid property marker')
        for row in value.values():
            _original_values(row)
    elif isinstance(value, list):
        for row in value:
            _original_values(row)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Figma source contains a nonfinite property')


def _required(record, fields, label):
    if not isinstance(record, dict) or set(fields) - record.keys():
        missing = sorted(set(fields) - record.keys()) if isinstance(record, dict) else sorted(fields)
        raise ValueError(f'Figma property snapshot is missing {label}: {missing}')
    for key in fields:
        if record[key] == UNDEFINED and key != 'boundVariables':
            raise ValueError(f'Figma required property is undefined: {label}.{key}')
    _original_values(record)


def _fonts(visual):
    if visual['type'] not in ('TEXT', 'TEXT_PATH'):
        return []
    fields = {'characters', 'start', 'end'} | set(SEGMENT_FIELDS) - {'boundVariables'}
    if visual['type'] == 'TEXT_PATH':
        fields.discard('textWrapStyle')
    text, segments = visual['characters'], visual['text_segments']
    if not isinstance(text, str) or not isinstance(segments, list):
        raise ValueError('Figma text snapshot needs original characters and styled segments')
    encoded = text.encode('utf-16-le', errors='surrogatepass')
    end, fonts = 0, []
    for segment in segments:
        _required(segment, fields, 'text segment')
        start, stop = segment['start'], segment['end']
        if (type(start) is not int or type(stop) is not int or start != end or stop <= start
                or stop * 2 > len(encoded) or not isinstance(segment['characters'], str)
                or segment['characters'].encode('utf-16-le', errors='surrogatepass') != encoded[start * 2:stop * 2]):
            raise ValueError('Figma text segments omit, overlap or change original characters')
        end = stop
        font = segment['fontName']
        if not isinstance(font, dict) or any(not isinstance(font.get(key), str) or not font[key] for key in ('family', 'style')):
            raise ValueError('Figma segment font is unreadable')
        if font not in fonts:
            fonts.append(font)
    if end * 2 != len(encoded):
        raise ValueError('Figma text segments do not cover the original text')
    if not segments:
        font = visual['fontName']
        if not isinstance(font, dict) or any(not isinstance(font.get(key), str) or not font[key] for key in ('family', 'style')):
            raise ValueError('Figma empty text font is unreadable')
        fonts.append(font)
    return fonts


def _assets(visual):
    node_id, found = visual['id'], []
    paints = [(field, visual.get(field)) for field in ('fills', 'strokes', 'backgrounds', 'prototypeBackgrounds')]
    paints += [(f'text_segments:{i}:fills', segment['fills']) for i, segment in enumerate(visual.get('text_segments', []))]
    for field, values in paints:
        if values is None or values == MIXED:
            continue
        if not isinstance(values, list):
            raise ValueError(f'Figma original paints are unreadable: {node_id}.{field}')
        for index, paint in enumerate(values):
            if paint['type'] == 'IMAGE':
                if not isinstance(paint.get('imageHash'), str) or not paint['imageHash']:
                    raise ValueError('Figma required image paint is unreadable')
                found.append(dict(id=f"{node_id}:{field}:{index}:{paint['imageHash']}", mime_type='image/png'))
            elif paint['type'] not in ('SOLID', 'GRADIENT_LINEAR', 'GRADIENT_RADIAL', 'GRADIENT_ANGULAR', 'GRADIENT_DIAMOND'):
                raise ValueError(f"Unsupported required Figma paint asset: {paint['type']}")
    if visual['type'] in ('VECTOR', 'BOOLEAN_OPERATION', 'TEXT_PATH'):
        found.append(dict(id=node_id + ':svg', mime_type='image/svg+xml'))
    return found


def _transitions(visual):
    result = []
    for index, reaction in enumerate(visual.get('reactions', [])):
        actions = reaction.get('actions', [reaction['action']] if reaction.get('action') else [])
        for action_index, action in enumerate(actions):
            result.append(dict(id=f"{visual['id']}:reaction:{index}:{action_index}",
                               target_node_id=action.get('destinationId') or '',
                               trigger=reaction['trigger'], action=action))
    return result


def _variants(node, nodes):
    visual = node['visual']
    if node['type'] in ('COMPONENT', 'COMPONENT_SET'):
        if node['component_key'] != visual['key'] or not isinstance(visual['key'], str) or not visual['key']:
            raise ValueError('Figma component key differs from its original getter')
    if node['type'] == 'COMPONENT':
        parent = nodes[visual['parent_id']]['visual']
        if parent['type'] == 'COMPONENT_SET':
            properties = visual['variantProperties']
            definitions = parent['componentPropertyDefinitions']
            _variant_coordinates(properties, definitions)
        else:
            properties = {}
        if node['variant_properties'] != properties:
            raise ValueError('Figma variant coordinates differ from original properties')
    if node['type'] == 'INSTANCE':
        ref = visual['main_component']
        owner = visual['main_component_owner']
        _required(owner, ('id', 'type', 'componentPropertyDefinitions'), 'main component owner')
        if owner['type'] not in ('COMPONENT', 'COMPONENT_SET') or not isinstance(owner['id'], str):
            raise ValueError('Figma instance component owner is unreadable')
        properties = {key: row['value'] for key, row in visual['componentProperties'].items() if row['type'] == 'VARIANT'}
        if owner['type'] == 'COMPONENT_SET':
            _variant_coordinates(properties, owner['componentPropertyDefinitions'])
        _required(ref, ('key', 'node_id', 'variant_properties', 'remote'), 'main component reference')
        if (node['component_ref'] != ref or ref['variant_properties'] != properties
                or not isinstance(ref['key'], str) or not ref['key'] or not isinstance(ref['node_id'], str)
                or type(ref['remote']) is not bool):
            raise ValueError('Figma instance identity differs from original properties')


def _variant_coordinates(properties, definitions):
    if not isinstance(properties, dict) or not properties or not isinstance(definitions, dict):
        raise ValueError('Figma variant identity is unreadable')
    expected = {key for key, row in definitions.items() if row['type'] == 'VARIANT'}
    if set(properties) != expected:
        raise ValueError('Figma variant identity omits or invents a coordinate')
    for key, value in properties.items():
        if value not in definitions[key]['variantOptions']:
            raise ValueError('Figma variant coordinate is absent from its original owner')


def _token_sources(receipt, nodes):
    sources = {row['id']: row for row in receipt['variable_sources']}
    collections = {row['id']: row for row in receipt['variable_collections']}
    if len(sources) != len(receipt['variable_sources']) or len(collections) != len(receipt['variable_collections']):
        raise ValueError('Duplicate Figma variable source identity')
    if not set(receipt['local_variable_ids']) <= sources.keys() or not set(receipt['local_collection_ids']) <= collections.keys():
        raise ValueError('Figma local token roster is incomplete')
    expected = []
    for collection in collections.values():
        _required(collection, ('id', 'key', 'name', 'modes', 'variableIds'), 'variable collection')
        mode_ids = [mode['modeId'] for mode in collection['modes']]
        if not mode_ids or len(mode_ids) != len(set(mode_ids)) or len(collection['variableIds']) != len(set(collection['variableIds'])):
            raise ValueError('Figma variable collection modes/roster are invalid')
        if not set(collection['variableIds']) <= sources.keys():
            raise ValueError('Figma collection omits its original variables')
        if any(sources[key]['variableCollectionId'] != collection['id'] for key in collection['variableIds']):
            raise ValueError('Figma variable belongs to a different original collection')
    for variable in sources.values():
        _required(variable, ('id', 'key', 'name', 'resolvedType', 'variableCollectionId', 'valuesByMode'), 'variable source')
        collection = collections[variable['variableCollectionId']]
        if variable['id'] not in collection['variableIds'] or set(variable['valuesByMode']) != {mode['modeId'] for mode in collection['modes']}:
            raise ValueError('Figma variable omits or invents original collection modes')
        for mode in collection['modes']:
            expected.append(dict(key=variable['key'] + ':' + mode['modeId'], source_id=variable['id'],
                                 name=variable['name'], kind=variable['resolvedType'], value=variable['valuesByMode'][mode['modeId']],
                                 collection=collection['name'], mode=mode['name'], mode_id=mode['modeId']))
    if sorted(expected, key=lambda row: row['key']) != sorted(receipt['variables'], key=lambda row: row['key']):
        raise ValueError('Figma derived token rows differ from original collection modes')
    if not aliases([row['visual'] for row in nodes] + list(sources.values())) <= sources.keys():
        raise ValueError('Figma original property/token aliases are unreadable or omitted')
    for node in nodes:
        for field in ('explicitVariableModes', 'resolvedVariableModes'):
            for collection_id, mode_id in node['visual'].get(field, {}).items():
                if collection_id not in collections or mode_id not in {row['modeId'] for row in collections[collection_id]['modes']}:
                    raise ValueError('Figma explicit/resolved variable mode is unreadable')
        for transition in node['transitions']:
            action = transition['action']
            if action.get('type') == 'SET_VARIABLE_MODE':
                collection_id = action['variableCollectionId']
                if collection_id not in collections or action['variableModeId'] not in {row['modeId'] for row in collections[collection_id]['modes']}:
                    raise ValueError('Figma prototype variable mode is unreadable')


def _property_shapes(visual):
    """Required structured getters cannot be replaced with invented empty maps."""
    for field in ('absoluteTransform', 'relativeTransform'):
        if field in visual:
            matrix = visual[field]
            if (not isinstance(matrix, list) or len(matrix) != 2
                    or any(not isinstance(row, list) or len(row) != 3 or any(type(value) not in (int, float) for value in row) for row in matrix)):
                raise ValueError(f'Figma original transform getter is malformed: {field}')
    for field in ('absoluteBoundingBox', 'absoluteRenderBounds'):
        box = visual.get(field)
        if box is not None and (not isinstance(box, dict) or any(type(box.get(key)) not in (int, float) for key in ('x', 'y', 'width', 'height'))):
            raise ValueError(f'Figma original bounds getter is malformed: {field}')
    for field in ('boundVariables', 'componentProperties', 'componentPropertyDefinitions',
                  'explicitVariableModes', 'resolvedVariableModes', 'animations', 'manualKeyframeTracks'):
        if field in visual and not isinstance(visual[field], dict):
            raise ValueError(f'Figma original binding/property getter is malformed: {field}')
    for field in ('explicitVariableModes', 'resolvedVariableModes'):
        if any(not isinstance(key, str) or not key or not isinstance(value, str) or not value for key, value in visual.get(field, {}).items()):
            raise ValueError('Figma original variable mode getter is malformed')
    if 'complexStrokeProperties' in visual and (not isinstance(visual['complexStrokeProperties'], dict) or visual['complexStrokeProperties'].get('type') not in ('BASIC', 'BRUSH', 'DYNAMIC')):
        raise ValueError('Figma original complex stroke getter is malformed')
    for field in ('fillGeometry', 'strokeGeometry', 'vectorPaths'):
        if field in visual and (not isinstance(visual[field], list) or any(not isinstance(row, dict) or not isinstance(row.get('data'), str) or row.get('windingRule') not in ('NONZERO', 'EVENODD') for row in visual[field])):
            raise ValueError(f'Figma original vector getter is malformed: {field}')
    if 'vectorNetwork' in visual and (not isinstance(visual['vectorNetwork'], dict) or any(not isinstance(visual['vectorNetwork'].get(key), list) for key in ('vertices', 'segments'))):
        raise ValueError('Figma original vector network getter is malformed')


def _validate_plugin(receipt, metadata, parents, file):
    if receipt.get('property_profile') != PROFILE_NAME or receipt.get('api_version') != '1.0.0':
        raise ValueError('Unsupported Figma Plugin API property profile/version')
    pages = receipt['document_pages']
    if (not isinstance(pages, list) or len(pages) != len({row['id'] for row in pages})
            or {row['id'] for row in pages} != {row['id'] for row in file['pages']}
            or any(row['type'] != 'PAGE' for row in pages)):
        raise ValueError('Figma original document page roster is incomplete or differs from file metadata')
    names = {row['id']: row['name'] for row in pages}
    if any(row['name'] != names[row['id']] for row in file['pages']):
        raise ValueError('Figma original document page names differ from file metadata')
    nodes = {row['id']: row for row in receipt['nodes']}
    for node in nodes.values():
        visual, kind = node['visual'], node['type']
        required = property_fields(kind) | {'parent_id'}
        if kind in CHILD_TYPES:
            required.add('child_ids')
        if kind in REACTION_TYPES:
            required.add('reactions')
        if kind in ('TEXT', 'TEXT_PATH'):
            required.add('text_segments')
        if kind == 'PAGE':
            required.add('prototype_start_node_id')
        if kind == 'COMPONENT_SET' or (kind == 'COMPONENT' and nodes[parents[node['id']]]['type'] != 'COMPONENT_SET'):
            required.add('componentPropertyDefinitions')
        if kind == 'COMPONENT' and nodes[parents[node['id']]]['type'] == 'COMPONENT_SET':
            required.add('variantProperties')
        if kind == 'INSTANCE':
            required |= {'main_component', 'main_component_owner'}
        _required(visual, required, f"node {node['id']}")
        _property_shapes(visual)
        for key in ('id', 'type', 'name', 'width', 'height'):
            if key in visual and node.get(key) != visual[key]:
                raise ValueError('Figma property identity/dimensions differ from the source node')
        if kind != 'PAGE' and visual['parent_id'] != parents[node['id']]:
            raise ValueError('Figma original parent differs from complete page metadata')
        if kind in CHILD_TYPES:
            if visual['child_ids'] != [key for key, parent in parents.items() if parent == node['id']]:
                raise ValueError('Figma original child order differs from complete page metadata')
        for field in ('visible', 'locked', 'clipsContent', 'isMask', 'autoRename', 'hasMissingFont', 'itemReverseZIndex', 'strokesIncludedInLayout', 'sectionContentsHidden'):
            if field in visual and type(visual[field]) is not bool:
                raise ValueError(f'Figma original boolean getter is unreadable: {field}')
        for field in ('x', 'y', 'width', 'height', 'opacity', 'rotation', 'paddingLeft', 'paddingRight', 'paddingTop', 'paddingBottom', 'itemSpacing', 'counterAxisSpacing'):
            if field in visual and type(visual[field]) not in (int, float):
                raise ValueError(f'Figma original numeric getter is unreadable: {field}')
        for field in ('fills', 'strokes', 'effects', 'reactions', 'layoutGrids', 'text_segments', 'child_ids', 'backgrounds', 'prototypeBackgrounds', 'flowStartingPoints', 'guides'):
            if field in visual and not isinstance(visual[field], list) and not (field == 'fills' and kind in ('TEXT', 'TEXT_PATH') and visual[field] == MIXED):
                raise ValueError(f'Figma original array getter is unreadable: {field}')
        if node['fonts'] != _fonts(visual) or node['assets'] != _assets(visual) or set(node['variables']) != aliases(visual):
            raise ValueError('Figma declared source resources differ from original getter properties')
        transitions = _transitions(visual)
        if len(transitions) != len(node['transitions']):
            raise ValueError('Figma source transitions omit original reactions')
        for original, derived in zip(transitions, node['transitions'], strict=False):
            if any(original[key] != derived[key] for key in ('id', 'target_node_id', 'action')) or original['trigger'] != json.loads(derived['trigger']):
                raise ValueError('Figma source transitions differ from original reactions')
        _variants(node, nodes)
    _token_sources(receipt, list(nodes.values()))


def aliases(value):
    """Variable dependencies inside bindings, expressions and aliased mode values."""
    if isinstance(value, list):
        return set().union(*(aliases(row) for row in value))
    if not isinstance(value, dict):
        return set()
    found = {value['id']} if value.get('type') == 'VARIABLE_ALIAS' else set()
    if value.get('variableId'):
        found.add(value['variableId'])
    return found | set().union(*(aliases(row) for row in value.values()))


def validate(file, root, node_tables):
    try:
        return _validate(file, root, node_tables)
    except (KeyError, TypeError, AttributeError, RuntimeError) as error:
        raise ValueError('Malformed Figma page source receipt') from error


def _validate(file, root, node_tables):
    fonts, assets, variables, reactions, facts = set(), {}, {}, {}, {}
    for page in file['pages']:
        artifact = page['source_json']
        path = Path(root) / artifact['path']
        if not path.resolve().is_relative_to(Path(root).resolve()) or path.is_symlink():
            raise ValueError('Figma page source receipt is outside its bundle')
        receipt = util.read_object(path)
        if (receipt.get('version') != 1 or receipt.get('file_key') != file['key']
                or receipt.get('page_id') != page['id'] or receipt.get('read_only') is not True
                or receipt.get('complete') is not True or receipt.get('errors')):
            raise ValueError('Figma source receipt is incomplete, unreadable or has the wrong identity')
        nodes = receipt['nodes']
        metadata, parents = node_tables[(file['key'], page['id'])]
        source_format = receipt.get('source_format')
        if (source_format not in (None, 'figma-rest-json-rest-v1', PLUGIN_SOURCE)
                or source_format != PLUGIN_SOURCE and any(key in receipt for key in
                    ('property_profile', 'api_version', 'variable_sources', 'variable_collections', 'document_pages'))):
            raise ValueError('Unsupported Figma page source format')
        if len(nodes) != len({n['id'] for n in nodes}) or {n['id'] for n in nodes} != set(metadata):
            raise ValueError('Figma source nodes differ from the complete page metadata')
        if source_format == PLUGIN_SOURCE:
            _validate_plugin(receipt, metadata, parents, file)
        for node in nodes:
            expected = metadata[node['id']]
            for key in ('type', 'name', 'width', 'height'):
                actual = 'CANVAS' if key == 'type' and node.get(key) == 'PAGE' else node.get(key)
                wanted = 'CANVAS' if key == 'type' and expected.get(key) == 'PAGE' else expected.get(key)
                if key in expected and actual != wanted:
                    raise ValueError('Figma source dimensions/type/name disagree with page metadata')
            if not isinstance(node.get('visual'), dict):
                raise ValueError('Figma source node needs its original source visual properties')
            facts[(page['id'], node['id'])] = node
            fonts.update((font['family'], font['style']) for font in node['fonts'])
            for asset in node['assets']:
                item = dict(page_id=page['id'], node_id=node['id'], mime_type=asset['mime_type'])
                if asset['id'] in assets:
                    raise ValueError('Duplicate source asset identity')
                assets[asset['id']] = item
            for reaction in node['transitions']:
                if reaction['id'] in reactions:
                    raise ValueError('Duplicate source transition identity')
                if reaction['target_node_id'] != (reaction['action'].get('destinationId') or ''):
                    raise ValueError('Prototype destination disagrees with its source action')
                reactions[reaction['id']] = dict(source_node_id=node['id'], **reaction)
        for variable in receipt['variables']:
            old = variables.setdefault(variable['key'], variable)
            if old != variable:
                raise ValueError('Conflicting Figma variable modes in source receipts')
        used = {key for node in nodes for key in node['variables']}
        used |= aliases([node['transitions'] for node in nodes])
        used |= aliases([row['value'] for row in receipt['variables']])
        if not used <= {row['source_id'] for row in receipt['variables']}:
            raise ValueError('Source variable bindings are unreadable or unrepresented')
    if fonts != {(row['family'], row['style']) for row in file['fonts']}:
        raise ValueError('Declared fonts omit or invent source typography')
    declared_assets = {row['id']: {key: row[key] for key in ('page_id', 'node_id', 'mime_type')}
                       for row in file['assets']}
    if declared_assets != assets:
        raise ValueError('Declared assets omit or invent source paints/vectors')
    if {row['key']: row for row in file['variables']} != variables:
        raise ValueError('Declared variables omit or change source token modes')
    if {row['id']: row for row in file['transitions']} != reactions:
        raise ValueError('Declared transitions omit or change source prototype actions')
    for component in file['components']:
        source = facts.get((component['source_page_id'], component['source_node_id']), {})
        if source.get('component_key') != component['key']:
            raise ValueError('Declared component key differs from its source library identity')
        for variant in component['variants']:
            source = facts.get((variant['page_id'], variant['node_id']), {})
            if source.get('variant_properties') != variant['properties']:
                raise ValueError('Declared component variant properties differ from their source')
    return facts
