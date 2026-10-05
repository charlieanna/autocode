"""A provider never outlives its controller: the per-attempt stage keeper (#454).

Every bound on a provider's lifetime (the stage cap, idle and tool limits, process-tree
cleanup and the interrupt handler) is a thread inside the controller. A controller that
dies without cleaning up (SIGKILL, a hangup or teardown of its process group) took those
bounds with it, and a provider in its own session ran on to its natural end.

The keeper is a small process tied to the controller by a pipe lifeline. The controller
holds the only write end. When supervision ends normally, or after the interrupt path has
cleaned up, the controller *releases* the keeper (writes ``R``, then closes). When it gives
up instead (failed cleanup, or an exception before supervision started) it writes ``X``.
End of file with neither means the controller died. Without a release the keeper stops the
identity-checked provider tree at once (freeze, TERM, KILL after the existing 2 s grace)
and writes ``<attempt>.supervision.json`` with the cause: ``supervisor_lost`` or
``lifeline_closed_by_live_owner``. The cause comes from the message, not from probing the
controller, whose descriptors close before it is seen to exit. Pipes behave the same on
Linux and macOS; nothing here depends on PDEATHSIG or a subreaper.

Launch inverts the fork so nothing about the provider's process changes::

    controller ── Popen ──> launcher L  (python -I -S -B this file SPEC)
    L forks K1; K1 forks K2 (the keeper) and exits; K2 calls setsid() and reports "armed"
    L then execs the provider command in place

The provider *is* L: the controller's direct child, with the pid, session, process group,
descriptors, environment and exit status ``subprocess.Popen`` gives it. The keeper is
neither its descendant nor a member of its group, so ``ProcessTree``, the activity monitor
and ``child.poll()`` see exactly what they saw before. Wrapped commands (visual runtime,
tool containment) are exec'd as given, so the wrapper is what the keeper watches.

Runtime layer: imports only ``autocode_process`` and ``autocode_util``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

if __name__ == "__main__":
    # Launcher: nothing this interpreter prints may reach the provider's event stream,
    # which is this process's stdout and stderr. stderr is restored for the provider.
    import warnings
    warnings.simplefilter("ignore")
    _PROVIDER_STDERR = os.dup(2)
    _quiet = os.open(os.devnull, os.O_WRONLY)
    os.dup2(_quiet, 2)
    os.close(_quiet)
    sys.path.extend(json.loads(Path(sys.argv[1]).read_text())["import_paths"])

try:
    from . import autocode_process as processes, autocode_util as util
except ImportError:
    import autocode_process as processes
    import autocode_util as util

SPEC_SUFFIX = ".supervision-spec.json"
REPORT_SUFFIX = ".supervision.json"
RELEASE = b"R"  # supervision ended cleanly; the keeper exits and touches nothing
ABANDON = b"X"  # the controller gave up; the keeper stops the tree
ARM_SECONDS = 10  # launcher start, two forks and identity lookups; a few hundred ms in practice
ARM_FAILED = 70   # launcher exit status when the keeper did not arm; the provider never ran
EXEC_FAILED = 127

# A Popen replaced after this module loaded (a test's scripted provider) owns the process
# boundary: it receives the provider command as before and is not supervised here.
_POPEN = subprocess.Popen


class SupervisionError(processes.ProcessError):
    """The keeper did not arm; the provider was never started."""


def report_path(base) -> Path:
    base = Path(base)
    return base.with_name(base.name + REPORT_SUFFIX)


class Lifeline:
    """The controller's side of one attempt's keeper.

    ``launch`` starts the provider behind an armed keeper and records ``pid`` and
    ``supervision`` (the keeper's identity and its report path) in ``record``, calling
    ``save`` so both are durable before supervision starts. ``supervise`` runs the
    controller's own wait and releases the keeper only when that wait finished its
    cleanup (normal end or interrupt). Leaving the ``with`` block any other way closes
    the lifeline without a release, so the keeper stops whatever is left.
    """

    def __init__(self, base, run_dir, record, save=None):
        self.base = Path(base)
        self.run_dir = Path(run_dir)
        self.record = record
        self.save = save
        self._lifeline = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False

    def launch(self, command, **options):
        if subprocess.Popen is not _POPEN:
            child = subprocess.Popen(command, **options)
            self.record["pid"] = child.pid
            return child
        spec_path = self.base.with_name(self.base.name + SPEC_SUFFIX)
        lifeline_r = ready_r = ready_w = None
        try:
            lifeline_r, self._lifeline = (_above_stdio(fd) for fd in os.pipe())
            ready_r, ready_w = (_above_stdio(fd) for fd in os.pipe())
            environment = options.get("env")
            spec = {"command": list(command), "executable": options.pop("executable", None),
                    "report": str(report_path(self.base)),
                    "receipt": str(self.run_dir / "active-processes.json"),
                    "lifeline": lifeline_r, "ready": ready_w, "import_paths": _import_paths(),
                    "lc_ctype": (os.environ if environment is None else environment).get("LC_CTYPE")}
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(json.dumps(spec))
            passed = tuple(dict.fromkeys((*options.pop("pass_fds", ()), lifeline_r, ready_w)))
            try:
                child = subprocess.Popen([sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()),
                                          str(spec_path)], **{**options, "pass_fds": passed, "close_fds": True})
            finally:
                for fd in (lifeline_r, ready_w):
                    os.close(fd)
                lifeline_r = ready_w = None
            try:
                messages = _read_messages(ready_r)
                armed = next((m for m in messages if "keeper" in m), None)
                failed = next((m for m in messages if "exec_error" in m), None)
                if armed is None:
                    raise SupervisionError(
                        "Stage keeper did not arm" + (f" ({messages[-1]['arm_error']})" if messages
                                                      and "arm_error" in messages[-1] else "")
                        + "; the provider was not started")
                if failed:
                    self.release()  # nothing ran; the keeper has nothing to stop
                    child.wait()
                    error = failed["exec_error"]
                    raise OSError(error["errno"], error["strerror"], error.get("filename"))
            except BaseException:
                _discard(child)
                raise
            self.record["pid"] = child.pid
            self.record["supervision"] = {"keeper": armed["keeper"], "report": spec["report"]}
            if self.save:
                self.save()
            return child
        except BaseException:
            self.close()
            raise
        finally:
            for fd in (lifeline_r, ready_r, ready_w):
                if fd is not None:
                    os.close(fd)
            spec_path.unlink(missing_ok=True)

    def supervise(self, wait, *args, **kwargs):
        try:
            result = wait(*args, **kwargs)
        except KeyboardInterrupt:
            self.release()  # the wait stopped the tree before propagating the interrupt
            raise
        except BaseException:
            self.close()  # cleanup failed or is uncertain: the keeper stops what is left
            raise
        self.release()
        return result

    def release(self):
        self._end(RELEASE)

    def close(self):
        """End supervision without a release: the keeper stops whatever is left."""
        self._end(ABANDON)

    def _end(self, message):
        fd, self._lifeline = self._lifeline, None
        if fd is not None:
            try:
                os.write(fd, message)
            except OSError:
                pass  # the keeper is gone
            finally:
                os.close(fd)


def _above_stdio(fd):
    """Keep pipe ends off 0-2 so Popen's stdio redirection cannot overwrite them."""
    if fd > 2:
        return fd
    import fcntl
    try:
        return fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)
    finally:
        os.close(fd)


