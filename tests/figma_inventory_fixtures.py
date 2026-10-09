"""Offline inventory provider adapters; captures never prove actual image fidelity."""
import re
from pathlib import Path

HOOK = r"""
if stage == 'collect_design':
    if os.environ.get('FAKE_NATIVE_FAILURE'):
        print(json.dumps(dict(type='turn.failed', error=dict(message='Offline connector process failed'))), flush=True)
        raise SystemExit(3)
    if os.environ.get('FAKE_NATIVE_BLOCKED'):
        report = dict(status='BLOCKED',manifest_path='',blockers=['Unreadable approved FILEB page'],summary='Offline unavailable connector')
    else:
        import shutil
        destination = Path('.autocode/native-design-inputs/fixture')
        shutil.copytree(Path(os.environ['FAKE_NATIVE_MANIFEST']).parent,destination,dirs_exist_ok=True)
        report = dict(status='READY',manifest_path=str(destination / Path(os.environ['FAKE_NATIVE_MANIFEST']).name),blockers=[],summary='Offline complete two-file collection')
if 'requirements_rerun' in data.get('report_schema', {}).get('properties', {}):
    report['requirements_rerun'] = ''
elif stage == 'astra_discovery' and data.get('brief_feedback'):
    report['requirements_rerun'] = ''
if 'requirement_trace' in report and 'requirement_trace_rows' in data:
    report['requirement_trace'] = [dict(requirement_id=row['requirement_id'], disposition='covered', evidence='C1')
                                 for row in data['requirement_trace_rows']]
design = data.get('design_manifest')
design_cases = (design.get('catalog', {}).get('cases', design.get('body', {}).get('cases', []))
                if design and stage in ('terra', 'sol') else
                design.get('body', {}).get('cases', []) if design else [])
if design and design.get('body', {}).get('version') == 2 and 'contract' in report:
    for milestone in report['contract']['milestones']:
        milestone['affected_paths'] = sorted(set(milestone.get('affected_paths', []))
            | {path for case in design_cases for path in case['implementation_paths']})
    report['contract']['design_coverage'] = dict(manifest_hash=design['manifest_hash'],
        cases=[dict(id=case['id'], criterion_ids=['C1'], milestone_ids=['M1']) for case in design_cases],
        responsive_derivations=[])
    if os.environ.get('FAKE_DESIGN_OMIT_PLAN_CASE'):
        report['contract']['design_coverage']['cases'].pop()
if design:
    sys.path.insert(0, os.environ['FAKE_CAPTURE_REPO'])
    from tests.visual_capture_fixtures import make_capture
if design and stage == 'terra':
    if os.environ.get('FAKE_DESIGN_STALE_CAPTURE'):
        old = [make_capture(Path.cwd(), design['manifest_hash'], case, asset=CAPTURE_ASSET) for case in design_cases]
        Path('.autocode/old-captures.json').write_text(json.dumps(old))
        with Path('greet.py').open('a') as source:
            source.write('\n# Implementation B: current source differs from capture A.\n')
    for case in design_cases:
        make_capture(Path.cwd(), design['manifest_hash'], case, asset=CAPTURE_ASSET)
if design and stage == 'sol' and not data.get('report_repair'):
    design = dict(design,body=json.loads(Path(design['full_manifest']).read_text())) if 'body' not in design else design
    selected = {row['case']['id']: row for row in data['implementation_captures']['current']}
    assert set(selected) == {case['id'] for case in design['body']['cases']}, selected
    assert data['implementation_captures']['visual_acceptance'] is None
    report['design_manifest_hash'] = design['manifest_hash']
    rows = []
    for index, case in enumerate(design['body']['cases']):
        capture = {key: selected[case['id']][key] for key in ('candidate_ref', 'capture_ref', 'capture_sha256')}
        if os.environ.get('FAKE_DESIGN_STALE_CAPTURE'):
            capture = json.loads(Path('.autocode/old-captures.json').read_text())[index]
        comparison = output.parent / ('compare-' + case['id'] + '.txt')
        comparison.write_text('Offline fixture metadata comparison; not real image acceptance')
        rows.append(dict(id=case['id'], status='PASS', criterion_ids=['C1'],
                         **capture, comparison_ref=str(comparison)))
    if os.environ.get('FAKE_DESIGN_UNVERIFIED'):
        rows[-1]['status'] = 'NOT_VERIFIED'
    report['design_results'] = rows
if design or stage == 'collect_design':
    with open(os.environ['FAKE_DESIGN_PROMPTS'], 'a') as log:
        log.write(json.dumps(dict(stage=stage, ids=[c['id'] for c in design_cases])) + '\n')
if data.get('report_repair') and stage in ('sol', 'astra_discovery', 'glm_revise', 'astra_finalize'):
    report = data['rejected_report']['content']
"""


