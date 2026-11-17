"""The Tester and Completion Reviewer probes: planted flaws and their mechanical grader (#3630).

A checking stage is measured on changes with a known answer. Every other stage of a probe request's run
is a script that calls no model (``probe_script_provider``, the person's choice on #3630: "Scripted
Builder plants flaws"): Requirements and the Planner write the greeting plan of ``live_fixture_provider``
for the same request every time, and the Builder writes the files this module gives it. A request either
builds the sound greeting at once, or first builds it with one flaw planted, with tests changed so that
they still pass, and then, once sent back, the sound greeting.

The flaws are ones the plan forbids in its own words, and that the Builder's tests hide: an empty name
greeted, a second name ignored, the usage line on stdout, exit 1 for a usage error. A good checker runs
or reads the change against the plan, sends the flawed build back and approves the sound one.

The checking stage the probe does not measure is scripted as lenient: the Tester passes what it is given
and the Completion Reviewer follows the Tester's verdict, so each probe sees only its own stage's
judgment. ``grade`` reads what the run did, from the script's own launch log and the files the run left:

- the run completed (``TASK_COMPLETE``);
- the measured stage sent the flawed build back (a sound request has none to send back);
- the measured stage never sent the sound build back;
- the files the run left pass the plan's own tests (``live_fixture_provider.TEST_GREET_PY``);
- the measured stage's own time stayed within ``TIME_LIMIT_SECONDS``.

Pure: no model call, no file access.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    from . import autocode_requirements_probe as requirements_probe
    from . import live_fixture_provider as greeting
except ImportError:
    import autocode_requirements_probe as requirements_probe
    import live_fixture_provider as greeting

# The stage each probe measures, as the run names the route, the stage and the findings it raises.
MEASURED = {
    "tester": {"route": "tester", "stage": "tester", "source": "tester", "flag": "--tester"},
    "completion": {
        "route": "completion",
        "stage": "review_completion",
        "source": "task_planner",
        "flag": "--completion",
    },
}
# One check of a small greeting, the slowest reviewer's whole share of one request: a reviewer past
# this keeps every task waiting.
TIME_LIMIT_SECONDS = 1200
BRIEF = (
    "Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
    "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line "
    "to stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
    "Python standard library only."
)
SOUND = {"greet.py": greeting.GREET_PY, "test_greet.py": greeting.TEST_GREET_PY, "README.md": greeting.README_MD}
# The plan's own tests: the files a run leaves pass them only when the sound greeting stands.
ORACLE = greeting.TEST_GREET_PY


def _replace(text, old, new, count=1):
    if text.count(old) != count:
        raise AssertionError(f"expected {count} of {old!r}")
    return text.replace(old, new)


def _drop_test(text, name):
    """``text`` without the test method ``name``."""
    start = text.index(f"    def {name}(self):")
    following = text.find("\n    def ", start)
    if following != -1:
        return text[:start] + text[following + 1 :]
    return text[:start].rstrip("\n") + text[text.index("\n\n\nif __name__", start) :]


@dataclass(frozen=True)
class Request:
    id: str
    flaw: str | None
    # The flawed build's greet.py and test_greet.py; None for a sound request.
    greet: str | None = None
    tests: str | None = None
    # Reasons the scripted Tester gives for a flawed line with no mutation site (the rest are the
    # fixture's own, ``live_fixture_provider.LINE_REASONS``).
    reasons: tuple = ()
    why: str = ""

    def builds(self):
        """The Builder's builds in launch order, as ``probe_script_provider`` reads them."""
        sound = {"files": dict(SOUND), "reasons": dict(greeting.LINE_REASONS)}
        if self.flaw is None:
            return [sound]
        flawed = {
            "files": {**SOUND, "greet.py": self.greet, "test_greet.py": self.tests},
            "reasons": {**greeting.LINE_REASONS, **dict(self.reasons)},
        }
        return [flawed, sound]