def _import_paths():
    """Where the launcher finds this runtime and the same psutil the controller uses."""
    psutil_file = getattr(processes.psutil, "__file__", None)
    if not psutil_file:
        raise SupervisionError("psutil is unavailable; refusing an unsupervised provider launch")
    return list(dict.fromkeys((str(Path(__file__).resolve().parent),
                               str(Path(psutil_file).resolve().parent.parent))))


def _read_messages(ready):
    """Read the launcher's JSON lines until it execs (end of file) or the deadline."""
    deadline = time.monotonic() + ARM_SECONDS
    data = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SupervisionError(f"Stage keeper did not arm within {ARM_SECONDS} s; the provider was not started")
        if not select.select([ready], [], [], remaining)[0]:
            continue
        chunk = os.read(ready, 65536)
        if not chunk:
            break
        data += chunk
    messages = []
    for line in data.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            messages.append(value)
    return messages


def _discard(child):
    """Stop the controller's own unreaped launcher child (its pid cannot be reused)."""
    if child.poll() is None:
        try:
            if os.getpgid(child.pid) == child.pid:
                os.killpg(child.pid, signal.SIGKILL)
            else:
                child.kill()
        except (ProcessLookupError, PermissionError):
            pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


# ---- launcher (L) and keeper (K2), run as a separate interpreter ----