def install_inventory_hook(provider, *, result='report', indent='    ', asset="'greet.py'"):
    source = Path(provider).read_text()
    markers = [f'output.write_text(json.dumps({result}))',
               f'Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps({result}))']
    matches = [marker for marker in markers if source.count(indent + marker) == 1]
    assert len(matches) == 1, matches
    marker = matches[0]
    hook = re.sub(r'\breport\b', result, HOOK.replace('CAPTURE_ASSET', asset))
    if marker.startswith('Path('):
        hook = 'output = Path(sys.argv[sys.argv.index("-o") + 1])\n' + hook
    source = source.replace(indent + marker, '\n'.join(indent + line for line in hook.splitlines()) + '\n' + indent + marker)
    component_dispatch = 'report = report_for(stage, component_id, spec, data)'
    if component_dispatch in source:
        source = source.replace(component_dispatch,
            "report = {} if stage == 'collect_design' else report_for(stage, component_id, spec, data)")
    if result == 'result':
        # This legacy provider builds a goal-contract packet before selecting its
        # stage. Inventory precedes that contract, so dispatch it at the boundary.
        marker = 'stage = data["stage"]\n'
        assert source.count(marker) == 1
        collector = hook.split("if 'requirements_rerun'", 1)[0]
        dispatch = r"""
if stage == 'collect_design':
    record_launch(stage)
    print(json.dumps(dict(type='thread.started', thread_id=str(uuid.uuid4()))), flush=True)
""" + '\n'.join('    ' + line for line in collector.splitlines()) + r"""
    with open(os.environ['FAKE_DESIGN_PROMPTS'], 'a') as log:
        log.write(json.dumps(dict(stage=stage, ids=[])) + '\n')
    Path(sys.argv[sys.argv.index('-o') + 1]).write_text(json.dumps(result))
    print(json.dumps(dict(type='turn.completed', usage=dict(input_tokens=20, output_tokens=10))), flush=True)
    raise SystemExit(0)
"""
        source = source.replace(marker, marker + dispatch)
    if 'import os' not in source:
        source = source.replace('import json', 'import os\nimport json', 1)
    Path(provider).write_text(source)
    Path(provider).chmod(0o755)


def native_bundle(root, file_key, implementation_path):
    """One synthetic native file, with every original page/node/resource retained."""
    import json
    from tests.test_design_manifest import inventory_bundle
    from autocode_util import file_hash
    path, body = inventory_bundle(root)
    body['files'] = body['files'][:1]
    body['cases'] = body['cases'][:2]
    original_key = body['files'][0]['key']
    body['files'][0]['key'] = file_key
    for case in body['cases']:
        case['file_key'] = file_key
        case['id'] = case['id'].replace(original_key, file_key, 1)
        case['implementation_paths'] = [implementation_path]
    artifact = body['files'][0]['pages'][0]['source_json']
    receipt_path = path.parent / artifact['path']
    receipt = json.loads(receipt_path.read_text())
    receipt['file_key'] = file_key
    receipt_path.write_text(json.dumps(receipt))
    artifact['sha256'] = file_hash(receipt_path)
    path.write_text(json.dumps(body))
    return path


