"""Bounded, source-derived Python CLI output observations (issue #452).

Pure APIs: inventory(sources), bind(sources, proposals), verify(sources, manifest),
commands(sources, manifest), preserve(previous, proposed, replacements=()),
line_pattern(observation), output_reason(output, pattern, line).
Sources are already-authenticated human records supplied by the caller; this
module does not authenticate events or import an AutoCode controller. Supported
syntax is an explicit `script.py ARGS` declaration that prints/outputs `FORMAT`
"one per line" and exits 0: every printed line must have FORMAT, and one must be
the bound observed item. This is not a general natural-language/API verifier.

Proposals supply argv and argument-bound placeholders, never expectations,
regular expressions, programs or executable code. Canonical manifests can live
inside the existing hashed contract. Caller-authenticated replacements authorize
one exact old observation hash -> new hash/source hash, never a blanket change.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
import shlex

VERSION = 1
MAX_STEPS = 8
MAX_ARGUMENTS = 32
MAX_ARGUMENT_BYTES = 4096
_SOURCE_KINDS = frozenset({'task', 'conversation_user', 'user_answer', 'user_feedback',
                           'user_intervention', 'user_cli_edit'})
_SPAN = re.compile(r'`([^`\r\n]+)`')
_FENCE = re.compile(r'```.*?(?:```|$)', re.S)
_OUTPUT = re.compile(r'\b(?:prints?|outputs?)\b[^`]*?\b(?:as|in the format)\s*`([^`\r\n]+)`', re.I)
_PER_LINE = re.compile(r'\bone per line\b', re.I)
_EXIT_ZERO = re.compile(r'\bexits?\s+0\b(?!\.\d)', re.I)
_PLACEHOLDER = re.compile(r'[A-Z][A-Z_]*\Z')
_FORMAT_TOKEN = re.compile(r'\[[A-Za-z0-9_-]+(?:\|[A-Za-z0-9_-]+)+\]|(?<![\w.])[A-Za-z0-9_-]+(?:\|[A-Za-z0-9_-]+)+(?![\w.])|(?<![\w.])[A-Z][A-Z_]*(?![\w.])')
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]*\Z')


def _object(properties):
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties),
            'properties': properties}


_STRING = {'type': 'string'}
PROPOSAL_SCHEMA = _object({
    'declaration_id': _STRING,
    'criterion_ids': {'type': 'array', 'minItems': 1, 'items': _STRING, 'uniqueItems': True},
    'steps': {'type': 'array', 'minItems': 1, 'maxItems': MAX_STEPS,
              'items': _object({'argv': {'type': 'array', 'minItems': 1,
                                       'maxItems': MAX_ARGUMENTS, 'items': _STRING}})},
    'observe_step': {'type': 'integer', 'minimum': 0},
    'bindings': {'type': 'array', 'items': _object({
        'placeholder': _STRING, 'step': {'type': 'integer', 'minimum': 0},
        'argument': {'type': 'integer', 'minimum': 0}})},
})
PROPOSALS_SCHEMA = {'type': 'array', 'items': PROPOSAL_SCHEMA}
# These are trusted authorization records supplied by the caller, never fields
# accepted from a Planner/Builder observation proposal.
REPLACEMENT_SCHEMA = _object({
    'previous_hash': _STRING, 'replacement_hash': _STRING, 'replacement_source_sha256': _STRING})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode('utf-8')).hexdigest()


def _exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f'{label} needs exactly {sorted(keys)}')


def _string(value, label):
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError(f'{label} must be a nonempty NUL-free string')
    return value


def _index(value, size, label):
    if type(value) is not int or not 0 <= value < size:
        raise ValueError(f'{label} is outside its declared list')
    return value


def _program(value):
    _string(value, 'CLI program')
    path = PurePosixPath(value)
    if (not re.fullmatch(r'[A-Za-z0-9_./-]+\.py', value) or path.is_absolute()
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('CLI program must be a repository-relative Python script without traversal')
    return value


def _source_records(sources):
    if not isinstance(sources, list):
        raise ValueError('Trusted human sources must be a list')
    result, ids = [], set()
    for source in sources:
        _exact(source, ('id', 'kind', 'text'), 'Trusted human source')
        ident = _string(source['id'], 'Source ID')
        if (not _ID.fullmatch(ident) or ident in ids or not isinstance(source['kind'], str)
                or source['kind'] not in _SOURCE_KINDS):
            raise ValueError('Sources need unique stable IDs and a supported human-source kind')
        text = _string(source['text'], 'Source text')
        ids.add(ident)
        result.append({**source, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()})
    return result  # Caller-authenticated human chronology determines inheritance.


def _clause_end(text, start):
    # A semicolon outside an inline code span terminates the supported command
    # clause. Preserve offsets even when a quoted format itself has punctuation.
    quoted = False
    for pos in range(start, len(text)):
        if text[pos] == '`':
            quoted = not quoted
        elif not quoted and text[pos] in ';\r\n':
            return pos
    return len(text)


def _clause_start(text, end):
    quoted, start = False, 0
    for pos in range(end):
        if text[pos] == '`':
            quoted = not quoted
        elif not quoted and text[pos] in ';\r\n':
            start = pos + 1
    return start


def _invocation(text):
    # Normalize only the first shell token before deciding whether this span
    # names a Python script. Inline data examples may have arbitrary quotes;
    # script names may use quotes/escapes inside the token too.
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    try:
        program = next(lexer, None)
    except ValueError as error:
        # A malformed first token cannot be normalized. Preserve strict refusal
        # when its unfinished spelling still names a Python entrypoint, while
        # ordinary prose such as can't or Jo"e remains outside this grammar.
        head = text.split(maxsplit=1)
        unfinished = re.sub(r"""['"\\]""", '', head[0]) if head else ''
        quoted_program = re.match(r"""^\s*['"][^'"]*\.py(?=['"\s]|$)""", text)
        if unfinished.endswith('.py') or quoted_program:
            raise ValueError('Source CLI invocation has invalid quoting') from error
        return None
    if program is None or not program.endswith('.py'):
        return None
    try:
        words = shlex.split(text)
    except ValueError as error:
        raise ValueError('Source CLI invocation has invalid quoting') from error
    if len(words) < 2:
        return None
    return {'program': _program(words[0]), 'argv': words[1:]}


def inventory(sources):
    """Independently inventory supported output declarations with exact source bindings.

    Human records are supplied in authenticated chronological order. New
    declarations inherit earlier successful invocations for their program; a
    later record never changes an earlier declaration. Output order is canonical.
    Fenced examples do not create declarations. Character spans reference the original source bytes'
    decoded text; their content identity is the unmodified UTF-8 source hash.
    """
    result, earlier_commands = [], {}
    for source in _source_records(sources):
        text = source['text']
        scanned = _FENCE.sub(lambda match: re.sub(r'[^\r\n]', ' ', match.group()), text)
        invocations = []
        for span in _SPAN.finditer(scanned):
            invocation = _invocation(span[1])
            if invocation is None:
                continue
            end = _clause_end(scanned, span.end())
            tail = scanned[span.end():end]
            # Successful setup invocations are source declarations too; output
            # expectations may not invent an undeclared command or exit code.
            plain_tail = _SPAN.sub(lambda match: ' ' * len(match.group()), tail)
            if not _EXIT_ZERO.search(plain_tail):
                continue
            invocations.append({**invocation, 'span': [span.start(), end], 'tail': tail,
                                'tail_start': span.end()})
        for invocation in invocations:
            output = _OUTPUT.search(invocation['tail'])
            if output is None or not _PER_LINE.search(invocation['tail'][output.end():]):
                continue
            # Deliberately bounded declarative syntax. Descriptions of existing
            # broken output must not silently become the expected target.
            prefix = scanned[_clause_start(scanned, invocation['span'][0]):invocation['span'][0]]
            context = prefix + invocation['tail'][:output.start()]
            if re.search(r'\b(?:currently|broken|incorrectly|used to|must not|should not)\b', context, re.I):
                continue
            literal = output[1]
            literal_start = invocation['tail_start'] + output.start(1)
            commands = [{key: row[key] for key in ('argv',)} for row in invocations
                        if row['program'] == invocation['program']]
            commands = sorted(earlier_commands.get(invocation['program'], set())
                              | {tuple(row['argv']) for row in commands})
            declaration = {'source_id': source['id'], 'source_kind': source['kind'],
                           'source_sha256': source['sha256'], 'span': invocation['span'],
                           'quote': text[slice(*invocation['span'])],
                           'literal': literal, 'literal_span': [literal_start, literal_start + len(literal)],
                           'literal_sha256': hashlib.sha256(literal.encode('utf-8')).hexdigest(),
                           'program': invocation['program'], 'observe_argv': invocation['argv'],
                           'commands': [list(words) for words in commands]}
            declaration['id'] = 'brief-' + digest(declaration)[:24]
            result.append(declaration)
        for invocation in invocations:
            earlier_commands.setdefault(invocation['program'], set()).add(tuple(invocation['argv']))
    return sorted(result, key=lambda row: (row['source_id'], row['span'][0], row['id']))


def _match_argv(arguments, pattern):
    return len(arguments) == len(pattern) and all(
        _PLACEHOLDER.fullmatch(expected) or actual == expected
        for actual, expected in zip(arguments, pattern))


def _variables(declaration):
    return {word for command in declaration['commands'] for word in command if _PLACEHOLDER.fullmatch(word)}


def _render(literal, placeholder):
    """Literal bytes and finite alternatives exact; placeholder(name) renders the rest."""
    pieces, previous = [], 0
    for token in _FORMAT_TOKEN.finditer(literal):
        pieces.append(re.escape(literal[previous:token.start()]))
        name = token.group()
        if name.startswith('['):
            pieces.append(r'\[(?:' + '|'.join(re.escape(option) for option in name[1:-1].split('|')) + r')\]')
        elif '|' in name:
            pieces.append('(?:' + '|'.join(re.escape(option) for option in name.split('|')) + ')')
        else:
            pieces.append(placeholder(name))
        previous = token.end()
    pieces.append(re.escape(literal[previous:]))
    return ''.join(pieces)


def _format_pattern(declaration, steps, bindings):
    variables, used = _variables(declaration), set()

    def placeholder(name):
        if name not in variables and name != 'ID':
            return re.escape(name)  # e.g. SUCCESS is literal, not a wildcard
        if name in bindings:
            binding = bindings[name]
            used.add(name)
            return re.escape(steps[binding['step']]['argv'][binding['argument']])
        if name == 'ID':
            return r'[^\s]+'  # opaque source-declared ID; never invent ID1
        raise ValueError(f'Output placeholder {name} must be tied to an invocation argument')

    pattern = _render(declaration['literal'], placeholder)
    if used != set(bindings):
        raise ValueError('Placeholder bindings must occur in the source-derived output template')
    return pattern


def line_pattern(observation):
    """Any one listed line of a canonical observation's `one per line` output.

    The sealed item pattern names one item; a listing prints every item
    (issue #452 live runs). Each line keeps the declaration's literal bytes and
    finite alternatives, ID stays opaque, and every other argument placeholder
    may only be a value the observation's own steps supplied up to the listing.
    Derived from the verified manifest, never stored in it or model-supplied.
    """
    declaration, proposal = observation['declaration'], observation['proposal']
    variables, supplied = _variables(declaration), {}
    for step in proposal['steps'][:proposal['observe_step'] + 1]:
        # bind() already required exactly one source-declared match per step.
        command = next((pattern for pattern in declaration['commands'] if _match_argv(step['argv'], pattern)), None)
        if command is None:
            raise ValueError('Invocation must uniquely match a source-declared successful CLI command')
        for word, value in zip(command, step['argv']):
            if word in variables:
                supplied.setdefault(word, set()).add(value)

    def placeholder(name):
        if name == 'ID':
            return r'[^\s]+'
        if name not in variables:
            return re.escape(name)
        values = sorted(supplied.get(name, ()))
        return '(?:' + '|'.join(re.escape(value) for value in values) + ')' if values else '(?!)'

    return _render(declaration['literal'], placeholder)


def output_reason(output, pattern, line):
    """'' when stdout bytes are the declared `one per line` listing, else why not.

    One optional final LF or CRLF; every line fullmatches `line` with one kind of
    line ending; at least one line is exactly the observed item `pattern`. No
    other normalization. _RUNNER repeats this rule inside the clean replay.
    """
    crlf = output.endswith(b'\r\n')
    body = output[:-2] if crlf else output[:-1] if output.endswith(b'\n') else output
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        return 'CLI output is not UTF-8 text in this bounded slice'
    rows = text.split('\r\n' if crlf else '\n')
    if (any('\r' in row or '\n' in row or re.fullmatch(line, row) is None for row in rows)
            or not any(re.fullmatch(pattern, row) for row in rows)):
        return 'CLI output differs from the original brief format'
    return ''


def _bind_one(declaration, proposal):
    _exact(proposal, PROPOSAL_SCHEMA['required'], 'Observation proposal')
    criteria = proposal['criterion_ids']
    if (not isinstance(criteria, list) or not criteria or any(not isinstance(cid, str) or not _ID.fullmatch(cid) for cid in criteria)
            or len(set(criteria)) != len(criteria)):
        raise ValueError('Criterion IDs must be unique nonempty IDs')
    steps = proposal['steps']
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f'Observation needs 1..{MAX_STEPS} invocation steps')
    patterns = []
    for step in steps:
        _exact(step, ('argv',), 'Invocation step')
        arguments = step['argv']
        if not isinstance(arguments, list) or not 1 <= len(arguments) <= MAX_ARGUMENTS:
            raise ValueError('Invocation argv must be a bounded nonempty list')
        for argument in arguments:
            _string(argument, 'Invocation argument')
            if len(argument.encode('utf-8')) > MAX_ARGUMENT_BYTES:
                raise ValueError('Invocation argument exceeds the bounded CLI slice')
        matches = [pattern for pattern in declaration['commands'] if _match_argv(arguments, pattern)]
        if len(matches) != 1:
            raise ValueError('Invocation must uniquely match a source-declared successful CLI command')
        patterns.append(matches[0])
    observe = _index(proposal['observe_step'], len(steps), 'Observed step')
    if not _match_argv(steps[observe]['argv'], declaration['observe_argv']):
        raise ValueError('Observed step must invoke the source-declared output command')
    raw_bindings = proposal['bindings']
    if not isinstance(raw_bindings, list):
        raise ValueError('Placeholder bindings must be a list')
    bindings = {}
    for binding in raw_bindings:
        _exact(binding, ('placeholder', 'step', 'argument'), 'Placeholder binding')
        name = _string(binding['placeholder'], 'Placeholder')
        step = _index(binding['step'], len(steps), 'Binding step')
        argument = _index(binding['argument'], len(steps[step]['argv']), 'Binding argument')
        if (name in bindings or step > observe or patterns[step][argument] != name
                or not _PLACEHOLDER.fullmatch(name)):
            raise ValueError('A binding must uniquely name the corresponding source argument placeholder')
        bindings[name] = dict(binding)
    pattern = _format_pattern(declaration, steps, bindings)
    observation = {'declaration': declaration, 'proposal': json.loads(json.dumps(proposal)), 'pattern': pattern}
    observation['hash'] = digest(observation)
    return observation


