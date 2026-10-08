"""Fixed, bounded lifecycle observations; callers own source and contract pins.

This compiler accepts no programs, schedules or expected values from a model.
The isolated supervisor imports only stdlib; candidate APIs run in owned children.

When the person also promised atomicity under contention (the declaration's
promises include CONTENTION, #451), the same supervisor then releases three more
interpreters together at shared barriers on a second database and checks that
each racing write took effect exactly once. A race can be missed, so a PASS is
evidence that no lost or duplicated write showed up, not a proof of atomicity.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import signal

PROTOCOLS = frozenset({'lease_queue_lifecycle_v1', 'transactional_outbox_lifecycle_v1'})
# The promise a declaration carries when its source states atomicity under contention.
CONTENTION = 'contention_atomicity'
MAX_TRANSCRIPT_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
_ROLES = {
    'lease_queue_lifecycle_v1': {'enqueue', 'claim', 'ack', 'nack', 'pending'},
    'transactional_outbox_lifecycle_v1': {'create_order', 'orders', 'pending', 'publish'},
}
# What each fixed protocol does, in words, for a repair to reproduce (autocode_risk_findings).
# It restates the programs below; it is never executed or parsed.
DESCRIPTIONS = {
    'lease_queue_lifecycle_v1': (
        'Three fresh Python interpreters open the class on one database file in turn. 1 holder: '
        "enqueue('risk-job-a', 'risk-payload-a') and ('risk-job-b', 'risk-payload-b') return True, True; "
        'claim(11, 7) returns risk-job-a with deadline 18; pending() is 2; then the process is killed '
        "with SIGKILL. 2 reclaim: claim(18, 7) returns risk-job-a again with a different token and "
        "deadline 25; ack and nack with the killed holder's token at now=18 both return False; pending() "
        "is 2; replaying the enqueue returns False and a conflicting payload raises ValueError. 3 finish: "
        'ack with the current token at now=19 returns True, then False; pending() is 1; claim(19, 7) '
        "returns risk-job-b with deadline 26 and its ack at now=20 is True; pending() is 0; both enqueue "
        'replays return False and a conflict raises ValueError.'),
    'transactional_outbox_lifecycle_v1': (
        'Three fresh Python interpreters open the class on one database file in turn. 1 seed: '
        "create_order for ('risk-order-a', 37, 'risk-key-a'), ('risk-order-b', 59, 'risk-key-b') and "
        "('risk-order-c', 83, 'risk-key-c') returns True each; an identical replay returns False and a "
        'conflicting amount for the same key raises ValueError; orders() and pending(3) show three events. '
        '2 publisher: publish(sink, 3); the sink records each event in an fsynced journal; the process is '
        'killed with SIGKILL inside the second callback, after the sink accepted the event. 3 recover: '
        'before any publish, pending(3) must still hold the second and third events with the same event_id '
        'values and orders() is unchanged; publish(sink, 1), publish(sink, 2) and publish(sink, 1) '
        'acknowledge 1, 1 and 0 events, so the sink sees the interrupted event again (at-least-once) and '
        'then the third; pending(3) ends empty and order replays return False.'),
}
CONTENTION_DESCRIPTIONS = {
    'lease_queue_lifecycle_v1': (
        'Contention: three more fresh interpreters open the class on a second database file and wait at a '
        "shared barrier. Released together, each calls enqueue('risk-race-NN', 'risk-race-payload-NN') for "
        'NN = 00 to 11: every job must return True to exactly one of them and False to the others. Released '
        'together again, each calls claim(5, 7) four times: the twelve leases must name twelve different jobs '
        'with twelve different tokens and deadline 12, and pending() is 12 for each.'),
    'transactional_outbox_lifecycle_v1': (
        'Contention: three more fresh interpreters open the class on a second database file and wait at a '
        "shared barrier. Released together, each calls create_order('risk-race-order-N', 41 + N, "
        "'risk-race-key-N') for N = 0 to 5: every order must return True to exactly one of them and False "
        '(never an exception) to the others. Released together again, each must see the same six orders '
        'and the same six pending events in creation order, with distinct event_id values.'),
}


def describe(observation):
    """What the fixed program does for this observation, in words (autocode_risk_findings)."""
    protocol = observation.get('protocol')
    return ' '.join(part for part in (DESCRIPTIONS.get(protocol, ''),
                                      CONTENTION_DESCRIPTIONS.get(protocol, '') if contended(observation) else '')
                    if part)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _observation(value):
    required = {'hash', 'protocol', 'target', 'criterion_ids', 'source_bindings'}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value) - required - {'declaration', 'proposal'}):
        raise ValueError('Lifecycle observation has unsupported fields')
    if not isinstance(value['protocol'], str) or value['protocol'] not in PROTOCOLS:
        raise ValueError('Unknown fixed lifecycle protocol')
    if (not isinstance(value['hash'], str) or not re.fullmatch('[0-9a-f]{64}', value['hash'])
            or _digest({key: item for key, item in value.items() if key != 'hash'}) != value['hash']):
        raise ValueError('Lifecycle observation content hash changed')
    target = value['target']
    if not isinstance(target, dict) or set(target) != {'module', 'class_name', 'methods'}:
        raise ValueError('Lifecycle target needs module, class_name and fixed method roles')
    if (not isinstance(target['module'], str)
            or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', target['module'])
            or not isinstance(target['class_name'], str)
            or not re.fullmatch(r'[A-Za-z_]\w*', target['class_name'])):
        raise ValueError('Lifecycle target must use Python identifiers')
    methods = target['methods']
    if (not isinstance(methods, dict) or set(methods) != _ROLES[value['protocol']]
            or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_]\w*', name)
                   for name in methods.values())):
        raise ValueError('Lifecycle target has invalid fixed method roles')
    ids = value['criterion_ids']
    if (not isinstance(ids, list) or not ids or any(not isinstance(cid, str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*', cid) for cid in ids)
            or len(ids) != len(set(ids))):
        raise ValueError('Lifecycle observation needs distinct criterion IDs')
    bindings = value['source_bindings']
    if not isinstance(bindings, list) or not bindings:
        raise ValueError('Lifecycle observation needs original human source bindings')
    for row in bindings:
        if (not isinstance(row, dict) or set(row) != {'source_id', 'source_sha256', 'span', 'quote'}
                or not isinstance(row['source_id'], str) or not row['source_id']
                or not isinstance(row['source_sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', row['source_sha256'])
                or not isinstance(row['quote'], str) or not row['quote']
                or not isinstance(row['span'], list) or len(row['span']) != 2
                or any(type(n) is not int for n in row['span'])
                or not 0 <= row['span'][0] < row['span'][1]
                or row['span'][1] - row['span'][0] != len(row['quote'])):
            raise ValueError('Lifecycle source binding is malformed')
    if len(json.dumps(value).encode()) > MAX_TRANSCRIPT_BYTES:
        raise ValueError('Lifecycle observation exceeds its fixed size bound')
    return value


_CHECK = r'''
def need(condition, reason):
    if not condition:
        raise ValueError(reason)

def decoded(value):
    need(isinstance(value, str), 'Lifecycle bytes must be base64')
    try:
        return base64.b64decode(value, validate=True)
    except Exception as error:
        raise ValueError('Lifecycle bytes have invalid base64') from error

def event(value):
    need(isinstance(value, dict) and set(value) == {'event_id','order_id','amount'}, 'Invalid public outbox event')
    need(isinstance(value['event_id'], str) and bool(value['event_id']), 'Outbox IDs must be opaque nonempty strings')
    need(type(value['amount']) is int, 'Outbox amount is not an actual integer')
    return value

def claim(value, ident, payload, deadline):
    need(isinstance(value, dict) and set(value) == {'id','payload','token','deadline'}, 'Missing public lease claim')
    need(value['id'] == ident and value['payload'] == payload and type(value['deadline']) is int
         and value['deadline'] == deadline, 'Lease identity, payload or exact deadline changed')
    need(isinstance(value['token'], str) and bool(value['token']), 'Lease token must be opaque and nonempty')
    return value['token']

def contended(observation):
    """Whether the observed declaration also promises atomicity under contention."""
    declaration = observation.get('declaration') if isinstance(observation, dict) else None
    promises = declaration.get('promises') if isinstance(declaration, dict) else None
    return isinstance(promises, list) and CONTENTION in promises

def raised(values):
    odd = next((value for value in values if type(value) is not bool), None)
    return '' if odd is None else ': ' + str(odd)[:200]

def check_contention(data, observation, pids, previous, output_bytes):
    """The promised contention phase: owned contenders released together, each racing write once."""
    block = data['contention']
    if not contended(observation):
        need(block is None, 'Lifecycle transcript has an unpromised contention phase')
        return []
    need(isinstance(block,dict) and set(block) == {'workers','calls','ready','checkpoints','releases'},
         'Promised contention phase is missing or incomplete')
    workers, calls, ready, checkpoints, releases = (block[key] for key in
        ('workers','calls','ready','checkpoints','releases'))
    need(isinstance(workers,list) and isinstance(calls,list) and len(workers) == len(calls) == 3,
         'Contention needs three owned concurrent interpreters')
    need(all(isinstance(row,list) and all(type(tick) is int for tick in row) for row in (ready,checkpoints,releases))
         and len(ready) == len(checkpoints) == 3 and len(releases) == 2, 'Contention barrier receipts are incomplete')
    need(ready == sorted(ready) and checkpoints == sorted(checkpoints)
         and ready[-1] < releases[0] < checkpoints[0] and checkpoints[-1] < releases[1],
         'Contenders were not all waiting at each barrier before their shared release')
    for worker in workers:
        need(isinstance(worker,dict) and set(worker) == {'pid','parent_pid','exit_code','started','received',
             'terminated','exited','stdout_base64','stderr_base64'}, 'Incomplete owned-worker receipt')
        pid = worker['pid']
        need(type(pid) is int and pid > 0 and pid != data['supervisor_pid'] and pid not in pids,
             'Contenders must use distinct actual worker PIDs')
        pids.add(pid)
        need(type(worker['parent_pid']) is int and worker['parent_pid'] == data['supervisor_pid'], 'Worker is not owned by this supervisor')
        for key in ['started','received','exited']:
            need(type(worker[key]) is int, 'Lifecycle ordering needs actual integer sequence numbers')
        need(previous < worker['started'] < ready[0] and releases[1] < worker['received'] < worker['exited'],
             'Contender start, release or reap ordering changed')
        need(type(worker['exit_code']) is int and worker['exit_code'] == 0 and worker['terminated'] is None,
             'Contender did not exit cleanly on its own')
        for key in ['stdout_base64','stderr_base64']:
            output_bytes += len(decoded(worker[key]))
            need(output_bytes <= 65536, 'Candidate output exceeded its shared bound')
    need(all(workers[index]['exited'] < workers[index + 1]['received'] for index in range(2)),
         'Contenders were not reaped in order')
    if data['protocol'] == 'lease_queue_lifecycle_v1':
        need(all(isinstance(row,dict) and set(row) == {'enqueued','claims','pending'}
                 and isinstance(row['enqueued'],list) and len(row['enqueued']) == 12
                 and isinstance(row['claims'],list) and len(row['claims']) == 4 for row in calls),
             'Contenders lack actual public enqueue and claim calls')
        for index in range(12):
            values = [row['enqueued'][index] for row in calls]
            need(not raised(values), 'Concurrent enqueue of one job raised or returned a non-boolean' + raised(values))
            need(sum(values) == 1, 'Concurrent enqueue of one job did not return True exactly once')
        leases = [item for row in calls for item in row['claims']]
        odd = next((item for item in leases if not isinstance(item,dict)
                    or set(item) != {'id','payload','token','deadline'}), None)
        need(odd is None, 'A concurrent claim returned no lease: ' + str(odd)[:200])
        jobs = ['risk-race-%02d' % index for index in range(12)]
        need(sorted(str(item['id']) for item in leases) == jobs and all(isinstance(item['id'],str) for item in leases),
             'Concurrent claims leased one job twice and left another unclaimed')
        need(all(item['payload'] == 'risk-race-payload-' + item['id'][-2:] and type(item['deadline']) is int
                 and item['deadline'] == 12 for item in leases), 'Concurrent claims changed a payload or exact deadline')
        tokens = [item['token'] for item in leases]
        need(all(isinstance(token,str) and token for token in tokens) and len(set(tokens)) == 12,
             'Concurrent claims reused a lease token')
        need(all(type(row['pending']) is int and row['pending'] == 12 for row in calls),
             'Concurrent enqueue lost or duplicated an unfinished job: pending() returned '
             + str([row['pending'] for row in calls])[:200])
    else:
        need(all(isinstance(row,dict) and set(row) == {'created','orders','pending'}
                 and isinstance(row['created'],list) and len(row['created']) == 6 for row in calls),
             'Contenders lack actual public create_order calls')
        for index in range(6):
            values = [row['created'][index] for row in calls]
            need(not raised(values), 'Concurrent create_order for one key raised or returned a non-boolean' + raised(values))
            need(sum(values) == 1, 'Concurrent create_order for one key did not commit exactly once')
        orders = {'risk-race-order-%d' % index: 41 + index for index in range(6)}
        odd = next((row['orders'] for row in calls if not isinstance(row['orders'],dict) or row['orders'] != orders
                    or any(type(amount) is not int for amount in row['orders'].values())), None)
        need(odd is None, 'Concurrent creation lost, duplicated or changed an order: orders() returned ' + str(odd)[:200])
        pending = calls[0]['pending']
        need(isinstance(pending,list) and len(pending) == 6 and all(row['pending'] == pending for row in calls),
             'Concurrent creation did not commit exactly one durable event per order: pending(12) returned '
             + str([row['pending'] if not isinstance(row['pending'],list) else len(row['pending']) for row in calls])[:200])
        for item, ident in zip(pending, orders):
            event(item)
            need(item['order_id'] == ident and item['amount'] == orders[ident], 'Concurrent events changed order or amount')
        need(len({item['event_id'] for item in pending}) == 6, 'Concurrent events need distinct stable identities')
    return workers

def check_transcript(data, observation):
    need(isinstance(data, dict) and set(data) == {'version','protocol','observation_hash','supervisor_pid',
         'deadline_seconds','elapsed_seconds','phases','contention','owned_workers','sink','verdict','error'},
         'Lifecycle transcript has incomplete or unexpected fields')
    need(type(data['version']) is int and data['version'] == 2 and data['protocol'] == observation['protocol']
         and data['observation_hash'] == observation['hash'], 'Lifecycle transcript belongs to another observation')
    need(data['verdict'] == 'PASS' and data['error'] == '', 'Lifecycle supervisor did not finish successfully')
    need(type(data['supervisor_pid']) is int and data['supervisor_pid'] > 0, 'Missing actual supervisor PID')
    need(type(data['deadline_seconds']) in (int,float) and 0 < data['deadline_seconds'] <= 30, 'Invalid shared lifecycle deadline')
    need(type(data['elapsed_seconds']) in (int,float) and 0 <= data['elapsed_seconds'] <= data['deadline_seconds'],
         'Actual lifecycle execution exceeded its shared deadline')
    names = (['holder','reclaim','finish'] if data['protocol'] == 'lease_queue_lifecycle_v1'
             else ['seed','publisher','recover'])
    phases = data['phases']
    need(isinstance(phases,list) and len(phases) == 3, 'Lifecycle needs all three independent interpreter phases')
    pids, previous, output_bytes = set(), 0, 0
    for row, name in zip(phases, names):
        need(isinstance(row,dict) and set(row) == {'phase','worker','calls'} and row['phase'] == name,
             'Lifecycle phases were omitted or reordered')
        worker = row['worker']
        need(isinstance(worker,dict) and set(worker) == {'pid','parent_pid','exit_code','started','received',
             'terminated','exited','stdout_base64','stderr_base64'}, 'Incomplete owned-worker receipt')
        pid = worker['pid']
        need(type(pid) is int and pid > 0 and pid != data['supervisor_pid'] and pid not in pids,
             'Lifecycle phases must use distinct actual worker PIDs')
        pids.add(pid)
        need(type(worker['parent_pid']) is int and worker['parent_pid'] == data['supervisor_pid'], 'Worker is not owned by this supervisor')
        for key in ['started','received','exited']:
            need(type(worker[key]) is int, 'Lifecycle ordering needs actual integer sequence numbers')
        need(previous < worker['started'] < worker['received'] < worker['exited'], 'Worker readiness or reap ordering changed')
        crash = name in ('holder','publisher')
        need(type(worker['exit_code']) is int and worker['exit_code'] == (-signal.SIGKILL if crash else 0),
             'Lifecycle did not observe the required actual hard-kill exit')
        if crash:
            need(type(worker['terminated']) is int and worker['received'] < worker['terminated'] < worker['exited'],
                 'Worker was not killed after the public commit/readiness barrier')
        else:
            need(worker['terminated'] is None, 'Healthy worker was terminated')
        for key in ['stdout_base64','stderr_base64']:
            output_bytes += len(decoded(worker[key]))
            need(output_bytes <= 65536, 'Candidate output exceeded its shared bound')
        previous = worker['exited']
    sink = data['sink']
    need(isinstance(sink,dict) and set(sink) == {'deliveries','journal_base64','journal_sha256'}, 'Missing owned durable sink receipt')
    journal = decoded(sink['journal_base64'])
    need(hashlib.sha256(journal).hexdigest() == sink['journal_sha256'], 'Durable sink journal changed')
    deliveries = sink['deliveries']
    need(isinstance(deliveries,list), 'Invalid sink delivery sequence')
    expected_journal = b''.join((json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode() for row in deliveries)
    need(journal == expected_journal, 'Durable sink differs from the parent-observed actual delivery sequence')
    a,b,c = [row['calls'] for row in phases]
    if data['protocol'] == 'lease_queue_lifecycle_v1':
        need(not deliveries and not journal, 'Queue protocol unexpectedly used an outbox sink')
        need(set(a) == {'enqueue','claim','pending'} and a['enqueue'] == [True,True]
             and all(type(x) is bool for x in a['enqueue']) and type(a['pending']) is int and a['pending'] == 2,
             'Holder did not durably enqueue and lease two unfinished jobs')
        old = claim(a['claim'],'risk-job-a','risk-payload-a',18)
        need(set(b) == {'claim','stale_ack','stale_nack','pending','replay','conflict'}, 'Reclaim phase lacks actual public calls')
        fresh = claim(b['claim'],'risk-job-a','risk-payload-a',25)
        need(old != fresh and b['stale_ack'] is False and b['stale_nack'] is False,
             'Restart reused a lease token or accepted a stale acknowledgment/release')
        need(type(b['pending']) is int and b['pending'] == 2 and b['replay'] is False and b['conflict'] == 'ValueError',
             'Restart changed idempotency or unfinished inventory')
        need(set(c) == {'ack','ack_again','pending_after_ack','other','other_ack','pending','replay','conflict'},
             'Final interpreter lacks actual current-token and unrelated-job calls')
        need(c['ack'] is True and c['ack_again'] is False and type(c['pending_after_ack']) is int
             and c['pending_after_ack'] == 1, 'Fresh interpreter did not fence the current token')
        claim(c['other'],'risk-job-b','risk-payload-b',26)
        need(c['other_ack'] is True and type(c['pending']) is int and c['pending'] == 0
             and c['replay'] == [False,False] and all(x is False for x in c['replay'])
             and c['conflict'] == 'ValueError', 'Unrelated job or completed idempotency was lost')
    else:
        orders = {'risk-order-a':37,'risk-order-b':59,'risk-order-c':83}
        need(set(a) == {'created','replay','conflict','orders','pending'} and a['created'] == [True,True,True]
             and all(x is True for x in a['created']) and a['replay'] is False and a['conflict'] == 'ValueError'
             and a['orders'] == orders, 'Outbox seed did not atomically retain its orders and idempotency')
        need(all(type(amount) is int for amount in a['orders'].values()), 'Public orders amounts are not actual integers')
        pending = a['pending']
        need(isinstance(pending,list) and len(pending) == 3, 'Outbox seed lacks three durable events')
        for item, ident in zip(pending,orders):
            event(item)
            need(item['order_id'] == ident and item['amount'] == orders[ident], 'Outbox seed order or amount changed')
        need(len({item['event_id'] for item in pending}) == 3, 'Outbox events need distinct stable identities')
        need(set(b) == {'callbacks'} and b['callbacks'] == pending[:2], 'Publisher did not reach the middle callback barrier')
        need(set(c) == {'before','after'} and set(c['before']) == {'orders','pending'}
             and c['before']['orders'] == orders and c['before']['pending'] == pending[1:],
             'Hard-kill lost the unacknowledged event or changed its stable identity')
        need(all(type(amount) is int for amount in c['before']['orders'].values()), 'Reopened orders amounts are not actual integers')
        after = c['after']
        need(set(after) == {'counts','pending_after_first','pending','orders','replay','conflict'}
             and after['counts'] == [1,1,0] and all(type(n) is int for n in after['counts'])
             and after['pending_after_first'] == pending[2:] and after['pending'] == []
             and after['orders'] == orders and after['replay'] == [False,False,False]
             and all(x is False for x in after['replay']) and after['conflict'] == 'ValueError',
             'Bounded retry did not drain the durable pending suffix without changing orders')
        need(all(type(amount) is int for amount in after['orders'].values()), 'Retried orders amounts are not actual integers')
        need(len(deliveries) == 4, 'At-least-once retry must retain the interrupted sink delivery')
        for index,(row,expected) in enumerate(zip(deliveries,[pending[0],pending[1],pending[1],pending[2]])):
            need(isinstance(row,dict) and set(row) == {'order','worker_pid','event'} and row['event'] == expected,
                 'Sink event IDs/order changed across restart')
            need(type(row['order']) is int and (index == 0 or row['order'] > deliveries[index-1]['order']),
                 'Sink delivery chronology changed')
            owner = phases[1 if index < 2 else 2]['worker']
            need(row['worker_pid'] == owner['pid'] and owner['started'] < row['order'] < owner['received'],
                 'Sink delivery is not owned by its actual publisher phase')
    # After the lifecycle values, so a lifecycle defect keeps its own reason.
    workers = [row['worker'] for row in phases] + check_contention(data, observation, pids, previous, output_bytes)
    owned = data['owned_workers']
    need(isinstance(owned,list) and len(owned) == len(workers) <= 6, 'Incomplete owned-worker cleanup inventory')
    for row,worker in zip(owned,workers):
        need(isinstance(row,dict) and set(row) == {'pid','parent_pid','exit_code','reaped'}
             and type(row['pid']) is int and row['pid'] == worker['pid']
             and type(row['parent_pid']) is int and row['parent_pid'] == data['supervisor_pid']
             and type(row['exit_code']) is int and row['exit_code'] == worker['exit_code']
             and row['reaped'] is True, 'Owned lifecycle worker was omitted or not actually reaped')
    return data
'''
_CHECK_NAMESPACE = {'base64': base64, 'hashlib': hashlib, 'json': json, 'signal': signal, 'CONTENTION': CONTENTION}
exec(_CHECK, _CHECK_NAMESPACE)
contended = _CHECK_NAMESPACE['contended']


def validate_transcript(rawbytes, observation):
    """Recheck the complete actual values, hard-kill exits, identities and chronology."""
    _observation(observation)
    if not isinstance(rawbytes, bytes) or not 0 < len(rawbytes) <= MAX_TRANSCRIPT_BYTES:
        raise ValueError('Lifecycle transcript must be bounded complete bytes')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Lifecycle JSON has duplicate keys')
            value[key] = item
        return value
    try:
        data = json.loads(rawbytes.decode('utf-8'), object_pairs_hook=unique)
        return _CHECK_NAMESPACE['check_transcript'](data, observation)
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('Lifecycle transcript is malformed') from error


_WORKER = r'''
import importlib,json,os,socket,sys
from pathlib import Path
cfg=json.loads(sys.argv[1]); channel=socket.socket(fileno=cfg['fd'])
def send(kind, **values):
    channel.sendall((json.dumps(dict(kind=kind,pid=os.getpid(),parent_pid=os.getppid(),**values),
                               sort_keys=True,separators=(',',':'))+'\n').encode())
def wait():
    data=b''
    while not data.endswith(b'\n'):
        part=channel.recv(4096)
        if not part: raise RuntimeError('Supervisor IPC closed before release')
        data+=part
        if len(data)>1048576: raise RuntimeError('Supervisor IPC exceeds bound')
    if json.loads(data) != {'continue':True}: raise RuntimeError('Invalid supervisor release')
try:
    root=Path(cfg['root']).resolve()
    parts=cfg['target']['module'].split('.')
    def safe_target(path):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Target source escapes candidate')
    source_root=root/'src'
    safe_target(source_root)
    roots=[root]+([source_root] if source_root.is_dir() else [])
    entries=[]
    for base in roots:
        # Inspect both possible roots before any candidate package initializer
        # runs. A symlink or duplicate cannot silently choose another API.
        for index in range(1,len(parts)+1):
            package=base.joinpath(*parts[:index])
            safe_target(package)
            safe_target(package/'__init__.py')
        location=base.joinpath(*parts)
        for path in (location.with_suffix('.py'),location/'__init__.py'):
            safe_target(path)
            if path.is_file(): entries.append((base,path))
    if not entries: raise ValueError('Target has no candidate Python source module')
    if len(entries)!=1: raise ValueError('Target module has ambiguous root/src or module/package sources')
    base,entry=entries[0]
    for index in range(1,len(parts)):
        if not base.joinpath(*parts[:index]).is_dir():
            raise ValueError('Target package escapes candidate')
    sys.path.insert(0,str(base))
    module=importlib.import_module(cfg['target']['module'])
    if Path(module.__file__).resolve()!=entry.resolve():
        raise ValueError('Target module escapes candidate source entry')
    store=getattr(module,cfg['target']['class_name'])(cfg['database'])
    methods=cfg['target']['methods']
    def call(role,*args,**kw): return getattr(store,methods[role])(*args,**kw)
    def conflict(*args):
        try: call('enqueue' if cfg['protocol'].startswith('lease_') else 'create_order',*args)
        except ValueError: return 'ValueError'
        return 'accepted'
    phase=cfg['phase']; old=cfg.get('old'); current=cfg.get('current')
    if phase=='holder':
        calls={'enqueue':[call('enqueue','risk-job-a','risk-payload-a'),call('enqueue','risk-job-b','risk-payload-b')],
               'claim':call('claim',11,7),'pending':call('pending')}
        send('result',calls=calls); wait()
        raise RuntimeError('Holder was released instead of hard-killed')
    elif phase=='reclaim':
        calls={'claim':call('claim',18,7),'stale_ack':call('ack',old['id'],old['token'],18),
               'stale_nack':call('nack',old['id'],old['token'],18),'pending':call('pending'),
               'replay':call('enqueue','risk-job-a','risk-payload-a'),
               'conflict':conflict('risk-job-a','wrong')}
    elif phase=='finish':
        calls={'ack':call('ack',current['id'],current['token'],19),
               'ack_again':call('ack',current['id'],current['token'],19),'pending_after_ack':call('pending')}
        other=call('claim',19,7)
        calls.update(other=other,other_ack=call('ack',other['id'],other['token'],20),pending=call('pending'),
                     replay=[call('enqueue','risk-job-a','risk-payload-a'),call('enqueue','risk-job-b','risk-payload-b')],
                     conflict=conflict('risk-job-a','wrong'))
    elif phase=='seed':
        rows=[('risk-order-a',37,'risk-key-a'),('risk-order-b',59,'risk-key-b'),('risk-order-c',83,'risk-key-c')]
        calls={'created':[call('create_order',*row) for row in rows], 'replay':call('create_order',*rows[1]),
               'conflict':conflict('risk-order-b',60,'risk-key-b'),'orders':call('orders'),'pending':call('pending',3)}
    elif phase=='contend':
        # Released together with the other contenders: a call that raises is a result, not a crash.
        def attempt(role,*args):
            try: return call(role,*args)
            except Exception as error: return 'raised '+type(error).__name__+': '+str(error)[:200]
        send('ready'); wait()
        if cfg['protocol'].startswith('lease_'):
            first={'enqueued':[attempt('enqueue','risk-race-%02d'%index,'risk-race-payload-%02d'%index) for index in range(12)]}
            send('checkpoint',calls=first); wait()
            calls={**first,'claims':[attempt('claim',5,7) for _ in range(4)],'pending':attempt('pending')}
        else:
            first={'created':[attempt('create_order','risk-race-order-%d'%index,41+index,'risk-race-key-%d'%index)
                              for index in range(6)]}
            send('checkpoint',calls=first); wait()
            calls={**first,'orders':attempt('orders'),'pending':attempt('pending',12)}
    else:
        def sink(value): send('callback',event=value); wait()
        if phase=='publisher':
            calls={'count':call('publish',sink,3)}
        elif phase=='recover':
            send('checkpoint',calls={'orders':call('orders'),'pending':call('pending',3)})
            first=call('publish',sink,1); pending=call('pending',3)
            second=call('publish',sink,2); third=call('publish',sink,1)
            rows=[('risk-order-a',37,'risk-key-a'),('risk-order-b',59,'risk-key-b'),('risk-order-c',83,'risk-key-c')]
            calls={'counts':[first,second,third],'pending_after_first':pending,'pending':call('pending',3),
                   'orders':call('orders'),'replay':[call('create_order',*row) for row in rows],
                   'conflict':conflict('risk-order-b',60,'risk-key-b')}
        else: raise ValueError('Unknown fixed worker phase')
    send('result',calls=calls)
except BaseException as error:
    send('error',error=type(error).__name__+': '+str(error)[:400])
    sys.exit(1)
'''

_SUPERVISOR = r'''
import base64,hashlib,json,os,selectors,signal,socket,subprocess,sys,tempfile,time
from pathlib import Path
observation=json.loads(sys.argv[1]); timeout=json.loads(sys.argv[2]); root=Path.cwd().resolve()
started_at=time.monotonic(); deadline=started_at+timeout; reserve=min(1,timeout/4)
sequence=0; workers=[]; deliveries=[]; journal_bytes=b''; ipc_total=0; output_total=0
def remaining(cleanup=False):
    value=deadline-time.monotonic()-(0 if cleanup else reserve)
    if value<=0: raise TimeoutError('Shared lifecycle deadline exceeded')
    return value
def tick():
    global sequence
    sequence+=1; return sequence
class Worker:
    def __init__(self,phase,database,**values):
        if len(workers)>=6: raise ValueError('Fixed worker bound exceeded')
        self.phase=phase; self.parent,self.child=socket.socketpair(); self.selector=selectors.DefaultSelector()
        cfg=dict(root=str(root),database=str(database),fd=self.child.fileno(),phase=phase,
                 protocol=observation['protocol'],target=observation['target'],**values)
        self.process=subprocess.Popen([sys.executable,'-I','-c',WORKER,json.dumps(cfg)],cwd=root,
            pass_fds=(self.child.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        workers.append(self); self.child.close(); self.buffer=b''; self.output={'stdout':b'','stderr':b''}
        self.record=dict(pid=self.process.pid,parent_pid=os.getpid(),exit_code=None,started=tick(),
                         received=None,terminated=None,exited=None,stdout_base64='',stderr_base64='')
        for file,label in [(self.parent,'ipc'),(self.process.stdout,'stdout'),(self.process.stderr,'stderr')]:
            os.set_blocking(file.fileno(),False); self.selector.register(file,selectors.EVENT_READ,label)
    def capture(self,label,chunk):
        global output_total
        output_total+=len(chunk)
        if output_total>65536: raise ValueError('Candidate output exceeded shared bound')
        self.output[label]+=chunk
    def read(self):
        global ipc_total
        if b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1)
            value=json.loads(line)
            if value.get('pid') != self.process.pid or value.get('parent_pid') != os.getpid():
                raise ValueError('Worker IPC identity changed')
            if value.get('kind')=='error': raise ValueError(value['error'])
            return value
        while True:
            ready=self.selector.select(remaining())
            if not ready: raise TimeoutError('Shared lifecycle deadline exceeded before the worker IPC barrier')
            for key,mask in ready:
                chunk=os.read(key.fd,65536)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    if key.data=='ipc' and not self.buffer: raise ValueError('Worker exited without its public barrier')
                    continue
                if key.data=='ipc':
                    ipc_total+=len(chunk)
                    if ipc_total>1048576: raise ValueError('Candidate IPC exceeded shared bound')
                    self.buffer+=chunk
                else:
                    self.capture(key.data,chunk)
            if b'\n' in self.buffer: return self.read()
    def release(self):
        self.parent.sendall(b'{"continue":true}\n')
    def finish(self,kill=False):
        self.record['received']=tick()
        if kill:
            self.record['terminated']=tick(); self.process.kill()
        self.record['exit_code']=self.process.wait(timeout=remaining())
        self.record['exited']=tick()
        for label,file in [('stdout',self.process.stdout),('stderr',self.process.stderr)]:
            while True:
                try: part=os.read(file.fileno(),65536)
                except BlockingIOError: break
                if not part: break
                self.capture(label,part)
            self.record[label+'_base64']=base64.b64encode(self.output[label]).decode()
        return self.record
    def close(self):
        if self.process.poll() is None:
            self.process.kill(); self.process.wait(timeout=remaining(cleanup=True))
        self.selector.close(); self.parent.close(); self.process.stdout.close(); self.process.stderr.close()
def result(worker):
    value=worker.read()
    if value['kind']!='result': raise ValueError('Worker returned an unexpected protocol phase')
    return value['calls']
def contend(database):
    """Three owned contenders, released together at two barriers on their own database."""
    contenders=[Worker('contend',database) for _ in range(3)]
    block=data['contention']=dict(workers=[],calls=[],ready=[],checkpoints=[],releases=[])
    for barrier,key in (('ready','ready'),('checkpoint','checkpoints')):
        for worker in contenders:
            if worker.read()['kind']!=barrier: raise ValueError('Contender omitted its shared barrier')
            block[key].append(tick())
        block['releases'].append(tick())
        for worker in contenders: worker.release()
    for worker in contenders:
        block['calls'].append(result(worker)); block['workers'].append(worker.finish())
data=dict(version=2,protocol=observation['protocol'],observation_hash=observation['hash'],supervisor_pid=os.getpid(),
          deadline_seconds=timeout,elapsed_seconds=None,phases=[],contention=None,owned_workers=[],sink=None,
          verdict='PASS',error='')
try:
    # SQLite refuses a database path over 512 bytes, journal suffix included. A replay root that deep
    # (a long project path plus the runner's scratch tree) is no product defect: use a short owned
    # directory in the system temporary directory instead, removed the same way.
    deep=len(os.fsencode(str(root)))>440
    with tempfile.TemporaryDirectory(prefix='autocode-risk-protocol-' if deep else '.risk-protocol-',
                                     dir=None if deep else root) as directory:
        folder=Path(directory); database=folder/'product.sqlite'; journal=folder/'sink.jsonl'
        def delivered(worker,value):
            global journal_bytes
            row=dict(order=tick(),worker_pid=worker.process.pid,event=value)
            blob=(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode()
            if len(journal_bytes)+len(blob)>1048576: raise ValueError('Durable sink journal exceeded bound')
            with journal.open('ab',buffering=0) as sink: sink.write(blob); os.fsync(sink.fileno())
            journal_bytes+=blob; deliveries.append(row)
        def phase(worker,calls,kill=False): data['phases'].append(dict(phase=worker.phase,worker=worker.finish(kill),calls=calls))
        if observation['protocol']=='lease_queue_lifecycle_v1':
            holder=Worker('holder',database); a=result(holder); phase(holder,a,True)
            reclaim=Worker('reclaim',database,old=a['claim']); b=result(reclaim); phase(reclaim,b)
            finish=Worker('finish',database,current=b['claim']); phase(finish,result(finish))
        else:
            seed=Worker('seed',database); phase(seed,result(seed))
            publisher=Worker('publisher',database); callbacks=[]
            for index in range(2):
                value=publisher.read()
                if value['kind']!='callback': raise ValueError('Publisher omitted the middle callback barrier')
                callbacks.append(value['event']); delivered(publisher,value['event'])
                if index==0: publisher.release()
            phase(publisher,dict(callbacks=callbacks),True)
            recover=Worker('recover',database); initial=recover.read()
            if initial['kind']!='checkpoint': raise ValueError('Fresh worker omitted public pending-state checkpoint')
            retries=0
            while True:
                value=recover.read()
                if value['kind']=='result': break
                if value['kind']!='callback': raise ValueError('Retry returned an unexpected phase')
                retries+=1
                if retries>2: raise ValueError('Bounded retry emitted too many callbacks')
                delivered(recover,value['event']); recover.release()
            phase(recover,dict(before=initial['calls'],after=value['calls']))
            if journal.read_bytes()!=journal_bytes: raise ValueError('Owned sink journal does not match fsynced deliveries')
        if contended(observation): contend(folder/'contention.sqlite')
except Exception as error:
    data['verdict']='FAIL'; data['error']=type(error).__name__+': '+str(error)[:500]
finally:
    for worker in workers:
        if worker.process.poll() is None:
            worker.process.kill()
    for worker in workers:
        try: worker.close()
        except Exception as error:
            data['verdict']='FAIL'; data['error']='Owned worker cleanup failed: '+str(error)[:400]
        data['owned_workers'].append(dict(pid=worker.process.pid,parent_pid=os.getpid(),
                                         exit_code=worker.process.poll(),reaped=worker.process.returncode is not None))
data['elapsed_seconds']=time.monotonic()-started_at
data['sink']=dict(deliveries=deliveries,journal_base64=base64.b64encode(journal_bytes).decode(),
                  journal_sha256=hashlib.sha256(journal_bytes).hexdigest())
if data['verdict']=='PASS':
    try: check_transcript(data,observation)
    except Exception as error:
        data['verdict']='FAIL'; data['error']=str(error)[:500]
encoded=json.dumps(data,sort_keys=True,separators=(',',':')).encode()
if len(encoded)>1048576:
    encoded=json.dumps(dict(verdict='FAIL',error='Lifecycle transcript exceeded bound')).encode()
sys.stdout.buffer.write(encoded+b'\n'); sys.exit(0 if data['verdict']=='PASS' else 1)
'''


def commands(observations, *, python='python3', timeout=30):
    """Compile fixed protocols into isolated supervisors using the caller's budget."""
    if (not isinstance(python, str) or not re.fullmatch(r'(?:[A-Za-z0-9_./-]*/)?python(?:\d+(?:\.\d+)*)?', python)
            or any(part == '..' for part in python.split('/'))):
        raise ValueError('Lifecycle interpreter must be an explicit Python executable')
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise ValueError('Lifecycle timeout must be within the fixed shared 30-second bound')
    if not isinstance(observations, list):
        raise ValueError('Lifecycle observations must be a list')
    result, seen = [], set()
    for observation in observations:
        _observation(observation)
        if observation['hash'] in seen:
            raise ValueError('Lifecycle observations must be distinct')
        seen.add(observation['hash'])
        program = ('WORKER=' + repr(_WORKER) + '\nCONTENTION=' + repr(CONTENTION) + '\n' + _CHECK + '\n'
                   + _SUPERVISOR)
        result.append(shlex.join([python, '-I', '-c', program,
                                 json.dumps(observation, sort_keys=True, ensure_ascii=False), str(timeout)]))
    return result
