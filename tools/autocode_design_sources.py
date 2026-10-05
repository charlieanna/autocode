"""Reconcile declarations with hash-bound read-only connector page receipts.

The exporter records actual node facts. Empty declarations cannot hide source
fonts, paints, variable modes or prototype actions. This module never calls Figma.
"""
from pathlib import Path
try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


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
        metadata = node_tables[(file['key'], page['id'])][0]
        if len(nodes) != len({n['id'] for n in nodes}) or {n['id'] for n in nodes} != set(metadata):
            raise ValueError('Figma source nodes differ from the complete page metadata')
        for node in nodes:
            expected = metadata[node['id']]
            for key in ('type', 'name', 'width', 'height'):
                actual = 'CANVAS' if key == 'type' and node.get(key) == 'PAGE' else node.get(key)
                wanted = 'CANVAS' if key == 'type' and expected.get(key) == 'PAGE' else expected.get(key)
                if key in expected and actual != wanted:
                    raise ValueError('Figma source dimensions/type/name disagree with page metadata')
            if not isinstance(node.get('visual'), dict):
                raise ValueError('Figma source node needs its original REST visual properties')
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