def _launch(spec_path):
    spec = json.loads(Path(spec_path).read_text())
    ready, lifeline = spec["ready"], spec["lifeline"]
    os.set_inheritable(ready, False)  # closes when L execs: end of file tells the controller
    target, owner = os.getpid(), os.getppid()
    line = b""
    try:
        arm_r, arm_w = os.pipe()
        first = os.fork()
        if first == 0:
            try:
                if os.fork() == 0:
                    os._exit(_keep(spec, lifeline, arm_w, target, owner))
            finally:
                os._exit(0)
        os.close(arm_w)
        os.waitpid(first, 0)
        while not line.endswith(b"\n"):
            chunk = os.read(arm_r, 65536)
            if not chunk:
                break
            line += chunk
        os.close(arm_r)
        armed = json.loads(line) if line.strip() else {}
    except Exception as error:
        armed = {"arm_error": f"{type(error).__name__}: {error}"}
    if "keeper" not in armed:
        _send(ready, armed if "arm_error" in armed else {"arm_error": "the keeper exited before arming"})
        os._exit(ARM_FAILED)
    if not _send(ready, armed):
        os._exit(ARM_FAILED)  # the controller is gone; its keeper stops this launcher
    os.close(lifeline)
    if spec.get("lc_ctype") is None:
        os.environ.pop("LC_CTYPE", None)  # undo this interpreter's C-locale coercion (PEP 538)
    else:
        os.environ["LC_CTYPE"] = spec["lc_ctype"]
    for name in ("SIGPIPE", "SIGXFSZ"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_DFL)  # as Popen(restore_signals=True)
    os.dup2(_PROVIDER_STDERR, 2)
    command = spec["command"]
    try:
        os.execvpe(spec.get("executable") or command[0], command, os.environ)
    except OSError as error:
        os.dup2(os.open(os.devnull, os.O_WRONLY), 2)
        _send(ready, {"exec_error": {"errno": error.errno, "strerror": error.strerror,
                                     "filename": error.filename}})
    os._exit(EXEC_FAILED)


def _send(fd, value):
    try:
        os.write(fd, json.dumps(value).encode() + b"\n")
        return True
    except OSError:
        return False


def _keep(spec, lifeline, arm_w, target_pid, owner_pid):
    """K2: arm, wait on the lifeline, and stop the tree if it closes without a release."""
    try:
        os.setsid()
        quiet = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(quiet, fd)
        _close_descriptors_except({0, 1, 2, lifeline, arm_w})
        os.chdir("/")
        for name in ("SIGINT", "SIGHUP"):
            signal.signal(getattr(signal, name), signal.SIG_IGN)
        table = processes.process_table({target_pid, owner_pid, os.getpid()})
        rows = {pid: processes.identity(table[pid]) for pid in (target_pid, owner_pid, os.getpid())}
        os.write(arm_w, json.dumps({"keeper": rows[os.getpid()], "target": rows[target_pid],
                                    "owner": rows[owner_pid]}).encode() + b"\n")
        os.close(arm_w)
    except BaseException:
        return 1
    try:
        received = b""
        while True:
            chunk = os.read(lifeline, 64)
            if not chunk:
                break
            received += chunk
        if RELEASE in received:
            return 0
        util.atomic_json(spec["report"], stop(rows[target_pid], owner=rows[owner_pid], keeper=rows[os.getpid()],
                                              receipt=spec["receipt"], abandoned=ABANDON in received))
        return 0
    except BaseException:
        return 1


