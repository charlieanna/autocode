"""Refunds follow the store-time window and the running cap (hidden tests): that is check(), the
product and the run verdict. diagnosis() scores apart from it whether AutoResolver, called on source
that still had a planted defect, named one rather than something vague, so a poor diagnosis is never
reported as a false completion of correct code. Reaching AutoResolver at all is the scenario's
precondition (`requires_stages`), judged by the harness."""
import re
import tempfile
from pathlib import Path

from harness import resolver_calls
from harness.oracle import Check, hidden_tests, python_change_checks

HIDDEN = Path(__file__).resolve().parent / "hidden"
# Phrases a diagnosis would use for each planted defect (regular expressions, case-insensitive). Lexical:
# a live attempt is read by a person before it is cited.
DEFECTS = {
    "store time": (r"\butc\b", r"\butc-?8\b", r"\boffset\b", r"\btime ?zone\b", r"\bstore_date\b",
                   r"\bstore[- ](?:time|calendar|local time|day|date)\b", r"\bstore's (?:local|calendar|time)",
                   r"\blocal (?:date|time|day|calendar)\b", r"\bcalendar days?\b"),
    "running cap": (r"\brunning (?:total|sum|cap)\b", r"\bcumulative\b", r"\balready refunded\b",
                    r"\b(?:previous|prior|earlier|existing) (?:partial )?refunds\b", r"\btotal refunded\b",
                    r"\bsum of (?:the |all |previous |prior |earlier )?(?:partial )?refunds\b"),
}


def check(project, scenario, run=None):
    return python_change_checks(project, scenario, "shop")


def diagnosis(project, run):
    """The diagnosis block (scenarios/README.md, "Diagnosis"). The calls are counted by
    harness.resolver_calls; one is on the planted failure when the source it saw fails the hidden tests
    (rebuilt from the runner's code checkpoint or saved diff). The first accepted such call is scored,
    else the first saved one."""
    if run is None:
        return {"verdict": "NOT_EXERCISED", "reason": "no run (check mode)", "checks": []}
    saved = resolver_calls.load_state(project)
    calls = resolver_calls.calls(*saved) if saved else []
    if not calls:
        return {"verdict": "NOT_EXERCISED", "reason": "AutoResolver never ran", "checks": []}
    state, run_dir = saved
    planted = {call["revision"]: _fails_hidden_tests(project, state, run_dir, call["revision"]) for call in calls}
    on_failure = [call for call in calls if planted[call["revision"]]]
    block = {"other_resolver_calls": [{"output": call["output"], "source_revision": (call["revision"] or "")[:12],
                                       "hidden_tests": "pass" if planted[call["revision"]] is False else "not run",
                                       "diagnosis": (call["report"] or {}).get("diagnosis")}
                                      for call in calls if not planted[call["revision"]]]}
    if not on_failure:
        return {"verdict": "NOT_EXERCISED", "checks": [], **block,
                "reason": "AutoResolver never ran on source shown to fail the hidden tests (it passed them, or "
                          "the source could not be rebuilt)"}
    scorable = [call for call in on_failure if call["report"] is not None and call["applied"] and not call["pending"]]
    if not scorable:
        return {"verdict": "UNSCORED", "checks": [], **block,
                "reason": "AutoResolver launched on the planted failure, but no report of it was saved and "
                          "applied by the runner"}
    chosen = next((call for call in scorable if call["accepted"]), scorable[0])
    text = str(chosen["report"].get("diagnosis") or "")
    named = sorted(name for name, words in DEFECTS.items() if any(re.search(word, text, re.I) for word in words))
    checks = [Check("diagnosis_accepted", chosen["accepted"],
                    "" if chosen["accepted"] else f"rejected: {chosen['row'].get('rejection_reason', '')}"[:300]),
              Check("resolver_named_a_planted_defect", bool(named),
                    f"named: {named}" if named else f"named neither defect: {text[:400]}")]
    failed = [check.name for check in checks if not check.ok]
    return {"verdict": "INCORRECT" if failed else "CORRECT", "checks": checks, **block,
            "reason": f"failing: {', '.join(failed)}" if failed else f"named: {named}",
            "resolver_calls_on_failure": len(on_failure), "model": chosen["model"], "cost_usd": chosen["cost_usd"]}


def _fails_hidden_tests(project, state, run_dir, revision):
    """True when the source at ``revision`` fails the hidden tests, False when it passes them, None when it
    cannot be rebuilt."""
    with tempfile.TemporaryDirectory(prefix="oracle-source-") as tmp:
        source = Path(tmp) / "source"
        if not revision or not resolver_calls.checkout(Path(project), state, run_dir, revision, source):
            return None
        return hidden_tests(source, HIDDEN).returncode != 0
