#!/usr/bin/env python3
"""Offline HTTP/2 wire-behavior oracle; never writes the submitted workspace.

Usage: python3 oracle.py WORKSPACE [--out NEW_DIRECTORY]
The operator supplies a sealed external GOMODCACHE containing the pinned modules.
Evaluation itself has GOPROXY=off, GOSUMDB=off and GOTOOLCHAIN=local.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tempfile
import subprocess
import time

# The evaluator owns this independent probe; it is not read from the candidate.
GO_TEST_SOURCE = r'''
package arenahttp2

import (
	"bytes"
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"net"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"testing/synctest"
	"time"

	"golang.org/x/net/http2"
	"golang.org/x/net/http2/hpack"
)

const bound = 2 * time.Second

// A real HTTP/2 wire peer, independent of the repository's test harness.
// It drains client writes but deliberately chooses which replies to send.
type peer struct {
	client, server net.Conn
	writes         chan func(*http2.Framer) error
	headers        chan uint32
	resets         chan *http2.RSTStreamFrame
	pings          chan *http2.PingFrame
	settingsAck    chan struct{}
	pingRequests   atomic.Int32
	closed         chan struct{}
	once           sync.Once
}

func newPeer(max uint32) *peer {
	c, s := net.Pipe()
	p := &peer{client: c, server: s, writes: make(chan func(*http2.Framer) error, 32),
		headers: make(chan uint32, 32), resets: make(chan *http2.RSTStreamFrame, 32),
		pings: make(chan *http2.PingFrame, 32), settingsAck: make(chan struct{}, 32), closed: make(chan struct{})}
	go func() {
		f := http2.NewFramer(s, nil)
		for {
			select {
			case fn := <-p.writes:
				if fn(f) != nil {
					return
				}
			case <-p.closed:
				return
			}
		}
	}()
	go func() {
		preface := make([]byte, len(http2.ClientPreface))
		if _, err := io.ReadFull(s, preface); err != nil || string(preface) != http2.ClientPreface {
			return
		}
		p.send(func(f *http2.Framer) error {
			return f.WriteSettings(http2.Setting{ID: http2.SettingMaxConcurrentStreams, Val: max})
		})
		f := http2.NewFramer(nil, s)
		for {
			frame, err := f.ReadFrame()
			if err != nil {
				return
			}
			switch x := frame.(type) {
			case *http2.HeadersFrame:
				p.headers <- x.StreamID
			case *http2.RSTStreamFrame:
				v := *x
				p.resets <- &v
			case *http2.PingFrame:
				v := *x
				if !v.IsAck() {
					p.pingRequests.Add(1)
				}
				p.pings <- &v
			case *http2.SettingsFrame:
				if x.IsAck() {
					p.settingsAck <- struct{}{}
				}
			}
		}
	}()
	return p
}

func (p *peer) send(fn func(*http2.Framer) error) {
	select {
	case p.writes <- fn:
	case <-p.closed:
	}
}

func (p *peer) close() {
	p.once.Do(func() { close(p.closed); p.client.Close(); p.server.Close() })
}

func receive[T any](t *testing.T, ch <-chan T, what string) T {
	t.Helper()
	select {
	case v := <-ch:
		return v
	case <-time.After(bound):
		t.Fatalf("bounded wait: %s", what)
		var zero T
		return zero
	}
}

func (p *peer) respond(stream uint32, end bool, body string) {
	p.send(func(f *http2.Framer) error {
		var b bytes.Buffer
		e := hpack.NewEncoder(&b)
		if err := e.WriteField(hpack.HeaderField{Name: ":status", Value: "200"}); err != nil {
			return err
		}
		if err := f.WriteHeaders(http2.HeadersFrameParam{StreamID: stream, EndHeaders: true, EndStream: end && body == "", BlockFragment: b.Bytes()}); err != nil {
			return err
		}
		if body != "" {
			return f.WriteData(stream, end, []byte(body))
		}
		return nil
	})
}

type reply struct {
	response *http.Response
	err      error
}

func request(ctx context.Context) *http.Request {
	r, err := http.NewRequestWithContext(ctx, "GET", "https://arena.invalid/probe", nil)
	if err != nil {
		panic(err)
	}
	return r
}

func roundTrip(fn func(*http.Request) (*http.Response, error), req *http.Request) <-chan reply {
	ch := make(chan reply, 1)
	go func() { r, err := fn(req); ch <- reply{r, err} }()
	return ch
}

func response(t *testing.T, ch <-chan reply) *http.Response {
	t.Helper()
	r := receive(t, ch, "RoundTrip response")
	if r.err != nil {
		t.Fatalf("RoundTrip: %v", r.err)
	}
	if r.response.StatusCode != 200 {
		t.Fatalf("status = %d", r.response.StatusCode)
	}
	return r.response
}

func connection(t *testing.T, strict bool, max uint32) (*http2.ClientConn, *peer) {
	t.Helper()
	p := newPeer(max)
	t.Cleanup(p.close)
	tr := &http2.Transport{StrictMaxConcurrentStreams: strict}
	cc, err := tr.NewClientConn(p.client)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { cc.Close() })
	receive(t, p.settingsAck, "peer SETTINGS acknowledgement")
	return cc, p
}

func openStream(t *testing.T, cc *http2.ClientConn, p *peer) *http.Response {
	t.Helper()
	ch := roundTrip(cc.RoundTrip, request(context.Background()))
	id := receive(t, p.headers, "request HEADERS")
	p.respond(id, false, "")
	return response(t, ch)
}

func reset(t *testing.T, p *peer, r *http.Response) {
	t.Helper()
	if err := r.Body.Close(); err != nil {
		t.Fatal(err)
	}
	f := receive(t, p.resets, "RST_STREAM")
	if f.ErrCode != http2.ErrCodeCancel {
		t.Fatalf("reset code = %v", f.ErrCode)
	}
	synctest.Wait()
}

func waitState(t *testing.T, cc *http2.ClientConn, what string, pred func(http2.ClientConnState) bool) {
	t.Helper()
	synctest.Wait()
	if s := cc.State(); !pred(s) {
		t.Fatalf("%s: public state %+v", what, s)
	}
}

func probePing(t *testing.T, p *peer) *http2.PingFrame {
	t.Helper()
	f := receive(t, p.pings, "health PING after canceled stream")
	if f.IsAck() {
		t.Fatal("wanted health PING, got ACK")
	}
	return f
}

func acknowledge(p *peer, f *http2.PingFrame) {
	p.send(func(w *http2.Framer) error { return w.WritePing(true, f.Data) })
	synctest.Wait()
}

func checkResetOccupiesCapacity(t *testing.T) {
	cc, p := connection(t, true, 3)
	a, b, c := openStream(t, cc, p), openStream(t, cc, p), openStream(t, cc, p)
	defer b.Body.Close()
	defer c.Body.Close()
	reset(t, p, a)
	probePing(t, p)
	waitState(t, cc, "reset retains advertised concurrency slot", func(s http2.ClientConnState) bool { return s.StreamsActive == 3 })
}

func checkStrictGateAndAckEpoch(t *testing.T) {
	cc, p := connection(t, true, 3)
	a, b, c := openStream(t, cc, p), openStream(t, cc, p), openStream(t, cc, p)
	defer c.Body.Close()
	reset(t, p, a)
	ping := probePing(t, p)
	reset(t, p, b)
	if n := p.pingRequests.Load(); n != 1 {
		t.Fatalf("two resets must share one health probe: %d", n)
	}
	next := roundTrip(cc.RoundTrip, request(context.Background()))
	waitState(t, cc, "strict request waits behind unconfirmed resets", func(s http2.ClientConnState) bool { return s.StreamsPending == 1 && s.StreamsActive == 3 })
	// A peer PING is not acknowledgement of our earlier health probe.
	var unrelated [8]byte
	copy(unrelated[:], "unrelated")
	p.send(func(f *http2.Framer) error { return f.WritePing(false, unrelated) })
	ack := receive(t, p.pings, "client acknowledgement of peer PING")
	if !ack.IsAck() || ack.Data != unrelated {
		t.Fatal("incorrect peer PING acknowledgement")
	}
	if s := cc.State(); s.StreamsPending != 1 || s.StreamsActive != 3 {
		t.Fatalf("non-ACK PING released reset capacity: %+v", s)
	}
	select {
	case id := <-p.headers:
		t.Fatalf("strict request %d escaped before health ACK", id)
	default:
	}
	acknowledge(p, ping)
	id := receive(t, p.headers, "pending request after health ACK")
	p.respond(id, true, "after-ack")
	r := response(t, next)
	payload, err := io.ReadAll(r.Body)
	r.Body.Close()
	if err != nil || string(payload) != "after-ack" {
		t.Fatalf("post-ACK response %q / %v", payload, err)
	}
	waitState(t, cc, "all earlier resets released together", func(s http2.ClientConnState) bool { return s.StreamsPending == 0 && s.StreamsActive == 1 })
	reset(t, p, c)
	probePing(t, p)
	if n := p.pingRequests.Load(); n != 2 {
		t.Fatalf("later cancel did not rearm health epoch: %d", n)
	}
}

func checkNonStrictAdmissionCountsResets(t *testing.T) {
	cc, p := connection(t, false, 2)
	a, b := openStream(t, cc, p), openStream(t, cc, p)
	defer b.Body.Close()
	reset(t, p, a)
	ping := probePing(t, p)
	if cc.CanTakeNewRequest() || cc.ReserveNewRequest() {
		t.Fatal("unconfirmed reset released pool/reservation capacity")
	}
	acknowledge(p, ping)
	waitState(t, cc, "ACK releases one capacity slot", func(s http2.ClientConnState) bool { return s.StreamsActive == 1 })
	if !cc.CanTakeNewRequest() || !cc.ReserveNewRequest() {
		t.Fatal("healthy ACK did not restore admission")
	}
	if s := cc.State(); s.StreamsReserved != 1 {
		t.Fatalf("reservation not reported: %+v", s)
	}
	ch := roundTrip(cc.RoundTrip, request(context.Background()))
	id := receive(t, p.headers, "reserved request HEADERS")
	p.respond(id, true, "")
	r := response(t, ch)
	r.Body.Close()
	waitState(t, cc, "reservation consumed exactly once", func(s http2.ClientConnState) bool { return s.StreamsReserved == 0 && s.StreamsActive == 1 })
}

type pool struct {
	tr    *http2.Transport
	peers chan *peer
	dials atomic.Int32
}

func newPool(t *testing.T, max uint32) *pool {
	p := &pool{peers: make(chan *peer, 32)}
	p.tr = &http2.Transport{DialTLSContext: func(ctx context.Context, network, addr string, _ *tls.Config) (net.Conn, error) {
		q := newPeer(max)
		t.Cleanup(q.close)
		p.dials.Add(1)
		p.peers <- q
		return q.client, nil
	}}
	t.Cleanup(p.tr.CloseIdleConnections)
	return p
}

func warmPool(t *testing.T, p *pool) *peer {
	t.Helper()
	ch := roundTrip(p.tr.RoundTrip, request(context.Background()))
	q := receive(t, p.peers, "initial connection")
	receive(t, q.settingsAck, "pool SETTINGS ACK")
	id := receive(t, q.headers, "warmup HEADERS")
	q.respond(id, true, "warm")
	r := response(t, ch)
	b, err := io.ReadAll(r.Body)
	r.Body.Close()
	if err != nil || string(b) != "warm" {
		t.Fatalf("warmup body %q/%v", b, err)
	}
	synctest.Wait()
	return q
}

func cancelOn(t *testing.T, p *pool, q *peer) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	ch := roundTrip(p.tr.RoundTrip, request(ctx))
	receive(t, q.headers, "cancellable request HEADERS")
	cancel()
	if r := receive(t, ch, "canceled RoundTrip"); r.err != context.Canceled {
		t.Fatalf("cancellation = %v", r.err)
	}
	if r := receive(t, q.resets, "cancellation RST_STREAM"); r.ErrCode != http2.ErrCodeCancel {
		t.Fatalf("cancellation reset = %v", r.ErrCode)
	}
	synctest.Wait()
}

func checkHealthyAckReusesPool(t *testing.T) {
	p := newPool(t, 2)
	q := warmPool(t, p)
	for i := 0; i < 7; i++ {
		cancelOn(t, p, q)
		// The peer is healthy: acknowledge any health probe actually sent.
		// Historical transports without probes may still preserve healthy reuse.
		select {
		case ping := <-q.pings:
			if ping.IsAck() {
				t.Fatal("unexpected client PING ACK")
			}
			acknowledge(q, ping)
		default:
		}
		// A public successful stream orders the ACK processing before the next cancellation.
		ch := roundTrip(p.tr.RoundTrip, request(context.Background()))
		id := receive(t, q.headers, "healthy reused HEADERS")
		q.respond(id, true, fmt.Sprint(i))
		r := response(t, ch)
		b, err := io.ReadAll(r.Body)
		r.Body.Close()
		if err != nil || string(b) != fmt.Sprint(i) {
			t.Fatalf("healthy response %q/%v", b, err)
		}
		synctest.Wait()
		if n := p.dials.Load(); n != 1 {
			t.Fatalf("healthy cancellation forced replacement: %d dials", n)
		}
	}
}

func TestArenaDeadConnectionReplacement(t *testing.T) {
	for _, max := range []uint32{1, 2, 5} {
		t.Run(fmt.Sprint(max), func(t *testing.T) {
			synctest.Test(t, func(t *testing.T) { checkDeadConnectionReplacement(t, max) })
		})
	}
}

func checkDeadConnectionReplacement(t *testing.T, max uint32) {
	p := newPool(t, max)
	q := warmPool(t, p)
	for i := uint32(0); i < max; i++ {
		cancelOn(t, p, q)
	}
	if n := p.dials.Load(); n != 1 {
		t.Fatalf("replacement before capacity exhausted: %d", n)
	}
	ch := roundTrip(p.tr.RoundTrip, request(context.Background()))
	var fresh *peer
	select {
	case fresh = <-p.peers:
	case id := <-q.headers:
		t.Fatalf("dead connection reused past advertised limit %d (stream %d)", max, id)
	case <-time.After(bound):
		t.Fatal("no bounded replacement of exhausted dead connection")
	}
	receive(t, fresh.settingsAck, "replacement SETTINGS ACK")
	id := receive(t, fresh.headers, "replacement HEADERS")
	fresh.respond(id, true, "replacement")
	r := response(t, ch)
	b, err := io.ReadAll(r.Body)
	r.Body.Close()
	if err != nil || string(b) != "replacement" || p.dials.Load() != 2 {
		t.Fatalf("replacement failed: %q/%v/%d dials", b, err, p.dials.Load())
	}
	if q.pingRequests.Load() != 1 {
		t.Fatalf("dead epoch sent %d PINGs, want one", q.pingRequests.Load())
	}
}

func checkCanceledClosedRequestPreservesSingleUse(t *testing.T) {
	p := newPool(t, 2)
	req := request(context.Background())
	req.Close = true
	ch := roundTrip(p.tr.RoundTrip, req)
	q := receive(t, p.peers, "single-use connection")
	receive(t, q.settingsAck, "single-use SETTINGS ACK")
	id := receive(t, q.headers, "single-use HEADERS")
	q.respond(id, false, "")
	r := response(t, ch)
	reset(t, q, r)
	if q.pingRequests.Load() != 0 {
		t.Fatal("already non-reusable connection was probed")
	}
	next := roundTrip(p.tr.RoundTrip, request(context.Background()))
	fresh := receive(t, p.peers, "single-use replacement")
	receive(t, fresh.settingsAck, "single-use replacement SETTINGS ACK")
	id = receive(t, fresh.headers, "single-use replacement HEADERS")
	fresh.respond(id, true, "")
	response(t, next).Body.Close()
	if p.dials.Load() != 2 {
		t.Fatal("single-use cancellation lost replacement behavior")
	}
}

func TestArenaResetOccupiesCapacity(t *testing.T) { synctest.Test(t, checkResetOccupiesCapacity) }
func TestArenaStrictGateAndAckEpoch(t *testing.T) { synctest.Test(t, checkStrictGateAndAckEpoch) }
func TestArenaNonStrictAdmissionCountsResets(t *testing.T) {
	synctest.Test(t, checkNonStrictAdmissionCountsResets)
}
func TestArenaHealthyAckReusesPool(t *testing.T) { synctest.Test(t, checkHealthyAckReusesPool) }
func TestArenaCanceledClosedRequestPreservesSingleUse(t *testing.T) {
	synctest.Test(t, checkCanceledClosedRequestPreservesSingleUse)
}
'''

ROOT = Path(__file__).resolve().parent
GO = Path(os.environ.get('ARENA_GO') or shutil.which('go') or '/opt/homebrew/bin/go')
CHECKS = {
    'TestArenaResetOccupiesCapacity': 'reset_capacity_accounting',
    'TestArenaStrictGateAndAckEpoch': 'strict_gate_ack_epoch',
    'TestArenaNonStrictAdmissionCountsResets': 'pool_reservation_accounting',
    'TestArenaHealthyAckReusesPool': 'healthy_cancel_reuse',
    'TestArenaDeadConnectionReplacement': 'dead_pool_replacement',
    'TestArenaCanceledClosedRequestPreservesSingleUse': 'single_use_preservation',
}


def inventory(root: Path) -> dict[str, str]:
    rows = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in {'.git', '.autocode', '.venv', '.scenario-runs', '__pycache__'})
        for name in sorted(names):
            p = Path(folder) / name
            if p.is_symlink():
                value = ('symlink:' + os.readlink(p)).encode()
            else:
                value = p.read_bytes()
            rows[p.relative_to(root).as_posix()] = hashlib.sha256(value).hexdigest()
    return rows


def bounded(argv: list[str], cwd: Path, env: dict[str, str], timeout: float):
    start = time.monotonic()
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            output, _ = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _ = proc.communicate(timeout=3)
    return {'argv': argv, 'returncode': proc.returncode, 'timeout': timed_out,
            'seconds': time.monotonic() - start, 'output': output}


def named_test_outcome(test: str, result: dict):
    """Require the requested top-level Go test, not only a process exit code."""
    if result['timeout']:
        return None, 'Go child exceeded its real process deadline'
    lines = result['output'].splitlines()
    runs = [i for i, line in enumerate(lines) if line == '=== RUN   ' + test]
    terminal = re.compile(r'--- (PASS|FAIL|SKIP): ' + re.escape(test)
                          + r' \([0-9]+(?:\.[0-9]+)?s\)')
    endings = [(i, match.group(1)) for i, line in enumerate(lines)
               if (match := terminal.fullmatch(line))]
    if len(runs) != 1 or len(endings) != 1 or runs[0] >= endings[0][0]:
        return None, 'Requested named test did not run and finish exactly once'
    outcome = endings[0][1]
    if outcome == 'SKIP':
        return None, 'Requested named test was skipped'
    expected_exit = 0 if outcome == 'PASS' else 1
    if result['returncode'] != expected_exit:
        return None, 'Named test result disagrees with Go child exit code'
    return outcome, None


def evaluate(workspace: Path, out: Path):
    if not (workspace / 'http2/transport.go').is_file():
        raise ValueError('workspace must be the pinned x/net repository root')
    out.mkdir(parents=True, exist_ok=False)
    before = inventory(workspace)
    submitted = out / 'submitted'
    shutil.copytree(workspace, submitted, ignore=shutil.ignore_patterns('.git', '.autocode', '.venv', '.scenario-runs', '__pycache__'))
    probe = out / 'probe'
    probe.mkdir()
    (probe / 'probe_test.go').write_text(GO_TEST_SOURCE.lstrip('\n'))
    # This replacement points only at the disposable copy, never the original.
    (probe / 'go.mod').write_text('module arena.example/http2probe\n\ngo 1.25.0\n\n'
        'require (\n golang.org/x/net v0.0.0\n golang.org/x/text v0.19.0\n)\n\n'
        f'replace golang.org/x/net => {submitted}\n')
    shutil.copyfile(submitted / 'go.sum', probe / 'go.sum')
    env = os.environ.copy()
    scratch = out / 'go-tmp'
    scratch.mkdir()
    module_cache = env.get('GOMODCACHE') or env.get('ARENA_GO_MODULE_CACHE')
    if not module_cache:
        module_cache = str(Path.home() / 'go/pkg/mod')
    env.update(GOCACHE=env.get('GOCACHE') or str(out / 'go-build-cache'), GOMODCACHE=module_cache,
               GOPATH=env.get('GOPATH') or str(out / 'go-path'), GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off',
               GOWORK='off', GOFLAGS='-mod=readonly', CGO_ENABLED='0',
               GOTMPDIR=str(scratch), TMPDIR=str(scratch), GODEBUG='asynctimerchan=0')
    built = bounded([str(GO), 'test', '-c', '-o', str(out / 'http2-probe.test'), '.'], probe, env, 180)
    (out / 'build.log').write_text(built['output'])
    checks = []
    execution_errors = []
    if built['returncode'] == 0 and not built['timeout']:
        for test, name in CHECKS.items():
            r = bounded([str(out / 'http2-probe.test'), '-test.run', '^' + test + '$',
                         '-test.v', '-test.timeout', '20s', '-test.count', '1'], probe, env, 25)
            (out / (name + '.log')).write_text(r['output'])
            outcome, error = named_test_outcome(test, r)
            if error:
                execution_errors.append({'test': test, 'error': error})
            checks.append({'name': name, 'test': test, 'ok': outcome == 'PASS',
                           'named_test_outcome': outcome, 'execution_error': error,
                           'returncode': r['returncode'], 'timeout': r['timeout'],
                           'seconds': r['seconds'], 'detail': r['output'][-5000:]})
    else:
        checks.append({'name': 'build', 'ok': False, 'detail': built['output'][-5000:]})
    after = inventory(workspace)
    copied_after = inventory(submitted)
    original_intact = before == after
    copy_intact = before == copied_after
    checks.extend([{'name': 'submitted_source_preserved', 'ok': original_intact},
                   {'name': 'disposable_source_preserved', 'ok': copy_intact}])
    result = {'ok': len(checks) == len(CHECKS) + 2 and all(x['ok'] for x in checks),
              'workspace': str(workspace), 'out': str(out), 'checks': checks,
              'execution_errors': execution_errors,
              'build': {k:v for k,v in built.items() if k != 'output'},
              'go': bounded([str(GO), 'version'], probe, env, 10)['output'].strip(),
              'dependencies': {'golang.org/x/text': 'v0.19.0', 'network_during_evaluation': False},
              'source_rows': before,
              'source_rows_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()}
    (out / 'result.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--out', type=Path, help='optional new retained control directory')
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    def run(out):
        if out.is_relative_to(workspace):
            parser.error('output must not be inside submitted source')
        result = evaluate(workspace, out)
        if result['build']['returncode'] != 0 or result['build']['timeout']:
            print(json.dumps({'oracle_setup_error': result['checks']}, sort_keys=True), file=sys.stderr)
            return 2
        if not all(row['ok'] for row in result['checks'] if row['name'].endswith('_source_preserved')):
            print('Submitted source or disposable copy changed during evaluation', file=sys.stderr)
            return 2
        if result['execution_errors']:
            print(json.dumps({'oracle_execution_error': result['execution_errors']}, sort_keys=True), file=sys.stderr)
            return 2
        behavior = [row for row in result['checks'] if row['name'] in CHECKS.values()]
        if len(behavior) != len(CHECKS):
            print('Independent Go probe omitted a required result', file=sys.stderr)
            return 2
        print(json.dumps({'checks': behavior}, sort_keys=True))
        # Arena consumes negative booleans; a valid failing control exits zero.
        return 0
    if args.out:
        return run(args.out.resolve())
    scratch_root = os.environ.get('ARENA_GO_SCRATCH')
    with tempfile.TemporaryDirectory(prefix='arena-http2-oracle-', dir=scratch_root) as temporary:
        return run(Path(temporary) / 'evaluation')


if __name__ == '__main__':
    raise SystemExit(main())