def _close_descriptors_except(keep):
    try:
        descriptors = [int(name) for name in os.listdir("/dev/fd")]
    except (OSError, ValueError):
        descriptors = range(3, min(os.sysconf("SC_OPEN_MAX"), 65536))
    for fd in descriptors:
        if fd not in keep:
            try:
                os.close(fd)
            except OSError:
                pass


class _Provider:
    """poll()/wait() for a provider that is not the keeper's child: never reaps it."""

    def __init__(self, row):
        self.row = row

    def poll(self):
        return None if processes.live_processes([self.row]) else 0

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("provider", timeout)
            time.sleep(.05)
        return 0


class _Tree(processes.ProcessTree):
    """The controller's ProcessTree, recording each identity-checked signal batch."""

    def __init__(self, target):
        super().__init__(target["pid"], lambda rows: None)
        self.known = {target["pid"]: dict(target)}
        self.sent = []

    def signal(self, rows, sig):
        table = processes.process_table({row["pid"] for row in rows})
        verified = [row for row in rows if processes.matches(row, table.get(row["pid"]))]
        super().signal(verified, sig)  # re-checks identity immediately before signalling
        if verified:
            self.sent.append({"signal": signal.Signals(sig).name, "pids": sorted(row["pid"] for row in verified)})


def receipt_rows(receipt, target):
    """The controller's last process receipt, only when it belongs to this provider."""
    try:
        saved = json.loads(Path(receipt).read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(saved, dict) or saved.get("pid") != target["pid"]:
        return []
    return [row for row in saved.get("processes") or []
            if isinstance(row, dict) and isinstance(row.get("pid"), int) and row["pid"] != target["pid"]]


def stop(target, *, owner, keeper, receipt, abandoned=False):
    """Stop the provider tree once supervision is lost; return the supervision report.

    Seeds the controller's ``ProcessTree`` with the provider's birth identity and the
    receipt rows the controller recorded for it, then runs the same freeze/TERM/KILL
    stop. Identity is re-checked before every signal; a reused pid, another run's
    process or an unrelated one is never signalled. ``abandoned`` means the controller
    closed the lifeline itself; otherwise it died.
    """
    tree = _Tree(target)
    for row in receipt_rows(receipt, target):
        tree.known.setdefault(row["pid"], row)
    report = {"version": 1, "detected_at": util.now(),
              "cause": "lifeline_closed_by_live_owner" if abandoned else "supervisor_lost",
              "owner": owner, "target": target, "keeper": keeper}
    try:
        live = tree.sample(notify=False)
        report["live_at_detection"] = sorted(row["pid"] for row in live)
        if live:
            tree.stop(_Provider(target))
        report["outcome"] = "stopped" if live else "already_exited"
    except (processes.ProcessError, OSError) as error:
        report["outcome"] = "failed"
        report["error"] = str(error)
        try:
            report["remaining"] = sorted(row["pid"] for row in processes.live_processes(list(tree.known.values())))
        except (processes.ProcessError, OSError):
            report["remaining"] = None
    report["signals"] = tree.sent
    try:
        report["owner_alive_after_stop"] = bool(processes.live_processes([owner]))
    except (processes.ProcessError, OSError):
        report["owner_alive_after_stop"] = None
    report["finished_at"] = util.now()
    return report


if __name__ == "__main__":
    _launch(sys.argv[1])
