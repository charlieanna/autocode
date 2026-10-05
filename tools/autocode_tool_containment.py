"""Kernel containment for OpenCode tool subprocesses, not its authenticated client.

Strict mode deliberately exposes only the native shell tool. In-process file,
plugin and MCP tools cannot inherit a child-process sandbox and are not covered.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import re
import shutil
import subprocess
import sys
import uuid


SUPPORTED_VERSION = "1.18.33"
SYSTEM_READ_ROOTS = ("/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/lib",
                     "/usr/share", "/System/Library", "/Library/Apple/System/Library")
# prepare() names each launch's control directory uuid4().hex; nothing else matches.
CONTROL_NAME = re.compile('tool-containment-[0-9a-f]{32}')


def _path(value):
    path = Path(value).absolute()
    if path != path.resolve():
        # /var is the standard macOS alias, not a caller-controlled symlink.
        if str(path).startswith('/var/') and str(path.resolve()) == '/private' + str(path):
            return path.resolve()
        raise ValueError(f"Tool containment refuses a symlinked authority path: {path}")
    return path


def policy(workspace, scratch, *, allow_write=False, read_roots=(), protected_paths=()):
    """Return a deny-default Seatbelt profile. No model command is interpreted here."""
    root, scratch = _path(workspace), _path(scratch)
    private = root / '.autocode'
    if not scratch.is_relative_to(private) or scratch == private:
        raise ValueError("Tool scratch must be a fresh owned child of workspace/.autocode")
    reads = {root, scratch, *(Path(p).resolve() for p in SYSTEM_READ_ROOTS)}
    reads.update(_path(p) for p in read_roots)
    if any(p == Path('/') or p == Path.home() for p in reads):
        raise ValueError("Tool containment refuses an unscoped read root")
    protected = {private, root / '.git', root / '.opencode', root / 'opencode.json',
                 root / 'opencode.jsonc', *(_path(p) for p in protected_paths),
                 *(_path(p) for p in read_roots if _path(p).is_relative_to(root))}
    literal = lambda p: '(literal ' + json.dumps(str(p)) + ')'
    subpath = lambda p: '(subpath ' + json.dumps(str(p)) + ')'
    lines = ['(version 1)', '(deny default)', '(allow process-exec process-fork)',
             '(allow signal (target same-sandbox))',
             '(allow file-read-metadata)', '(allow file-read* (literal "/"))',
             '(allow file-read* ' + ' '.join(subpath(p) for p in sorted(reads)) + ')',
             '(allow file-read* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))',
             '(allow file-write* (literal "/dev/null") ' + subpath(scratch) + ')']
    if allow_write:
        lines.append('(allow file-write* (require-all ' + subpath(root) + ' ' + ' '.join(
            '(require-not ' + subpath(p) + ')' for p in sorted(protected)) + '))')
    # Deny metadata changes on authority directories too: renaming an ancestor
    # could otherwise move a writable subtree over the runner's evidence.
    lines.extend('(deny file-write* ' + literal(p) + ')' for p in sorted(protected))
    lines.append('(deny file-read* (regex "(^|/)\\\\.env($|\\\\.)") '
                 '(regex "(^|/)(auth\\\\.json|id_rsa|id_ed25519)$"))')
    return '\n'.join(lines) + '\n'


def _reject_loopback_request(checks, authority):
    # Seatbelt's "localhost" matches non-loopback addresses belonging to this
    # host too. Exact-command dispatch cannot make that an exact-IP sandbox.
    if not isinstance(checks, (list, tuple)):
        raise ValueError('Loopback checks must be an explicit command list')
    if checks or authority is not None:
        raise RuntimeError('Exact-IP loopback containment is unsupported: macOS Seatbelt localhost '
                           'also permits non-loopback host addresses; use a runner-mediated approved check')


def prepare(workspace, *, allow_write=False, read_roots=(), protected_paths=(), environment=None,
            loopback_checks=(), loopback_authority=None):
    """Create a fresh runner-owned policy and shell; return only nonsecret metadata."""
    _reject_loopback_request(loopback_checks, loopback_authority)
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        raise RuntimeError('Strict native tool containment requires macOS sandbox-exec; no provider launched')
    root = _path(workspace)
    parent = root / '.autocode'
    if parent.is_symlink():
        raise ValueError('Tool containment refuses a symlinked .autocode directory')
    parent.mkdir(exist_ok=True)
    control = parent / ('tool-containment-' + uuid.uuid4().hex)
    control.mkdir(mode=0o700)
    scratch = control / 'scratch'
    scratch.mkdir(mode=0o700)
    profile = control / 'policy.sb'
    profile.write_text(policy(root, scratch, allow_write=allow_write, read_roots=read_roots,
                              protected_paths=protected_paths))
    shell = control / 'shell'
    # env -i separates model-client credentials and startup hooks from tool
    # authority. The authenticated OpenCode parent's environment is unchanged.
    env = environment or {}
    safe = {'PATH': env.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin'),
            'HOME': str(scratch), 'TMPDIR': str(scratch), 'TMP': str(scratch), 'TEMP': str(scratch),
            'SHELL': '/bin/bash', 'BASH_ENV': '/dev/null', 'ENV': '/dev/null',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1', 'CI': '1',
            'LANG': 'en_US.UTF-8', 'AUTOCODE_TOOL_CONTAINMENT_ID': control.name,
            'AUTOCODE_OUTPUT_STORE': str(scratch / 'output-store')}
    if env.get('AUTOCODE_CAPTURE_CONTEXT'):
        context = json.loads(env['AUTOCODE_CAPTURE_CONTEXT'])
        if not isinstance(context, dict) or not all(isinstance(context.get(key), str)
                                                    for key in ('attempt', 'nonce', 'source_revision')):
            raise ValueError('Invalid runner capture context for tool containment')
        safe['AUTOCODE_CAPTURE_CONTEXT'] = json.dumps(context)
    argv = ['/usr/bin/env', '-i', *(key + '=' + value for key, value in safe.items()),
            '/usr/bin/sandbox-exec', '-f', str(profile), '/bin/bash', '--noprofile', '--norc']
    shell.write_text('#!/bin/sh -p\n'
                     'if [ "$#" -ne 2 ] || [ "$1" != "-c" ]; then exit 126; fi\n'
                     'exec ' + shlex.join(argv) + ' -c "$2"\n')
    shell.chmod(0o500)
    profile.chmod(0o400)
    probe = subprocess.run([str(shell), '-c', 'exit 0'], cwd=root, capture_output=True, text=True, timeout=10)
    if probe.returncode:
        raise RuntimeError(f'Strict native tool sandbox is unavailable (exit {probe.returncode}): '
                           + probe.stderr.strip())
    return {'version': 1, 'platform': sys.platform, 'workspace': str(root),
            'shell': str(shell), 'profile': str(profile), 'scratch': str(scratch),
            'allow_write': allow_write, 'tools': ['bash']}


def shell_permissions(rules):
    """Intersect effective OpenCode permissions with the contained tool surface.

    OpenCode uses ordered wildcard rules, not shell-command substring filters.
    Reconstruct ONLY the already effective bash rules; never add an allow.
    """
    if not isinstance(rules, list) or not rules:
        raise RuntimeError('Strict containment needs an auditable effective permission policy')
    bash = {}
    for rule in rules:
        if not isinstance(rule, dict) or not all(isinstance(rule.get(k), str)
                                               for k in ('permission', 'pattern', 'action')):
            raise RuntimeError('Strict containment received an unsupported permission rule')
        if rule['action'] not in ('allow', 'deny', 'ask'):
            raise RuntimeError('Strict containment received an unknown permission action')
        pattern = re.escape(rule['permission']).replace(r'\*', '.*').replace(r'\?', '.')
        if re.fullmatch(pattern, 'bash'):
            # Re-insertion retains last-match ordering, including repeated keys.
            bash.pop(rule['pattern'], None)
            bash[rule['pattern']] = rule['action']
    if not bash:
        raise RuntimeError('Strict containment cannot establish the native bash permission')
    return {'*': 'deny', 'bash': bash, 'external_directory': 'deny'}


def configure(command, environment, workspace, *, allow_write, request):
    """Constrain one launch and prove its *actual* native bash path before dispatch.

    Only model-free debug commands run here. No approval is auto-granted: an
    approval-bearing shell policy blocks conformance rather than using debug's
    auto-allow-ask behavior. The original model, effort and command are untouched.
    """
    if sys.platform != 'darwin':
        raise RuntimeError('Strict native tool containment requires macOS; no provider launched')
    if not isinstance(request, dict) or set(request) - {
            'read_roots', 'protected_paths', 'loopback_checks', 'loopback_authority'}:
        raise ValueError('Unsupported strict tool containment request')
    _reject_loopback_request(request.get('loopback_checks', ()), request.get('loopback_authority'))
    if '--session' in command:
        raise RuntimeError('Strict native tool containment requires a fresh session; resumed transport is unqualified')
    executable = shutil.which(command[0], path=environment.get('PATH', ''))
    if not executable:
        raise RuntimeError('Strict native tool containment cannot find the provider executable')
    version = subprocess.run([executable, '--version'], env=environment, cwd=workspace,
                             capture_output=True, text=True, timeout=15)
    if version.returncode or version.stdout.strip() != SUPPORTED_VERSION:
        raise RuntimeError('Strict native tool containment supports only conformance-tested OpenCode ' + SUPPORTED_VERSION)
    agent = command[command.index('--agent') + 1]
    child = dict(environment)
    config = json.loads(child['OPENCODE_CONFIG_CONTENT'])
    definition = config['agent'][agent]
    definition['model'] = command[command.index('--model') + 1]
    child['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
    prefix = [executable, 'debug', 'agent', agent]

    def debug(args=()):
        result = subprocess.run([*prefix, *args], cwd=workspace, env=child,
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError('Strict native tool conformance failed before provider launch (exit '
                               + str(result.returncode) + ')')
        try:
            return json.loads(result.stdout)
        except ValueError as error:
            raise RuntimeError('Native tool conformance did not return auditable JSON') from error

    original = debug()
    permissions = shell_permissions(original.get('permission'))
    boundary = prepare(workspace, allow_write=allow_write, environment=child,
                       read_roots=request.get('read_roots', ()),
                       protected_paths=request.get('protected_paths', ()),
                       loopback_checks=request.get('loopback_checks', ()),
                       loopback_authority=request.get('loopback_authority'))
    config['shell'] = boundary['shell']
    definition['permission'] = permissions
    definition.pop('tools', None)
    child['SHELL'] = boundary['shell']
    child['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
    actual = debug()
    if shell_permissions(actual.get('permission')) != permissions:
        raise RuntimeError('Strict containment could not preserve the effective shell restrictions')
    tools = actual.get('tools', {})
    if not isinstance(tools, dict) or tools.get('bash') is not True or any(
            value is not False for name, value in tools.items() if name != 'bash'):
        raise RuntimeError('Strict containment could not disable every uncontained native tool')
    # The final restrictive wildcard shadows all non-shell asks. A shell ask
    # must remain a real approval, never be silently approved by debug agent.
    if any(action == 'ask' for action in permissions['bash'].values()):
        raise RuntimeError('Native debug would auto-approve a shell ask; strict conformance is blocked')
    control = Path(boundary['profile']).parent
    sentinel = control / 'conformance-forbidden'
    sentinel.write_text('runner-owned sentinel\n')
    scratch = Path(boundary['scratch']) / 'conformance-capture'
    probe = ('test "$AUTOCODE_TOOL_CONTAINMENT_ID" = ' + shlex.quote(control.name)
             + ' && ! (: > ' + shlex.quote(str(sentinel)) + ')'
             + ' && (: > ' + shlex.quote(str(scratch)) + ')')
    response = debug(['--tool', 'bash', '--params', json.dumps({
        'command': probe, 'description': 'AutoCode kernel containment conformance (no model)',
        'workdir': str(Path(workspace).resolve()), 'timeout': 10000})])
    result = response.get('result', {})
    if (response.get('input', {}).get('command') != probe
            or type(result.get('metadata', {}).get('exit')) is not int
            or result['metadata']['exit'] != 0 or not scratch.is_file()
            or sentinel.read_text() != 'runner-owned sentinel\n'
            or not re.search(r'Operation not permitted|Permission denied', result.get('output', ''))):
        raise RuntimeError('The actual native bash tool did not demonstrate kernel containment')
    proof = {'command': probe, 'tool': 'bash', 'exit': result['metadata']['exit'],
             'output': result.get('output'), 'native_version': SUPPORTED_VERSION,
             'profile_sha256': hashlib.sha256(Path(boundary['profile']).read_bytes()).hexdigest(),
             'shell_sha256': hashlib.sha256(Path(boundary['shell']).read_bytes()).hexdigest()}
    proof_path = control / 'conformance.json'
    proof_path.write_text(json.dumps(proof, indent=2) + '\n')
    boundary.update({'conformance': str(proof_path), 'profile_sha256': proof['profile_sha256'],
                     'shell_sha256': proof['shell_sha256'], 'native_version': SUPPORTED_VERSION,
                     'conformance_sha256': hashlib.sha256(proof_path.read_bytes()).hexdigest()})
    verify(boundary)
    child['AUTOCODE_TOOL_CONTAINMENT'] = json.dumps(boundary)
    return child, boundary


def verify(boundary):
    """Recheck runner-owned containment bytes immediately before provider launch."""
    if not isinstance(boundary, dict) or boundary.get('version') != 1:
        raise RuntimeError('Missing or unsupported native tool containment manifest')
    if sys.platform != 'darwin' or boundary.get('native_version') != SUPPORTED_VERSION:
        raise RuntimeError('Native tool containment platform or transport is unsupported')
    try:
        workspace = _path(boundary['workspace'])
        profile, shell, scratch = (_path(boundary[name]) for name in ('profile', 'shell', 'scratch'))
        control = profile.parent
        if (control.parent != workspace / '.autocode'
                or not CONTROL_NAME.fullmatch(control.name)
                or profile.name != 'policy.sb' or shell != control / 'shell'
                or scratch != control / 'scratch' or not scratch.is_dir()):
            raise ValueError('Unexpected containment authority layout')
        if any(key.startswith('loopback_') for key in boundary):
            raise ValueError('Unsupported loopback capability in containment manifest')
        for name in ('profile', 'shell', 'conformance'):
            path = _path(boundary[name])
            expected = control / {'profile': 'policy.sb', 'shell': 'shell', 'conformance': 'conformance.json'}[name]
            if path != expected or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != boundary[name + '_sha256']:
                raise ValueError('Containment authority bytes changed')
    except (KeyError, OSError, ValueError) as error:
        raise RuntimeError('Native tool containment authority changed; no provider launched') from error
    return boundary


def recorded_scratch(records, workspace):
    """The scratch directories these launch records say prepare() made for them, in its exact layout.

    A contained stage is told to capture evidence there (provider_launch.containment_prompt), so such
    evidence belongs to the run whose stage records are passed; another run's scratch is not in them.
    Lexical only: callers still refuse symlinks on the evidence path itself.
    """
    private = Path(workspace) / '.autocode'
    found = []
    for record in records:
        boundary = record.get('tool_containment') if isinstance(record, dict) else None
        scratch = boundary.get('scratch') if isinstance(boundary, dict) else None
        if not isinstance(scratch, str):
            continue
        path = Path(scratch)
        if path.name == 'scratch' and path.parent.parent == private and CONTROL_NAME.fullmatch(path.parent.name):
            found.append(path)
    return tuple(dict.fromkeys(found))