def plugin_source_bundle(root):
    """Independent metadata/declarations for the frozen native-getter connector.

    This exports production collector fixtures, not test results or real Figma
    fidelity evidence. The source validator reconciles the exact output itself.
    """
    import json
    import shutil
    import subprocess
    from autocode_util import file_hash
    from tests.test_design_manifest import png
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node is required for the offline connector fixture')
    result = subprocess.run([node, str(Path(__file__).parents[1] / 'tools/test_figma_inventory_page.cjs'), '--fixture'],
                            text=True, capture_output=True, timeout=20, check=True)
    collected = json.loads(result.stdout)
    receipt, parts = collected['receipt'], collected['parts']
    source_path = root / 'source.json'
    source_path.write_text(json.dumps(receipt))
    # Metadata is a separate known document/page shape, not copied from source.
    xml = '''<CANVAS id="0:1" name="Main">
      <FRAME id="1:2" name="Home" width="1440" height="900">
        <TEXT id="I4:5;10:12" name="Label" width="100" height="20"/>
        <RECTANGLE id="1:7" name="Image" width="100" height="20"/>
        <VECTOR id="1:8" name="Icon" width="100" height="20"/>
        <INSTANCE id="1:9" name="Button" width="100" height="20"/>
      </FRAME>
      <COMPONENT_SET id="1:3" name="Button" width="100" height="20">
        <COMPONENT id="1:4" name="Type=primary" width="100" height="20"/>
      </COMPONENT_SET>
    </CANVAS>'''
    (root / 'page.xml').write_text(xml)
    (root / 'file.xml').write_text('<DOCUMENT>' + xml + '</DOCUMENT>')
    png(root / 'screen.png', 1440, 900)
    (root / 'context.txt').write_text('Synthetic mixed typography, component variant, modes and original source properties')
    (root / 'inter.woff2').write_bytes(b'offline fixture font, not a live font qualification')
    (root / 'icon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    png(root / 'image.png')
    def artifact(name):
        return {'path': name, 'sha256': file_hash(root / name)}
    fonts = []
    for row in receipt['nodes']:
        for font in row['fonts']:
            definition = {key: font[key] for key in ('family', 'style')}
            if not any(all(item[key] == definition[key] for key in definition) for item in fonts):
                fonts.append(dict(id='font-' + str(len(fonts)), **definition, status='available', artifact=artifact('inter.woff2')))
    assets = [dict(id=asset['id'], page_id='0:1', node_id=row['id'], name='Fixture source asset', mime_type=asset['mime_type'],
                   status='available', artifact=artifact('icon.svg' if asset['mime_type'] == 'image/svg+xml' else 'image.png'))
              for row in receipt['nodes'] for asset in row['assets']]
    transitions = [dict(source_node_id=row['id'], **transition) for row in receipt['nodes'] for transition in row['transitions']]
    file = dict(key='FILEA', revision='offline-plugin-fixture', metadata_xml=artifact('file.xml'),
                pages=[dict(id='0:1', name='Main', metadata_xml=artifact('page.xml'), source_json=artifact('source.json'))],
                screen_states=[dict(page_id='0:1', node_id='1:2', state='default', viewport=dict(width=1440, height=900, device_scale_factor=1))],
                components=[dict(key='LIBRARY', name='Button', source_page_id='0:1', source_node_id='1:3',
                                 variants=[dict(page_id='0:1', node_id='1:4', properties={'Type': 'primary'})])],
                fonts=fonts, assets=assets, variables=receipt['variables'], transitions=transitions)
    case = dict(id='home.default', file_key='FILEA', page_id='0:1', node_id='1:2', state='default', route='/fixture',
                implementation_paths=['src/home.js'], viewport=dict(width=1440, height=900, device_scale_factor=1),
                native_size=dict(width=1440, height=900), export_scale=1,
                artifacts=dict(screenshot=artifact('screen.png'), design_context=artifact('context.txt')),
                inventory_refs={section: [row['key' if section in ('components', 'variables') else 'id'] for row in file[section]]
                                for section in ('components', 'variables', 'fonts', 'assets', 'transitions')})
    body = dict(version=2, files=[file], cases=[case], responsive_targets=[])
    path = root / 'manifest.json'
    path.write_text(json.dumps(body))
    return path, body, receipt, parts