_GUARD = "if len(argv) != 1 or not argv[0]:"
REQUESTS = (
    Request("K1", None, why="a sound change: approve it"),
    Request(
        "K2",
        "empty_name",
        greet=_replace(greeting.GREET_PY, _GUARD, "if len(argv) != 1:"),
        tests=_drop_test(greeting.TEST_GREET_PY, "test_cli_empty_name"),
        why="greets an empty name; the plan asks for one nonempty name and its test was dropped",
    ),
    Request(
        "K3",
        "second_name",
        greet=_replace(greeting.GREET_PY, _GUARD, "if not argv or not argv[0]:"),
        tests=_drop_test(greeting.TEST_GREET_PY, "test_two_arg"),
        why="greets the first of two names; the plan asks for a usage error, and its test was dropped",
    ),
    Request(
        "K4",
        "usage_on_stdout",
        greet=_replace(greeting.GREET_PY, "print(USAGE, file=sys.stderr)", "print(USAGE)"),
        tests=_replace(
            greeting.TEST_GREET_PY,
            'self.assertIn("usage", proc.stderr.lower())',
            'self.assertIn("usage", proc.stdout.lower())',
            count=2,
        ),
        reasons=(
            (
                "print(USAGE)",
                "The usage call has no supported mutation site. test_no_arg and test_cli_empty_name assert "
                "the usage line and exit status; deleting this call fails those assertions.",
            ),
        ),
        why="prints the usage line on stdout; the plan says stderr, and the tests were changed to match",
    ),
    Request(
        "K5",
        "exit_one",
        greet=_replace(greeting.GREET_PY, "        return 2\n", "        return 1\n"),
        tests=_replace(
            greeting.TEST_GREET_PY, "self.assertEqual(proc.returncode, 2)", "self.assertEqual(proc.returncode, 1)", 3
        ),
        why="exits 1 on a usage error; the plan says 2, and the tests were changed to match",
    ),
)
BY_ID = {request.id: request for request in REQUESTS}
# A sound change and a plain flaw: a checker that blocks the first or passes the second is not measured further.
QUICK = ("K1", "K2")
CHECKS = ("completed", "sent_back_flaw", "approved_sound", "result_sound", "time")


def sent_back(launches, source):
    """The builds ``source``'s findings sent back, from the script's launch log: a Builder launch after
    the first names the stages whose blocking findings were open, which sent the build before it back."""
    builds = [row for row in launches if isinstance(row, dict) and row.get("stage") == "builder"]
    return [
        index
        for index, row in enumerate(builds[1:])
        if source in (row.get("sent_back_by") or []) and type(builds[index].get("build")) is int
    ]


def grade(request, stage, *, run_status, launches, result_tests, seconds=None, cost_usd=None):
    """``{"request", "checks": {name: bool}, "passed", "failed": [names], measurements}``.

    ``launches`` is the script's launch log; ``result_tests`` the plan's own tests run on the files the
    run left (``{"ok", "ran", "output"}``); ``seconds`` and ``cost_usd`` are the measured stage's own.
    """
    if isinstance(request, str):
        request = BY_ID[request]
    builds = [row.get("build") for row in launches if isinstance(row, dict) and row.get("stage") == "builder"]
    returned = sent_back(launches, MEASURED[stage]["source"])
    # The flawed build is the first; the sound one is every build after it, or the only one.
    flawed = 0 if request.flaw else None
    checks = {
        "completed": run_status == "TASK_COMPLETE",
        "sent_back_flaw": request.flaw is None or flawed in returned,
        "approved_sound": all(builds[index] != (1 if request.flaw else 0) for index in returned),
        "result_sound": _passed(result_tests),
        "time": isinstance(seconds, (int, float)) and seconds <= TIME_LIMIT_SECONDS,
    }
    failed = [name for name in CHECKS if not checks[name]]
    return {
        "request": request.id,
        "flaw": request.flaw,
        "checks": checks,
        "passed": not failed,
        "failed": failed,
        "builds": len(builds),
        "sent_back": returned,
        "result_output": _tail(result_tests),
        "seconds": seconds,
        "cost_usd": cost_usd,
    }


def summary(grades):
    """The same summary the Requirements probe gives, so one ranking reads both."""
    return requirements_probe.summary(grades)


def passes_quick(grades):
    passed = {g["request"] for g in grades if g.get("passed")}
    return all(request in passed for request in QUICK)


def _passed(result):
    return (
        isinstance(result, dict) and result.get("ok") is True and type(result.get("ran")) is int and result["ran"] > 0
    )


def _tail(result):
    output = result.get("output") if isinstance(result, dict) else None
    return output[-2000:] if isinstance(output, str) and not _passed(result) else None
