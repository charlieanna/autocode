"""Phase-owned environments for acceptance sequences (issue #225).

An acceptance sequence runs several phases in a row — preparation, primary
tests, a stats smoke, later compatibility suites. Reusing one mutable
credential/config environment across them lets one phase's synthetic account
setup become an undeclared input of a later phase: a leftover
``.credentials.json`` makes a later startup poll its default usage
destination, and when the production client swallows the transport refusal
the phase's own tests stay green while the acceptance result is no longer
trustworthy.

This module makes the phases' inputs explicit. Each phase owns a declared
credential/config/state/cache root under the sequence's fresh base, the
subprocess environment carries those roots without ever overriding the
ambient HOME (the parent model-provider OAuth home stays untouched), and one
recording path per phase retains every request the transport guard refused —
at setup, call or teardown time — so a swallowed refusal stays visible to the
aggregate observer instead of hiding behind a green child exit.

The transport guard is isolation, not an allowlist change: only loopback
destinations explicitly declared for the phase may be reached, and every
other destination is refused in-process before any socket is opened.

Standard library only; part of the harness package and never imports
AutoCode, so the scenarios stay black box.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

HARNESS_ROOT = Path(__file__).resolve().parent.parent  # the scenarios/ directory

DEFAULT_USAGE_URL = "http://usage.synthetic.invalid/v1/usage"
CREDENTIALS_FILENAME = ".credentials.json"

# verdict.py's ERROR semantics, reused verbatim for contamination reports.
ERROR_SEMANTICS = "the run or the oracle broke; no judgement possible"

GREEN = "GREEN"  # every check ok and no refused transport anywhere in the sequence
ERROR = "ERROR"  # phase contamination: refused transport; no judgement possible, never a pass
FAILED = "FAILED"  # checks failed without transport contamination


class RefusedTransportError(RuntimeError):
    """A request to a non-loopback or undeclared destination, refused before any socket was opened."""


class PhaseEvidenceError(RuntimeError):
    """A phase's pre-created refusal ledger is missing or unreadable."""


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, guard):
        self.guard = guard

    def redirect_request(self, request, response, code, message, headers, new_url):
        self.guard.check(request.get_method(), new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def is_loopback_host(host: str) -> bool:
    """String-level loopback check only: refusing must never need DNS or a socket."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _destination(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return host, port


def normalize_endpoint(endpoint: str) -> str:
    """``host:port``, accepting a full URL whose scheme supplies the default port."""
    if "://" in endpoint:
        host, port = _destination(endpoint)
        return f"{host}:{port}"
    host, separator, port = endpoint.lower().rpartition(":")
    if not separator or not port.isdigit():
        raise ValueError(f"endpoint must be 'host:port' or a URL, got {endpoint!r}")
    return f"{host.strip('[]')}:{port}"


def credentials_path(credential_root) -> Path:
    return Path(credential_root) / CREDENTIALS_FILENAME


def write_synthetic_credentials(credential_root, token: str) -> Path:
    """Write the synthetic credential a stats phase leaves behind (never a real one)."""
    path = credentials_path(credential_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")
    return path


def find_credentials(credential_root) -> Path | None:
    """The credential a startup would discover in this root, if any."""
    path = credentials_path(credential_root)
    return path if path.exists() else None


def read_unexpected_requests(requests_log) -> list[dict]:
    """Every refusal recorded for a phase, in the order it was refused."""
    path = Path(requests_log)
    try:
        entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        if any(not isinstance(entry, dict) for entry in entries):
            raise ValueError('refusal records must be objects')
        return entries
    except (OSError, ValueError) as error:
        raise PhaseEvidenceError(f'refusal ledger is missing or unreadable: {path}') from error


def _inside(path, base: Path) -> bool:
    try:
        Path(path).resolve().relative_to(base)
        return True
    except ValueError:
        return False


def guard(env: dict | None = None) -> PhaseGuard:
    """The transport guard for the current phase environment, or an explicit one."""
    return PhaseGuard(env)


class PhaseGuard:
    """Loopback-only transport guard for one phase's declared destinations.

    ``get`` refuses non-loopback destinations and loopback destinations that
    were not declared for the phase, in-process and before any socket is
    opened, and appends each refusal to the phase's single recording path —
    so the violation survives even a production client that catches the
    exception and returns "no snapshot" behind a green child exit, whatever
    point of the phase lifecycle it happens at.
    """

    def __init__(self, env: dict | None = None):
        env = os.environ if env is None else env
        self.phase = env.get("PHASE_NAME", "")
        self.traffic_identity = env.get("PHASE_TRAFFIC_IDENTITY", "")
        self.allowed = {normalize_endpoint(endpoint)
                        for endpoint in json.loads(env.get("PHASE_ALLOWED_ENDPOINTS", "[]"))}
        self.requests_log = env.get("PHASE_UNEXPECTED_REQUESTS")
        if not self.requests_log:
            raise RuntimeError("no PHASE_UNEXPECTED_REQUESTS in this environment; "
                               "build it with Phase.build_env()")

    def check(self, method: str, url: str) -> None:
        try:
            host, port = _destination(url)
        except ValueError:
            self._refuse(method, url, "invalid destination")
        if not is_loopback_host(host):
            self._refuse(method, url, f"non-loopback destination {host}:{port}")
        if f"{host}:{port}" not in self.allowed:
            self._refuse(method, url, f"{host}:{port} is not a declared destination of phase {self.phase!r}")

    def get(self, url: str, timeout: int = 10):
        self.check("GET", url)
        # Check every redirect before connecting again. Ambient proxy settings
        # must not route an admitted loopback request to another destination.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _GuardedRedirect(self))
        return opener.open(url, timeout=timeout)

    def _refuse(self, method: str, url: str, reason: str) -> None:
        entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                 "phase": self.phase,
                 "traffic_identity": self.traffic_identity,
                 "method": method,
                 "url": url,
                 "reason": reason,
                 "refused_before_socket": True}
        path = Path(self.requests_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        raise RefusedTransportError(f"{method} {url} refused before any socket was opened: {reason}")


class Phase:
    """One acceptance phase with declared roots and its own subprocess environment."""

    def __init__(self, sequence: PhaseSequence, name: str, *, traffic_identity: str | None = None,
                 credential_root=None, share_credential_root_with: Phase | None = None,
                 allowed_endpoints=(), root_vars=None):
        self.sequence = sequence
        self.name = name
        self.traffic_identity = traffic_identity or f"synthetic-{name}"
        shared = (share_credential_root_with.credential_root
                  if share_credential_root_with is not None else None)
        self.credential_root = Path(credential_root or shared or sequence.base / name / "credentials")
        self.config_root = sequence.base / name / "config"
        self.state_root = sequence.base / name / "state"
        self.cache_root = sequence.base / name / "cache"
        self.root_vars = dict(root_vars or {})
        owned = {'credential_root', 'config_root', 'state_root', 'cache_root'}
        for variable, root in self.root_vars.items():
            if (not isinstance(variable, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', variable)
                    or variable in {'HOME', 'PATH', 'CODEX_HOME', 'PYTHONPATH', 'PYTHONHOME'}
                    or variable.startswith(('PHASE_', 'OPENCODE_')) or not isinstance(root, str) or root not in owned):
                raise ValueError('application root bindings must name an owned phase root and preserve provider inputs')
        roots = (self.credential_root, self.config_root, self.state_root, self.cache_root)
        if any(not _inside(root, sequence.base) for root in roots):
            raise ValueError('declared phase roots must stay inside the fresh sequence base')
        for root in roots:
            root.mkdir(parents=True, exist_ok=True)
        self.allowed_endpoints = sorted({normalize_endpoint(endpoint) for endpoint in allowed_endpoints})
        self.requests_log.parent.mkdir(parents=True, exist_ok=True)
        self.requests_log.touch(exist_ok=False)
        self.results: list = []

    @property
    def requests_log(self) -> Path:
        """The one recording path every refusal in this phase's lifecycle lands in."""
        return self.sequence.base / '.phase-evidence' / f'{self.name}.jsonl'

    def build_env(self, **extra: str) -> dict:
        """The phase's subprocess environment: declared roots on top of the ambient one.

        The sequence environment is copied — including its preserved HOME, so
        the parent model-provider OAuth home stays untouched — and the phase's declared
        inputs are added on top. HOME is never set, removed or rewritten here.
        Extra values may add unrelated variables; HOME and the declared phase
        bindings cannot be overridden.
        """
        declared = {
            "PHASE_NAME": self.name,
            "PHASE_TRAFFIC_IDENTITY": self.traffic_identity,
            "PHASE_CREDENTIAL_ROOT": str(self.credential_root),
            "PHASE_CONFIG_ROOT": str(self.config_root),
            "PHASE_STATE_ROOT": str(self.state_root),
            "PHASE_CACHE_ROOT": str(self.cache_root),
            "PHASE_UNEXPECTED_REQUESTS": str(self.requests_log),
            "PHASE_ALLOWED_ENDPOINTS": json.dumps(self.allowed_endpoints),
            "PHASE_HARNESS_ROOT": str(HARNESS_ROOT),
        }
        declared.update({variable: str(getattr(self, root)) for variable, root in self.root_vars.items()})
        conflicts = set(extra) & (set(declared) | {"HOME"})
        if conflicts:
            raise ValueError("phase-owned inputs cannot be overridden: " + ", ".join(sorted(conflicts)))
        env = dict(self.sequence._env)
        env.update(declared)
        env.update(extra)
        return env

    def run(self, cmd, *, cwd=None, timeout: int = 120, input: str | None = None,
            env: dict | None = None):
        """Run a phase subprocess through the maintained oracle boundary, in its
        declared environment, and keep the result for the sequence's checks.
        ``env`` adds unrelated variables while preserving the phase bindings."""
        from harness import oracle  # deferred: this module must stay importable standalone

        merged = self.build_env(**(env or {}))
        result = oracle.run(cmd, Path(cwd) if cwd is not None else self.sequence.base,
                            timeout=timeout, input=input, env=merged)
        self.results.append(result)
        return result

    def unexpected_requests(self) -> list[dict]:
        return read_unexpected_requests(self.requests_log)

    def record(self) -> dict:
        """The phase's effective roots, synthetic identity and retained refusals."""
        evidence_error = None
        try:
            unexpected = self.unexpected_requests()
        except PhaseEvidenceError as error:
            unexpected, evidence_error = None, str(error)
        return {"phase": self.name,
                "environment_roots": {variable: str(getattr(self, root)) for variable, root in self.root_vars.items()},
                "credential_root": str(self.credential_root),
                "config_root": str(self.config_root),
                "state_root": str(self.state_root),
                "cache_root": str(self.cache_root),
                "traffic_identity": self.traffic_identity,
                "allowed_endpoints": list(self.allowed_endpoints),
                "requests_log": str(self.requests_log),
                "unexpected_requests": unexpected,
                "evidence_error": evidence_error}


class PhaseSequence:
    """A fresh base plus the phases of one acceptance sequence.

    Every root and record a sequence creates lies under its own base, so
    sequences (variants, controls, reruns) cannot read one another's state and
    prior evidence directories stay untouched.
    """

    def __init__(self, name: str, base, *, env=None):
        # Freeze before creating roots; never clear or alter the provider parent's
        # environment. Explicit maps can omit account variables for acceptance.
        ambient = dict(os.environ)
        self._env = dict(ambient if env is None else env)
        if env is not None:
            if 'HOME' in self._env and self._env['HOME'] != ambient.get('HOME'):
                raise ValueError('acceptance preparation must preserve the provider OAuth HOME')
            if 'HOME' in ambient:
                self._env['HOME'] = ambient['HOME']
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self._env.items()):
            raise ValueError('phase environment entries must be strings')
        self.name = name
        self.base = Path(base).resolve()
        self.base.mkdir(parents=True, exist_ok=True)
        if any(self.base.iterdir()):
            raise ValueError('sequence base must be fresh and empty; prior evidence cannot be reused')
        try:
            with (self.base / '.sequence-owner').open('x') as owner:
                owner.write(json.dumps({'sequence': name}) + '\n')
        except FileExistsError as error:
            raise ValueError('sequence base is already owned by another sequence') from error
        self.phases: list[Phase] = []

    def phase(self, name: str, **kwargs) -> Phase:
        if (not isinstance(name, str) or name in ('', '.', '..')
                or Path(name).name != name or '\\' in name):
            raise ValueError('phase names must be single path components')
        if any(phase.name.casefold() == name.casefold() for phase in self.phases):
            raise ValueError(f'duplicate phase name: {name}')
        phase = Phase(self, name, **kwargs)
        self.phases.append(phase)
        return phase

    def finish(self) -> dict:
        """Score the sequence: refused transport anywhere is contamination.

        A refused or swallowed-but-recorded request makes the outcome ERROR
        with the harness's ERROR semantics — the run or the oracle broke; no
        judgement possible — whatever the child exit codes were, so a green
        child exit alone can never produce acceptance.
        """
        phase_records = [phase.record() for phase in self.phases]
        checks = []
        for phase, phase_record in zip(self.phases, phase_records, strict=False):
            for index, result in enumerate(phase.results, start=1):
                checks.append({"name": f"{phase.name}: subprocess {index} exit",
                               "ok": result.returncode == 0,
                               "detail": f"exit={result.returncode}"})
            roots = [phase_record[key] for key in ("credential_root", "config_root", "state_root", "cache_root")]
            stray = [root for root in roots if not _inside(root, self.base)]
            checks.append({"name": f"{phase.name}: roots inside the sequence base",
                           "ok": not stray, "detail": stray})
            checks.append({"name": f"{phase.name}: refusal ledger readable",
                           "ok": not phase_record["evidence_error"],
                           "detail": phase_record["evidence_error"]})
            checks.append({"name": f"{phase.name}: no unexpected requests",
                           "ok": phase_record["unexpected_requests"] == [],
                           "detail": phase_record["unexpected_requests"]})
        missing_evidence = [record["phase"] for record in phase_records if record["evidence_error"]]
        contaminated = [record["phase"] for record in phase_records if record["unexpected_requests"]]
        if missing_evidence:
            outcome, reason = ERROR, (f"phase evidence unavailable in {', '.join(missing_evidence)}; "
                                      f"{ERROR_SEMANTICS}")
        elif contaminated:
            outcome, reason = ERROR, ("phase contamination: refused default or non-loopback transport in "
                                      f"{', '.join(contaminated)}; {ERROR_SEMANTICS}")
        elif all(check["ok"] for check in checks):
            outcome, reason = GREEN, "every check ok"
        else:
            outcome, reason = FAILED, "checks failed without transport contamination"
        record = {"sequence": self.name, "base": str(self.base), "phases": phase_records,
                  "checks": checks, "outcome": outcome, "reason": reason}
        record_path = self.base / "sequence-record.json"
        record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        record["record_path"] = str(record_path)
        return record