def bind(sources, proposals, *, inactive=()):
    """Bind every active declaration; only caller-authenticated IDs may be inactive.

    Inactive IDs are not proposal fields. The caller derives them from actual
    authenticated amendment receipts, then checks preserve() before approval.
    Keep the full source inventory binding even when one obligation is replaced.
    """
    full_inventory = inventory(sources)
    if not isinstance(inactive, (list, tuple)) or any(not isinstance(ident, str) for ident in inactive):
        raise ValueError('Inactive declaration IDs must be caller-authenticated IDs')
    if len(set(inactive)) != len(inactive) or not set(inactive) <= {row['id'] for row in full_inventory}:
        raise ValueError('Inactive declarations must be known and unique')
    declared = [row for row in full_inventory if row['id'] not in inactive]
    if not isinstance(proposals, list):
        raise ValueError('Observation proposals must be a list')
    by_id = {row['id']: row for row in declared}
    proposed = {}
    for proposal in proposals:
        _exact(proposal, PROPOSAL_SCHEMA['required'], 'Observation proposal')
        ident = proposal['declaration_id']
        if not isinstance(ident, str) or ident not in by_id or ident in proposed:
            raise ValueError('Observation declarations must be known and unique')
        proposed[ident] = proposal
    if set(proposed) != set(by_id):
        raise ValueError('Every supported original-brief declaration needs an observation')
    manifest = {'version': VERSION, 'inventory_hash': digest(full_inventory),
                'inactive_declaration_ids': sorted(inactive),
                'observations': [_bind_one(row, proposed[row['id']]) for row in declared]}
    manifest['hash'] = digest(manifest)
    return manifest


