"""Exact file sections and original-output retrieval: autocode output --help."""
import argparse
import base64
import json
import os
import shlex
import sys
from pathlib import Path

try:
    from . import autocode_output_store as store
except ImportError:
    import autocode_output_store as store


def representation(data):
    try:
        return {'encoding': 'utf-8', 'content': data.decode('utf-8')}
    except UnicodeDecodeError:
        return {'encoding': 'base64', 'content': base64.b64encode(data).decode('ascii')}


def section(data, start, end):
    lines = data.splitlines(keepends=True)
    total = len(lines)
    if start < 1 or end is not None and end < start or start > max(total, 1):
        raise ValueError('Line range is outside the exact original')
    last = min(end if end is not None else total, total)
    omitted = []
    if start > 1:
        omitted.append({'start_line': 1, 'end_line': start - 1})
    if last < total:
        omitted.append({'start_line': last + 1, 'end_line': total})
    return b''.join(lines[start-1:last]), {'start_line': start, 'end_line': last,
                                         'total_lines': total, 'omitted_sections': omitted}


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, default=os.environ.get('AUTOCODE_OUTPUT_STORE'))
    parser.add_argument('--mode', choices=('raw', 'conservative'), default=os.environ.get('AUTOCODE_OUTPUT_MODE', 'conservative'))
    commands = parser.add_subparsers(dest='action', required=True)
    read = commands.add_parser('read', help='Read exact file lines and retain the complete original bytes')
    read.add_argument('file', type=Path)
    read.add_argument('--known-sha256', help='Refer to a verified retained copy when this full-file hash is unchanged')
    get = commands.add_parser('retrieve', help='Retrieve exact retained bytes, including from before a file changed')
    get.add_argument('sha256')
    get.add_argument('--raw', action='store_true', help='Emit original bytes directly, without a JSON envelope')
    for command in (read, get):
        command.add_argument('--start-line', type=int, default=1)
        command.add_argument('--end-line', type=int)
    commands.add_parser('status', help='Show display measurements; these are not model tokens or cost savings')
    args = parser.parse_args(argv)
    try:
        root = store.store_root(args.store)
        if args.action == 'status':
            print(json.dumps(store.summary(root)))
            return 0
        if args.action == 'read':
            path = args.file.resolve()
            if not path.is_relative_to(Path.cwd().resolve()) or not path.is_file():
                raise ValueError('File reads must stay inside this workspace')
            data = path.read_bytes()
            baseline, _ = section(data, args.start_line, args.end_line)
            try:
                original = store.retain(root, data)
            except (OSError, ValueError) as error:
                # A broken store must not turn a healthy read into lost context.
                print(json.dumps({'file': str(path), 'fallback': 'complete_original',
                                  'retention_error': str(error), **representation(data)}))
                return 0
            # retain verified retrieval and byte equality before this omission.
            unchanged = args.mode == 'conservative' and args.known_sha256 == original['sha256']
            end = args.end_line
            if end is None and args.mode == 'conservative':
                end = args.start_line + 199
            selected, coordinates = section(data, args.start_line, end)
            if unchanged:
                selected = b''
                coordinates['omitted_sections'] = ([{'start_line': 1, 'end_line': coordinates['total_lines']}]
                                                   if data else [])
            payload = {'file': str(path), 'original': original, 'unchanged': unchanged,
                       **coordinates, **representation(selected)}
        else:
            data = store.retrieve(root, args.sha256)
            selected, coordinates = section(data, args.start_line, args.end_line)
            if args.raw:
                sys.stdout.buffer.write(selected)
                store.record(root, operation='retrieve', raw_bytes=len(selected), displayed_bytes=len(selected), mode='raw')
                return 0
            payload = {'original': {'sha256': args.sha256, 'bytes': len(data)},
                       **coordinates, **representation(selected)}
        payload['retrieval'] = shlex.join([sys.executable, str(Path(__file__).with_name('autocode.py')),
                                          'output', '--store', str(root), 'retrieve', store.digest(data), '--raw'])
        output = json.dumps(payload, ensure_ascii=False) + '\n'
        sys.stdout.write(output)
        store.record(root, operation=args.action, raw_bytes=len(baseline) if args.action == 'read' else len(selected), displayed_bytes=len(output.encode()),
                     mode=args.mode, omitted=len(payload['omitted_sections']))
        return 0
    except (OSError, ValueError) as error:
        print('Exact output unavailable: ' + str(error) + '; use the original file/native read tool.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(cli())
