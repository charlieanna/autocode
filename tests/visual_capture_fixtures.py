"""Synthetic capture receipts for guard tests; never real image acceptance."""
import json
from pathlib import Path
import struct
import uuid
import zlib
import autocode_util as util


def png(path, width=2, height=1):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    header = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    pixels = zlib.compress((b'\x00' + b'\x11\x22\x33' * width) * height)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + header + chunk(b'IDAT', pixels) + chunk(b'IEND', b''))


def make_capture(root, reference, case, *, asset='greet.py'):
    root = Path(root).resolve()
    # Where the capture instruction tells a reviewer to write its configs; acceptance refuses most other .autocode/ areas.
    configs = root / '.autocode' / 'evidence' / 'fixture-capture-configs'
    configs.mkdir(parents=True, exist_ok=True)
    fixture = configs / 'fixture.cjs'
    fixture.write_text('// Synthetic acquisition metadata for offline gate tests only.\n')
    config_path = configs / (case['id'] + '.json')
    binding = {key: case[key] for key in ('id', 'route', 'state', 'viewport')}
    config = {'reference_hash': reference, 'case': binding, 'fixture': str(fixture.relative_to(root)),
              'inputs': [], 'assets': [{'url': case['route'], 'path': asset}], 'build_command': [],
              'ready': [{'selector': 'body', 'attribute': 'data-state', 'equals': case['state']}]}
    util.atomic_json(config_path, config)
    folder = root / '.autocode' / 'captures' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    candidate = folder / 'candidate.png'
    viewport = case['viewport']
    png(candidate, *(round(viewport[key] * viewport['device_scale_factor']) for key in ('width', 'height')))
    browser = {'status': 'CAPTURED', 'setup': True, 'teardown': True, 'errors': [],
               'url': 'http://fixture.test' + case['route'], 'viewport': viewport, 'ready': config['ready'],
               'screenshot_sha256': util.file_hash(candidate),
               'responses': [{'asset': case['route'], 'sha256': util.file_hash(root / asset), 'status': 200}]}
    util.atomic_json(folder / 'browser.json', browser)
    tools = Path(__file__).resolve().parents[1] / 'tools'
    names = [str(config_path.relative_to(root)), str(fixture.relative_to(root)), asset]
    body = {'version': 1, 'kind': 'implementation_capture', 'reference_hash': reference, 'case': binding,
            'source_revision': util.snapshot(root)['revision'], 'config_ref': names[0],
            'inputs': {name: util.file_hash(root / name) for name in names}, 'build_executed': False,
            'engine': {name: util.file_hash(tools / name) for name in ('autocode_visual_capture.py', 'autocode_visual_browser.cjs')},
            'artifacts': {key: {'path': str((folder / name).relative_to(root)), 'sha256': util.file_hash(folder / name)}
                          for key, name in (('candidate', 'candidate.png'), ('browser', 'browser.json'))}}
    manifest = folder / 'manifest.json'
    util.atomic_json(manifest, body)
    return {'candidate_ref': str(candidate), 'capture_ref': str(manifest), 'capture_sha256': util.file_hash(manifest)}


def install_native_hook(provider, *, result='result', asset="'greet.py'", indent=''):
    """Extend only a test's private copied provider with synthetic capture evidence."""
    root = Path(__file__).resolve().parents[1]
    hook = f'''if data.get('figma_file') and stage == 'sol':
    sys.path.insert(0, {str(root)!r})
    from tests.visual_capture_fixtures import make_capture
    import autocode_visual_evidence as visual
    item = make_capture(Path.cwd(), visual.reference_hash({{'figma_file': data['figma_file']}}),
        {{'id': 'fixture', 'route': '/fixture', 'state': 'ready',
          'viewport': {{'width': 2, 'height': 1, 'device_scale_factor': 1}}}}, asset={asset})
    {result}['implementation_captures'] = [{{k: item[k] for k in ('capture_ref', 'capture_sha256')}}]
    for row in {result}['criterion_results']:
        row['evidence_refs'].append(item['candidate_ref'])
'''
    marker = f'Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps({result}))'
    source = Path(provider).read_text()
    assert source.count(marker) == 1
    source = source.replace(indent + marker, '\n'.join(indent + line for line in hook.splitlines()) + '\n' + indent + marker)
    Path(provider).write_text(source)