def verify(sources, manifest, *, inactive=()):
    """Recompute canonical source/expectation bindings; trust no persisted regex or hash."""
    _exact(manifest, ('version', 'inventory_hash', 'inactive_declaration_ids', 'observations', 'hash'), 'Acceptance manifest')
    if type(manifest['version']) is not int or manifest['version'] != VERSION or not isinstance(manifest['observations'], list):
        raise ValueError('Unsupported acceptance manifest')
    proposals = []
    for row in manifest['observations']:
        _exact(row, ('declaration', 'proposal', 'pattern', 'hash'), 'Canonical observation')
        proposals.append(row['proposal'])
    canonical = bind(sources, proposals, inactive=inactive)
    if manifest != canonical:
        raise ValueError('Original-brief source or canonical observation binding changed')
    return canonical


_RUNNER = r'''import base64,json,pathlib,re,subprocess,sys,tempfile
case=json.loads(sys.argv[1])
root=pathlib.Path.cwd().resolve()
entry=root.joinpath(*case['program'].split('/'))
if any(path.is_symlink() for path in (entry,*entry.parents) if path!=root and root in path.parents) or not entry.resolve().is_relative_to(root) or not entry.is_file():
    print(json.dumps({'verdict':'FAIL','reason':'candidate entrypoint escapes the clean source tree'}))
    sys.exit(1)
rows=[]
reason=''
with tempfile.TemporaryDirectory(prefix='.brief-acceptance-',dir=root) as working:
    for step in case['steps']:
        try:
            result=subprocess.run([sys.executable,str(entry),*step['argv']],cwd=working,capture_output=True,timeout=case['timeout'])
        except subprocess.TimeoutExpired:
            reason='CLI invocation timed out'
            break
        rows.append({'argv':step['argv'],'exit_code':result.returncode,'stdout_base64':base64.b64encode(result.stdout).decode(),'stderr_base64':base64.b64encode(result.stderr).decode()})
        if result.returncode!=0:
            reason='source-declared successful CLI invocation exited nonzero'
            break
    if not reason:
        output=base64.b64decode(rows[case['observe_step']]['stdout_base64'])
        # output_reason(): one optional final line ending is a printing convention,
        # not permission to strip spaces, extra lines, brackets or other source
        # literal bytes. Every listed line has the declared format; one is the item.
        crlf=output.endswith(b'\r\n')
        body=output[:-2] if crlf else output[:-1] if output.endswith(b'\n') else output
        try:
            text=body.decode('utf-8')
        except UnicodeDecodeError:
            reason='CLI output is not UTF-8 text in this bounded slice'
        else:
            lines=text.split('\r\n' if crlf else '\n')
            if any('\r' in line or '\n' in line or re.fullmatch(case['line_pattern'],line) is None for line in lines) or not any(re.fullmatch(case['pattern'],line) for line in lines):
                reason='CLI output differs from the original brief format'
print(json.dumps({'verdict':'FAIL' if reason else 'PASS','observation_hash':case['hash'],'steps':rows,'reason':reason},sort_keys=True))
sys.exit(1 if reason else 0)
'''


def commands(sources, manifest, *, inactive=(), python='python3', timeout=15):
    """Runner-owned commands only; caller provides its qualified Python and timeout."""
    canonical = verify(sources, manifest, inactive=inactive)
    _string(python, 'Qualified Python')
    if (not re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', PurePosixPath(python).name)
            or any(character in python for character in '\r\n\0') or '..' in PurePosixPath(python).parts):
        raise ValueError('Caller must supply a qualified Python interpreter path')
    if type(timeout) not in (int, float) or not 0 < timeout <= 900:
        raise ValueError('CLI step timeout must be bounded by the caller existing replay limit')
    result = []
    for observation in canonical['observations']:
        payload = {'program': observation['declaration']['program'],
                   'steps': observation['proposal']['steps'],
                   'observe_step': observation['proposal']['observe_step'],
                   'pattern': observation['pattern'], 'line_pattern': line_pattern(observation),
                   'hash': observation['hash'], 'timeout': timeout}
        result.append(shlex.join([python, '-I', '-c', _RUNNER, json.dumps(payload, sort_keys=True, ensure_ascii=False)]))
    return result


def _manifest_observations(manifest):
    _exact(manifest, ('version', 'inventory_hash', 'inactive_declaration_ids', 'observations', 'hash'), 'Acceptance manifest')
    if type(manifest['version']) is not int or manifest['version'] != VERSION or not isinstance(manifest['observations'], list):
        raise ValueError('Unsupported acceptance manifest')
    body = {key: value for key, value in manifest.items() if key != 'hash'}
    if manifest['hash'] != digest(body):
        raise ValueError('Acceptance manifest hash changed')
    result = {}
    for observation in manifest['observations']:
        _exact(observation, ('declaration', 'proposal', 'pattern', 'hash'), 'Canonical observation')
        body = {key: value for key, value in observation.items() if key != 'hash'}
        if observation['hash'] != digest(body) or observation['hash'] in result:
            raise ValueError('Observation hash changed or repeated')
        result[observation['hash']] = observation
    return result


def preserve(previous, proposed, *, replacements=()):
    """Require exact caller-authenticated replacements for changed/removed proof.

    Validate both manifests against their respective trusted source inventories
    with verify() first. Replacements are authorization facts supplied by the
    caller after checking actual non-delegated user event/approval provenance.
    New independent observations may be added, never used to erase old proof.
    """
    before, after = _manifest_observations(previous), _manifest_observations(proposed)
    authorized, used_new = {}, set()
    for replacement in replacements:
        _exact(replacement, REPLACEMENT_SCHEMA['required'], 'Authenticated replacement')
        if any(not isinstance(value, str) or not _HEX.fullmatch(value) for value in replacement.values()):
            raise ValueError('Authenticated replacement needs exact SHA256 identities')
        old, new = replacement['previous_hash'], replacement['replacement_hash']
        if old in authorized or new in used_new or old not in before or old in after or new not in after:
            raise ValueError('Replacement must identify one actual old-to-new observation change')
        if after[new]['declaration']['source_sha256'] != replacement['replacement_source_sha256']:
            raise ValueError('Replacement source content hash does not match the new observation')
        authorized[old] = new
        used_new.add(new)
    if set(before) - set(after) != set(authorized):
        raise ValueError('An accepted original-brief observation changed without an exact authenticated replacement')
    return proposed
