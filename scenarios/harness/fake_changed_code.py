"""Handwritten independent judgments for exact, tested scenario fixtures.

This is not a generic reference-solution waiver. Unknown bytes, weak tests and
surviving/unrun mutants receive no judgment; the core still authenticates every
offered gap, source pin and cited runner proof.
"""

import copy
import hashlib
import json
from pathlib import Path


def _review_data(data):
    """Read the runner's pinned full rows before judging summarized proof detail."""
    fields = {
        "regression_proof_mutation_files": ("mutation_check", "files"),
        "regression_proof_obligations": ("changed_code_evidence", "obligations"),
    }
    artifact = data.get("context_artifact") or {}
    if not isinstance(artifact, dict):
        return None
    declared = artifact.get("fields") or {}
    if not isinstance(declared, dict):
        return None
    requested = fields.keys() & declared.keys()
    if requested:
        try:
            raw = Path(artifact["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
                return None
            saved = json.loads(raw)
            result = copy.deepcopy(data)
            proof = result.get("regression_proof") or {}
            for name in requested:
                rows = saved[name]
                if (
                    not isinstance(rows, list)
                    or not all(isinstance(row, dict) for row in rows)
                    or declared[name].get("total") != len(rows)
                ):
                    return None
                holder, key = fields[name]
                proof.setdefault(holder, {})[key] = rows
            result["regression_proof"] = proof
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
    else:
        result = data
    proof = result.get("regression_proof") or {}
    for row in (proof.get("mutation_check") or {}).get("files") or []:
        if not isinstance(row, dict) or not isinstance(row.get("mutants", []), list):
            return None  # A count without readable full rows is not mutation evidence.
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        if not isinstance(row, dict) or "gaps" in row or "tested_lines" in row:
            return None  # Grouped handles cannot substitute for the pinned gap identities.
    return result


STOCK_SOURCE = "ab756c8dc262940703efb13321038874624b89f55bb946bd403897e22da7b9d0"
STOCK_TESTS = "f16ce9d71d25577c9df3b74ba30b680fba7ec85a46204f09f405a8a161aa1e30"
STOCK_DOCUMENTATION = b"\n# Transfers retain the existing validation rules.\n"
STOCK_REASONS = {
    70: "The on-hand lookup has no supported mutation. The move/remove tests assert exact remaining quantities and refuse overdraw without changing stored bytes; omitting or replacing this lookup breaks those CLI results.",
    73: "The stock assignment has no supported mutation. test_c1_move_transfers_units and test_c2_remove_takes_units_and_drops_empty_holdings assert the decremented source quantities; leaving the assignment out retains the old quantity and fails those tests.",
    81: "The quantity conversion call has no supported mutation. The move success test requires integer arithmetic and test_c6_non_positive_quantity_is_refused requires refusal with unchanged bytes; omitting conversion breaks those outcomes.",
    84: "The move load call has no supported mutation. test_c1_move_transfers_units preloads stock and checks both locations after the command, so failing to load that data cannot satisfy the assertions.",
    85: "The source-decrement call has no supported mutation. test_c1_move_transfers_units asserts A1 bolt 3 after moving two of five; omitting this call leaves five and fails the assertion.",
    86: "The destination-container assignment has no supported mutation. test_c1_move_transfers_units starts with no B2 and requires B2 bolt 2 after moving; omitting container creation cannot pass.",
    87: "The destination increment has no supported mutation. test_c1_move_transfers_units checks exact B2 bolt 2; omitting or discarding the transferred quantity fails this observed CLI assertion.",
    88: "The move save call has no supported mutation. test_c1_move_transfers_units invokes a separate show command and checks persisted quantities at both locations; in-memory changes alone fail it.",
    93: "The remove quantity conversion has no supported mutation. The remove success/overdraw tests require numeric subtraction and refusal at the quantity boundary; discarding the conversion cannot satisfy those CLI outcomes.",
    94: "The remove load call has no supported mutation. The remove tests preload two holdings and assert the correct persisted remainder; a missing or wrong load cannot satisfy that state transition.",
    95: "The remove decrement call has no supported mutation. test_c2_remove_takes_units_and_drops_empty_holdings requires the bolt holding to disappear while nut remains, and the last-holding test requires an empty store; omitting the call fails them.",
    96: "The remove save call has no supported mutation. The removal tests invoke show again and read stock.json, requiring the new state on disk; discarding this call fails those assertions.",
}
for number, command, part in (
    (108, "move", "subcommand"),
    (109, "move", "sku"),
    (110, "move", "qty"),
    (111, "move", "source"),
    (112, "move", "target"),
    (113, "move", "handler"),
    (114, "remove", "subcommand"),
    (115, "remove", "sku"),
    (116, "remove", "qty"),
    (117, "remove", "location"),
    (118, "remove", "handler"),
):
    STOCK_REASONS[number] = (
        f"The argparse {command} {part} registration has no supported mutation. "
        f"The {command} CLI tests supply its declared positional arguments and check successful persisted "
        "stock changes plus refusal without changes. Omitting this registration causes a parser/namespace "
        "error or dispatches the wrong handler, which cannot satisfy those assertions. Help wording is not "
        "a separate behavioral requirement."
    )


def _stock(data, root):
    proof = data.get("regression_proof") or {}
    try:
        source = (root / "stock.py").read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        if digest != STOCK_SOURCE and not (
            source.endswith(STOCK_DOCUMENTATION)
            and hashlib.sha256(source[: -len(STOCK_DOCUMENTATION)]).hexdigest() == STOCK_SOURCE
        ):
            return []
        if hashlib.sha256((root / "tests/test_stock.py").read_bytes()).hexdigest() != STOCK_TESTS:
            return []
    except OSError:
        return []
    if not proof.get("path"):
        return []
    accepted = []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        if (
            row.get("status") != "reason_required"
            or row.get("path") != "stock.py"
            or row.get("sha256") != digest
            or row.get("mutation_outcomes")
        ):
            continue
        if reason := STOCK_REASONS.get(row.get("line")):
            accepted.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], "stock.py"],
                }
            )
    return accepted


TOTALS_INPUTS = {
    (
        "4502f5a1070a87cdd3e9914f1a99722a51827974bed9f1e396469ca2b16ab622",
        "754c24b368b2eaa52ca21f346a170f3c6ea607bef5a19db2dec641bccbc66cbd",
    ),
    (
        "19987a5986ced2c6f2a9608117416e4b00358bb6b0364b60af6da68216ed0eaa",
        "76a94517effe97ad908f0ad554a3fb20d44802e601d008d11ddd1208c165d9bc",
    ),
}


def _totals(data, root):
    proof = data.get("regression_proof") or {}
    try:
        digest = hashlib.sha256((root / "totals.py").read_bytes()).hexdigest()
        tests = hashlib.sha256((root / "test_totals.py").read_bytes()).hexdigest()
    except OSError:
        return []
    if (digest, tests) not in TOTALS_INPUTS or not proof.get("path"):
        return []
    mutants = [
        m
        for f in (proof.get("mutation_check") or {}).get("files") or []
        if f.get("path") == "totals.py"
        for m in f.get("mutants") or []
    ]
    surviving = [m for m in mutants if m.get("outcome") == "survived"]
    if (
        not surviving
        or any(m.get("outcome") not in ("killed", "survived") for m in mutants)
        or any(
            (m.get("line"), m.get("operator"), m.get("before"), m.get("after")) != (1, "swap_bound", ">=", ">")
            for m in surviving
        )
    ):
        return []
    return [
        {
            "gap_id": row["gap_id"],
            "decision": "accept",
            "reason_kind": "equivalent_mutant",
            "reason": "The only surviving mutant excludes numeric zero from this numeric sum (v >= 0 becomes v > 0). Zero contributes the additive identity, so the returned total and the keyword-only starting accumulator are unchanged. The tests include negative, positive, fractional, empty and generator inputs; a threshold of 1 is not equivalent and its mutant must fail. No other surviving or incomplete mutation is accepted.",
            "evidence_refs": [proof["path"], "totals.py"],
        }
        for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []
        if row.get("status") == "reason_required"
        and row.get("path") == "totals.py"
        and row.get("sha256") == digest
        and row.get("line") == 1
        and row.get("mutation_outcomes")
        and set(row["mutation_outcomes"]) <= {"killed", "survived"}
    ]


TODO_SOURCE = "cefe6abde79ff40a33e3b0bdb7147edc12af36305a6e1b1c2be244b4d51cd00d"
# The reference tests, and the same tests with the follow-up turn's list test (tests.test_brief_cli).
TODO_TESTS = {
    "55536eb04e1ddccd31e434df0655b40a7064d83587540a210014986dca783ff7",
    "4e7e88535f8ba0fe9fd8da156b765a5e73c05ce089f2a57724ad197670a6a41f",
}
_TODO_IMPORT = (
    "No supported import mutation. Every CLI test runs todo.py as a subprocess; without this import the "
    "module fails before main and test_add_list_complete's exact list output cannot be produced."
)
TODO_REASONS = {
    "import json": _TODO_IMPORT,
    "import os": _TODO_IMPORT,
    "import sys": _TODO_IMPORT,
    "from pathlib import Path": _TODO_IMPORT,
    'STORE = Path("todos.json")': "No supported assignment mutation. test_unknown_id_leaves_store_identical and "
    "test_malformed_store_is_never_rewritten read todos.json in the command's directory; another store name fails them.",
    "class StoreError(Exception):": "A class header has no supported mutation. test_malformed_store_is_never_rewritten "
    "requires exit 1 with the malformed-store message, which main produces only by catching this exception type.",
    "pass": "The empty exception body has no behavior of its own; the class header's reason covers the exception.",
    "try:": "A try header has no supported mutation. The malformed-store tests and the add/list/complete tests pass "
    "only when the guarded load succeeds or its failure becomes exit 1 with an unchanged store.",
    'data = json.loads(STORE.read_text(encoding="utf-8"))': "No supported call mutation. test_add_list_complete "
    "reads back saved to-dos through list, and the '{broken' store must fail to parse; skipping the read fails both.",
    "except (ValueError, AttributeError) as error:": "No supported handler mutation. test_malformed_store_is_never_rewritten "
    "requires exit 1 and the malformed message for unparsable and wrongly shaped stores; without the handler they crash.",
    'raise StoreError(f"todos.json is malformed ({error}); leaving it untouched") from None': "No supported raise "
    "mutation. test_malformed_store_is_never_rewritten asserts 'todos.json is malformed' on stderr for every command.",
    'temporary = STORE.with_suffix(".json.tmp")': "No supported assignment mutation. test_add_list_complete lists the "
    "saved to-do in a later process, so the save path must reach todos.json through this temporary file.",
    'temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")': "No supported call mutation. "
    "test_add_list_complete reads the added and completed to-do from a later process; an unwritten save fails it.",
    "os.replace(temporary, STORE)": "No supported call mutation. test_add_list_complete and "
    "test_unknown_id_leaves_store_identical read todos.json itself; without the replace nothing reaches the store.",
    "data = load()": "No supported assignment mutation. test_add_list_complete lists a to-do added by an earlier "
    "process; without loading the store the later commands see no to-dos and fail.",
    "except StoreError as error:": "No supported handler mutation. test_malformed_store_is_never_rewritten requires "
    "exit 1 with the message instead of a traceback for every command.",
    'print(f"todo: {error}", file=sys.stderr)': "No supported call mutation. test_malformed_store_is_never_rewritten "
    "asserts the malformed-store message on stderr; dropping this print fails it.",
    "command = argv[0]": "No supported assignment mutation. Every command test dispatches on this name; without it "
    "add, list and complete cannot run.",
    'data["next_id"] += 1': "No supported augmented-assignment mutation. test_add_list_complete completes id 1 and the "
    "follow-up list test lists ids 1 and 2; a stale counter repeats ids and fails the exact list output.",
    "save(data)": "No supported call mutation. test_add_list_complete lists the added and then completed to-do from "
    "later processes; without saving, list prints nothing.",
    "print(f\"{todo['id']} {todo['text']} [{'done' if todo['done'] else 'open'}]\")": "No supported call mutation. "
    "test_add_list_complete asserts the exact line '1 buy milk [done]'; dropping the print fails it.",
    'match[0]["done"] = True': "No supported assignment mutation. test_add_list_complete asserts '[done]' after "
    "completing id 1; without the assignment it lists '[open]'.",
    'if __name__ == "__main__":': "The mutator excludes entrypoint guards. Every CLI test runs todo.py as a script "
    "and asserts its output and exit status; disabling the guard fails all of them.",
    "raise SystemExit(main(sys.argv[1:]))": "No supported exit-adapter mutation. The usage, unknown-id and "
    "malformed-store tests assert exits 2 and 1; discarding main's return value exits 0 and fails them.",
}


def _todo(data, root):
    proof = data.get("regression_proof") or {}
    try:
        source = (root / "todo.py").read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        if digest != TODO_SOURCE or hashlib.sha256((root / "test_todo.py").read_bytes()).hexdigest() not in TODO_TESTS:
            return []
    except OSError:
        return []
    if not proof.get("path"):
        return []
    lines, accepted = source.decode().splitlines(), []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        line = row.get("line")
        if (
            row.get("status") != "reason_required"
            or row.get("path") != "todo.py"
            or row.get("sha256") != digest
            or type(line) is not int
            or not 0 < line <= len(lines)
            or row.get("mutation_outcomes")
        ):
            continue
        if reason := TODO_REASONS.get(lines[line - 1].strip()):
            accepted.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], "todo.py"],
                }
            )
    return accepted


DIAMOND_CHECK = "35f1a48f7793f0b6d3f06fa8d39a0907dacd9ee88b9e2daa1a55245b05f36320"
DIAMOND_TESTS = "f041e5eef4e1c7c8ae5516c7957012e17060cb7e20eb0c7cfca79fd40e35b6b4"
DIAMOND_REASONS = {
    "from client.fetch import fetch": "No supported import mutation. test_check_combines_handle_and_fetch asserts "
    "the exact 'handled D / fetched D' result; without this import check() raises NameError and fails it.",
    "from server.handler import handle": "No supported import mutation. test_check_combines_handle_and_fetch asserts "
    "the exact 'handled D / fetched D' result; without this import check() raises NameError and fails it.",
}


def _diamond(data, root):
    proof = data.get("regression_proof") or {}
    try:
        source = (root / "integration/check.py").read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        if (
            digest != DIAMOND_CHECK
            or hashlib.sha256((root / "integration/test_check.py").read_bytes()).hexdigest() != DIAMOND_TESTS
        ):
            return []
    except OSError:
        return []
    if not proof.get("path"):
        return []
    lines, accepted = source.decode().splitlines(), []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        line = row.get("line")
        if (
            row.get("status") != "reason_required"
            or row.get("path") != "integration/check.py"
            or row.get("sha256") != digest
            or type(line) is not int
            or not 0 < line <= len(lines)
            or row.get("mutation_outcomes")
        ):
            continue
        if reason := DIAMOND_REASONS.get(lines[line - 1].strip()):
            accepted.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], "integration/check.py"],
                }
            )
    return accepted


WEEKS_SOURCE = "31fbfcf6402c2bc85a0ff832fba5aa4e8ca76400d1609fc9105b252006d963d3"
WEEKS_TESTS = "13fc59908469b6d9650dcc96fa604956ffb4526975938090b6cea3d3280cfe03"
WEEKS_REASONS = {
    "iso = day.isocalendar()": "No supported assignment mutation for a call result. test_year_boundaries asserts the "
    "exact ISO (year, week) for six days across year boundaries and test_reported_new_year_week_is_one_row asserts "
    "one 2025-W01 row; without this ISO calendar lookup week_key cannot return those pairs and both tests fail.",
}


def _killed_capture(proof, path, digest, line):
    mutation = (proof.get("changed_code_inputs") or {}).get("mutation") or {}
    return [
        ref
        for file in mutation.get("files", [])
        if file.get("path") == path and file.get("sha256") == digest
        for mutant in file.get("mutants", [])
        if mutant.get("outcome") == "killed" and mutant.get("line") == line
        for ref in mutant.get("evidence", [])
    ][:1]


def _weeks(data, root):
    """bugfix-iso-weeks: the reference fix's ISO lookup line has no mutation site; its tests observe it."""
    proof = data.get("regression_proof") or {}
    try:
        source = (root / "timesheet/weeks.py").read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        if (
            digest != WEEKS_SOURCE
            or hashlib.sha256((root / "tests/test_weeks.py").read_bytes()).hexdigest() != WEEKS_TESTS
        ):
            return []
    except OSError:
        return []
    if not proof.get("path"):
        return []
    lines, accepted = source.decode().splitlines(), []
    capture = _killed_capture(proof, "timesheet/weeks.py", digest, lines.index("    return iso.year, iso.week") + 1)
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        line = row.get("line")
        if (
            row.get("status") != "reason_required"
            or row.get("path") != "timesheet/weeks.py"
            or row.get("sha256") != digest
            or type(line) is not int
            or not 0 < line <= len(lines)
            or row.get("mutation_outcomes")
        ):
            continue
        if reason := WEEKS_REASONS.get(lines[line - 1].strip()):
            accepted.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], "timesheet/weeks.py", *capture],
                }
            )
    return accepted


# review-then-fix: the reference fix of pr-184.patch, with its tests (tests/test_client.py, tests/test_policies.py).
REGCLIENT_SOURCES = {
    "regclient/client.py": "3b1711d90eaf93b0c03ccc62dd9ffa8dc8b55ab8cbe8bb57ebc05393b670e715",
    "regclient/policies.py": "88b3caa3a14464c9a97265f21c86b4f354f12fd692b8fbcb88502d128db92bfd",
}
REGCLIENT_TESTS = {
    "tests/test_client.py": "50be50fdb5170c1c801fd655bd2214fdd75790b8e7d1df18de141e109fd98991",
    "tests/test_policies.py": "6c96a819059b633b740351c2e695606ecfc1a82bab74320c7328e229a20a8171",
}
_REGCLIENT_LOOP = (
    "instrumentation_unavailable",
    "A loop header has no supported mutation. test_retryable_errors_are_deduplicated_and_sorted asserts .com and "
    ".net merge TRANSIENT and SESSION into exactly three sorted codes; skipping either loop loses codes and fails it.",
)
# The lines whose mutant survives because no public input reaches them (drop_raise, #2815).
REGCLIENT_SURVIVORS = {"regclient/client.py": ('raise AssertionError("unreachable")',)}
REGCLIENT_REASONS = {
    "regclient/client.py": {
        "import time": (
            "instrumentation_unavailable",
            "No supported import mutation. The default sleep argument is time.sleep; without the import, importing "
            "regclient.client raises NameError and every ClientTests test fails.",
        ),
        "def __init__(self, transport, sleep=time.sleep):": (
            "instrumentation_unavailable",
            "A def header has no supported mutation. Every ClientTests test passes sleep to RegistryClient and "
            "test_backoff_is_applied_between_attempts asserts the injected sleep saw [1.0]; a header without the "
            "parameter fails them with TypeError.",
        ),
        "self.sleep = sleep": (
            "instrumentation_unavailable",
            "No supported assignment mutation (the value is a name, not a literal). "
            "test_backoff_is_applied_between_attempts asserts the injected sleep received [1.0]; without storing it "
            "send() raises AttributeError on the first retry.",
        ),
        "for attempt in range(1, policy.max_attempts + 1):": (
            "instrumentation_unavailable",
            "A loop header has no supported mutation. test_server_busy_is_retried and test_com_retries_session_limit "
            "assert exactly two transport calls and test_de_is_never_retried exactly one; the loop's attempt number "
            "drives the killed attempt == policy.max_attempts conditions on lines 32 and 35.",
        ),
        "self.sleep(policy.backoff_seconds)": (
            "instrumentation_unavailable",
            "No supported call mutation. test_backoff_is_applied_between_attempts asserts the .org retry waits "
            "exactly [1.0]; dropping or changing this call fails it.",
        ),
        'raise AssertionError("unreachable")': (
            "defensive_unreachable",
            "Unreachable from public input: every policy has max_attempts of at least 1, and on the last attempt "
            "the loop body returns or re-raises (the killed conditions on lines 32 and 35), so the loop never "
            "falls through to this line, and its surviving drop_raise mutant changes nothing a test can reach.",
        ),
    },
    "regclient/policies.py": {
        "def retryable(self, code: str) -> bool:": (
            "instrumentation_unavailable",
            "A def header has no supported mutation. Its body's constant_return mutant (line 16) is killed by "
            "test_non_retryable_error_is_raised and test_server_busy_is_retried, which reach it only through "
            "this method, which client.py calls by name.",
        ),
        "merged = []": (
            "instrumentation_unavailable",
            "No mutation site: _errors runs on import (DEFAULT and REGISTRIES call it), so binding None here "
            "would stop the module loading before any test ran. "
            "test_retryable_errors_are_deduplicated_and_sorted asserts the exact merged tuples; any other start "
            "fails it.",
        ),
        "for group in groups:": _REGCLIENT_LOOP,
        "for code in group:": _REGCLIENT_LOOP,
        'TRANSIENT = ("SERVER_BUSY", "RATE_LIMITED")': (
            "instrumentation_unavailable",
            "No mutation site: DEFAULT and REGISTRIES read TRANSIENT on import, so binding None here would stop "
            "the module loading before any test ran. "
            "test_retryable_errors_are_deduplicated_and_sorted asserts DEFAULT's and .com's exact codes, and "
            "test_server_busy_is_retried retries SERVER_BUSY; another tuple fails them.",
        ),
        'SESSION = ("SESSION_LIMIT_EXCEEDED",)': (
            "instrumentation_unavailable",
            "No mutation site: REGISTRIES reads SESSION on import, so binding None here would stop the module "
            "loading before any test ran. test_com_retries_session_limit "
            "retries SESSION_LIMIT_EXCEEDED on .com and test_retryable_errors_are_deduplicated_and_sorted asserts "
            "it among .com's and .net's codes.",
        ),
        "DEFAULT = RetryPolicy(retryable_errors=_errors(TRANSIENT), max_attempts=3)": (
            "instrumentation_unavailable",
            "No supported assignment mutation (a call, not a literal). test_unknown_registry_gets_default asserts "
            "policy_for('xyz') is DEFAULT with no backoff, and test_retryable_errors_are_deduplicated_and_sorted "
            "asserts its exact codes.",
        ),
        "REGISTRIES = {": (
            "instrumentation_unavailable",
            "No supported assignment mutation (a dict display). test_known_registries_have_their_own_policy, "
            "test_de_gets_one_attempt_and_retries_nothing and the .com/.org/.de client tests read their policies "
            "through this table; without it they get DEFAULT and fail.",
        ),
        '"com": RetryPolicy(retryable_errors=_errors(TRANSIENT, SESSION), max_attempts=4, backoff_seconds=0.5),': (
            "instrumentation_unavailable",
            "A dict entry has no supported mutation. test_known_registries_have_their_own_policy asserts 4 attempts, "
            "test_retryable_errors_are_deduplicated_and_sorted the merged codes and test_com_retries_session_limit "
            "the retry; without the entry .com gets DEFAULT and fails them.",
        ),
        '"net": RetryPolicy(retryable_errors=_errors(TRANSIENT, SESSION), max_attempts=4, backoff_seconds=0.5),': (
            "instrumentation_unavailable",
            "A dict entry has no supported mutation. test_known_registries_have_their_own_policy asserts 4 attempts "
            "for NET and test_retryable_errors_are_deduplicated_and_sorted its merged codes; without the entry .net "
            "gets DEFAULT and fails them.",
        ),
        '"org": RetryPolicy(retryable_errors=_errors(TRANSIENT), max_attempts=3, backoff_seconds=1.0),': (
            "instrumentation_unavailable",
            "A dict entry has no supported mutation. test_known_registries_have_their_own_policy and "
            "test_backoff_is_applied_between_attempts assert .org's 1.0 s backoff; without the entry .org gets "
            "DEFAULT's 0.0 and fails them.",
        ),
    },
}


def _regclient(data, root):
    """review-then-fix: the reference fix's changed lines that have no mutation site, with their tests pinned."""
    proof = data.get("regression_proof") or {}
    try:
        sources = {path: (root / path).read_bytes() for path in REGCLIENT_SOURCES}
        if any(hashlib.sha256(sources[path]).hexdigest() != digest for path, digest in REGCLIENT_SOURCES.items()):
            return []
        if any(hashlib.sha256((root / path).read_bytes()).hexdigest() != pin for path, pin in REGCLIENT_TESTS.items()):
            return []
    except OSError:
        return []
    if not proof.get("path"):
        return []
    accepted = []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        path, line = row.get("path"), row.get("line")
        if (
            row.get("status") != "reason_required"
            or path not in REGCLIENT_SOURCES
            or row.get("sha256") != REGCLIENT_SOURCES[path]
            or type(line) is not int
        ):
            continue
        lines = sources[path].decode().splitlines()
        if not 0 < line <= len(lines):
            continue
        text = lines[line - 1].strip()
        # A line with a mutant is judged only when it is the unreachable raise and that mutant survived.
        outcomes = row.get("mutation_outcomes")
        if outcomes and not (text in REGCLIENT_SURVIVORS.get(path, ()) and set(outcomes) == {"survived"}):
            continue
        if judged := REGCLIENT_REASONS[path].get(text):
            accepted.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": judged[0],
                    "reason": judged[1],
                    "evidence_refs": [proof["path"], path],
                }
            )
    return accepted


LEASE_SOURCE = "37086ea9920187aa6f500f5d946c70c33229ebade1d69cb1a655235c2c6304e3"
LEASE_TESTS = "78e4c933baa51d3a0c09167303692e9dacd71439c9769b086163a28f71bd345d"
LEASE_TOKENS_TESTS = "d736b080b6d9cd3095de44622bf20ccd54ac815569f2e55a6fcd875d1772b8ce"
_LEASE_TXN = (
    "No supported mutation for this transaction line. Every test in tests/test_behavior.py enqueues, claims "
    "and acknowledges through _db; test_enqueue_claim_restart_and_ack reads the claim back from a new "
    "LeaseQueue on the same file, so omitting the connection, transaction or commit fails it."
)
LEASE_REASONS = {
    1: "No supported import mutation. Every test constructs LeaseQueue, which opens its store with sqlite3; "
    "without the import construction raises NameError and every test fails.",
    4: "No supported import mutation. claim issues uuid4 tokens; test_expiry_at_the_deadline_fences_the_old_token "
    "claims twice and asserts distinct tokens, and without the import every claim raises NameError.",
    5: "No supported import mutation. _db is decorated with contextmanager; without it the module fails at "
    "import and every test fails.",
    10: "No supported assignment mutation. _db connects to self.path; test_enqueue_claim_restart_and_ack "
    "reopens the same file and acknowledges the earlier claim, which requires this stored path.",
    11: "No supported mutation for a with header. The schema statement runs inside this transaction; without it "
    "the jobs table is never created and every enqueue in the tests fails.",
    12: "No supported call mutation. This statement creates the jobs table every test enqueues into; omitting it "
    "fails every test with no such table.",
    13: "The schema literal has no supported mutation. Every query reads these columns (seq, id, payload, token, "
    "deadline, done); test_enqueue_claim_restart_and_ack and the expiry tests fail on any missing column.",
    16: "A decorator has no supported mutation. Without contextmanager, 'with self._db()' raises because a "
    "generator is not a context manager, and every test fails.",
    18: _LEASE_TXN,
    19: _LEASE_TXN,
    20: "No supported call mutation. BEGIN IMMEDIATE takes the write lock before claim reads its candidate, which "
    "only racing processes observe: the runner's lease_queue_lifecycle_v1 contention phase releases three "
    "interpreters together and requires distinct claims (the broken/non-atomic-claim variant leases one job "
    "twice there).",
    21: _LEASE_TXN,
    22: _LEASE_TXN,
    23: "A handler header has no supported mutation. The conflicting-enqueue ValueError in "
    "test_inputs_are_validated_at_their_boundaries is raised inside a transaction and passes through this "
    "handler; the test asserts it reaches the caller.",
    24: "No supported call mutation. The rollback releases the failed transaction before close; close without "
    "commit also discards it, so the conflicting-enqueue test observes the same unchanged queue either way. "
    "It is an explicit release, kept for clarity, with no separate observable behavior.",
    25: "A bare raise has no supported mutation. test_inputs_are_validated_at_their_boundaries asserts ValueError "
    "for a conflicting payload raised inside the transaction; swallowing it here fails that assertion.",
    27: _LEASE_TXN,
    29: "A decorator has no supported mutation. _time is called as self._time(now); without staticmethod now "
    "binds to self and test_inputs_are_validated_at_their_boundaries' valid claim(0, 1) raises.",
    37: "No supported mutation for a with header. test_enqueue_claim_restart_and_ack enqueues and replays; "
    "without the transaction there is no db to insert into.",
    38: "No supported assignment mutation. test_enqueue_claim_restart_and_ack asserts False for an identical "
    "replay and the boundary test asserts ValueError for a conflicting payload; both need this lookup.",
    43: "No supported call mutation. Every test claims a job it enqueued; without the insert claim returns None "
    "and the claim assertions fail.",
    47: "No supported call mutation for a bare call. test_inputs_are_validated_at_their_boundaries requires "
    "ValueError from claim(-1, 5) and claim(True, 5); omitting this check accepts them.",
    50: "No supported mutation for a with header. Every test's claim reads and leases through this transaction.",
    52: "No supported assignment mutation. Every claim selects from these candidates; test_enqueue_claim_restart_"
    "and_ack and the expiry tests assert the claimed id and payload.",
    56: "No supported assignment mutation. test_expiry_at_the_deadline_fences_the_old_token asserts the reclaim's "
    "token differs from the first; a constant token fails it and lets the old token acknowledge.",
    57: "No supported assignment mutation. test_enqueue_claim_restart_and_ack asserts deadline 15 for claim(10, 5) "
    "and the expiry test asserts 20 for claim(15, 5).",
    58: "No supported call mutation. Without storing the lease, the expiry test's claim(14, 5) is not refused and "
    "acknowledging the claimed token returns False in test_enqueue_claim_restart_and_ack.",
    62: "No supported call mutation for a bare call. test_inputs_are_validated_at_their_boundaries requires "
    "ValueError from ack(..., -1); omitting this check returns False instead.",
    63: "No supported mutation for a with header. Every ack and nack in the tests reads and updates through "
    "this transaction.",
    64: "No supported assignment mutation. ack and nack decide from this row; the tests assert True for the "
    "current lease and False for a wrong, expired or released token.",
    67: "No supported call mutation. test_enqueue_claim_restart_and_ack asserts pending() is 0 after ack and "
    "test_nack_releases_for_immediate_retry reclaims after nack; without the update neither holds.",
    77: "No supported mutation for a with header. The tests assert pending() counts through this transaction.",
}
# broken/process-local-tokens ships the same tests; only its token source differs, and the runner's
# restart observation (not these tests) is what refuses it. From the class on its lines sit two below.
LEASE_TOKENS_SOURCE = "bbe869af30ceffeace071b921ccd0a4fc1edf4d77e940a647f206109287ea679"
LEASE_TOKENS_REASONS = {
    1: LEASE_REASONS[1],
    2: LEASE_REASONS[5],
    5: "No supported import mutation. claim draws tokens from count; without the import the module fails at "
    "import and every test fails.",
    7: "No supported assignment mutation. claim draws every token from this counter; "
    "test_expiry_at_the_deadline_fences_the_old_token asserts the reclaim's token differs from the first.",
    **{line + 2: reason for line, reason in LEASE_REASONS.items() if line >= 10},
}


OUTBOX_SOURCE = "edc2e9466110c1f311254eab65c61b59aad12a324e07577e6be4689e115109e2"
OUTBOX_TESTS = "bf0b21a820bdeb356f04d5f1ceb955ad8bffeae04aa7ca33c5185815dd862d6f"
_OUTBOX_TXN = (
    "No supported mutation for this transaction line. Every test in tests/test_behavior.py creates, reads and "
    "publishes through _db; test_orders_publish_and_restart and test_sink_failure_retries_same_durable_event "
    "reopen the same file and assert the committed orders and events, so omitting the connection, transaction "
    "or commit fails them."
)
_OUTBOX_SCHEMA = (
    "No supported call mutation for the schema. Every test creates an order and reads orders() or pending() "
    "from these tables and columns; omitting or changing the statement fails them."
)
OUTBOX_REASONS = {
    1: "No supported import mutation. Every test constructs Store, which opens its file with sqlite3; without "
    "the import construction raises NameError and every test fails.",
    4: "No supported import mutation. create_order generates the event_id with uuid4; without the import every "
    "new order raises NameError and every test fails.",
    5: "No supported import mutation. _db is decorated with contextmanager; without it the module fails at "
    "import and every test fails.",
    10: "No supported assignment mutation. _db connects to self.path; test_orders_publish_and_restart reopens the "
    "same file and asserts the saved order, which requires this stored path.",
    11: "No supported mutation for a with header. The schema statements run inside this transaction; without it "
    "no table exists and every test fails.",
    12: _OUTBOX_SCHEMA,
    13: _OUTBOX_SCHEMA,
    14: _OUTBOX_SCHEMA,
    17: "A decorator has no supported mutation. Without contextmanager, 'with self._db()' raises because a "
    "generator is not a context manager, and every test fails.",
    19: _OUTBOX_TXN,
    20: _OUTBOX_TXN,
    21: "No supported call mutation. BEGIN IMMEDIATE takes the write lock before create_order reads the key, which "
    "only racing processes observe: the runner's transactional_outbox_lifecycle_v1 contention phase creates "
    "the same key from three interpreters together and requires one commit (the broken/racy-create-order "
    "variant commits twice there).",
    22: _OUTBOX_TXN,
    23: _OUTBOX_TXN,
    24: "A handler header has no supported mutation. The idempotency-conflict ValueError in "
    "test_inputs_and_conflicts_are_refused_without_effect is raised inside a transaction and passes through "
    "this handler; the test asserts it reaches the caller and that orders() is unchanged.",
    25: "No supported call mutation. The rollback releases the failed transaction before close; close without "
    "commit also discards it, so the conflict test observes the same unchanged orders either way. It is an "
    "explicit release, kept for clarity, with no separate observable behavior.",
    26: "A bare raise has no supported mutation. test_inputs_and_conflicts_are_refused_without_effect asserts "
    "ValueError for a reused key raised inside the transaction; swallowing it here fails that assertion.",
    28: _OUTBOX_TXN,
    33: "A try header has no supported mutation. test_inputs_and_conflicts_are_refused_without_effect requires "
    "ValueError for an existing order under another key, which only the IntegrityError handler it guards "
    "produces.",
    34: "No supported mutation for a with header. Every test creates its orders through this transaction.",
    35: "No supported assignment mutation. test_orders_publish_and_restart asserts False for an identical replay "
    "and the conflict test asserts ValueError for a reused key; both need this lookup.",
    40: "No supported call mutation. test_orders_publish_and_restart asserts orders() is {'a': 9} after reopening; "
    "without the insert it is empty.",
    41: "No supported call mutation. test_orders_publish_and_restart publishes exactly one event for the new "
    "order; without this insert publish returns 0.",
    42: "The event insert's statement literal has no supported mutation; the publish tests read these columns "
    "back through pending() and publish().",
    43: "The event insert's parameters have no supported mutation. The publish tests assert the delivered "
    "order_id, and test_inputs_and_conflicts_are_refused_without_effect asserts the pending amount 1.",
    46: "A handler header has no supported mutation. test_inputs_and_conflicts_are_refused_without_effect "
    "requires ValueError for an existing order under another key, which sqlite raises as IntegrityError.",
    47: "A raise with a cause has no supported mutation. test_inputs_and_conflicts_are_refused_without_effect "
    "requires ValueError, not IntegrityError, for an existing order under another key.",
    50: "No supported mutation for a with header. test_orders_publish_and_restart reads orders() through this "
    "transaction.",
    53: "A header with a default has no supported mutation. test_orders_publish_and_restart calls pending() with "
    "the default limit and the conflict test passes limit 1; both assert the returned events.",
    56: "No supported mutation for a with header. Every pending() and publish() in the tests reads through this "
    "transaction.",
    58: "No supported assignment mutation. The clamp only matters above SQLite's integer range, which the hidden "
    "contract checks; every public test passes a smaller limit and reads the events this query returns.",
    60: "No supported call mutation. test_sink_failure_retries_same_durable_event compares pending() with the "
    "delivered event dicts, and the conflict test asserts order_id and amount; changing the shape fails them.",
    61: "The query call has no supported mutation. Every pending() and publish() assertion reads these rows.",
    62: "The query literal has no supported mutation. test_orders_publish_and_restart asserts pending() is empty "
    "after publish, which requires the published=0 filter, and the retry test requires the pending event.",
    66: "A header with a default has no supported mutation. The publish tests call publish(sink) with the default "
    "limit and assert the returned count.",
    68: "A for header has no supported mutation. test_orders_publish_and_restart asserts publish delivers the "
    "pending event and returns 1.",
    69: "No supported call mutation. test_orders_publish_and_restart asserts the sink received order 'a'; "
    "test_sink_failure_retries_same_durable_event asserts the sink's error propagates.",
    70: "No supported mutation for a with header. The acknowledgement update runs in this transaction; "
    "test_orders_publish_and_restart asserts pending() is empty after publishing.",
    71: "No supported call mutation. test_orders_publish_and_restart asserts pending() is empty after publish; "
    "without the acknowledgement the event stays pending.",
    72: "No supported augmented-assignment mutation. The publish tests assert publish returns 1 for one "
    "delivered event; without the increment it returns 0.",
}


def _outbox_cleanup_equivalent(proof, row, digest):
    """Only these two exact mutants leave SQLite's mandatory close rollback intact."""
    signatures = {
        24: ("never_catch", "BaseException", "()"),
        25: ("drop_call", "db.rollback()", "None"),
    }
    expected = signatures.get(row.get("line"))
    if digest != OUTBOX_SOURCE or expected is None or row.get("mutation_outcomes") != ["survived"]:
        return False
    files = [
        file
        for file in (proof.get("mutation_check") or {}).get("files") or []
        if file.get("path") == "outbox/__init__.py"
    ]
    if len(files) != 1 or files[0].get("sha256") != digest:
        return False
    mutants = [mutant for mutant in files[0].get("mutants") or [] if mutant.get("line") == row["line"]]
    if len(mutants) != 1:
        return False
    mutant = mutants[0]
    return (
        (mutant.get("operator"), mutant.get("before"), mutant.get("after")) == expected
        and mutant.get("source_sha256") == digest
        and mutant.get("outcome") == "survived"
        and mutant.get("failed_tests") == []
        and all(
            isinstance(values, list) and bool(values) and all(isinstance(value, str) and value for value in values)
            for values in (mutant.get("survived_tests"), mutant.get("evidence"))
        )
    )


def _lifecycle(data, root, source_path, sources, tests_digest):
    """The ladder lifecycle fixtures: no-mutation-site lines of an exact scripted source and its tests."""
    proof = data.get("regression_proof") or {}
    try:
        digest = hashlib.sha256((root / source_path).read_bytes()).hexdigest()
        reasons = sources.get(digest)
        if reasons is None:
            return []
        if hashlib.sha256((root / "tests/test_behavior.py").read_bytes()).hexdigest() != tests_digest:
            return []
    except OSError:
        return []
    if not proof.get("path"):
        return []
    accepted = []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        if row.get("status") != "reason_required" or row.get("path") != source_path or row.get("sha256") != digest:
            continue
        equivalent = source_path == "outbox/__init__.py" and _outbox_cleanup_equivalent(proof, row, digest)
        if row.get("mutation_outcomes") and not equivalent:
            continue
        reason = (
            "Exact scripted outbox: this mutant only removes the explicit rollback on exception. "
            "The exception still propagates through the unchanged finally block, whose db.close() "
            "rolls back the pending SQLite transaction before releasing the connection. The conflict "
            "and failed-sink tests observe unchanged persisted orders/events, and the connection test "
            "proves that close still occurs. No transaction-start or connection-close mutant is equivalent."
            if equivalent
            else reasons.get(row.get("line"))
        )
        if reason:
            accepted.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": "equivalent_mutant" if equivalent else "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], source_path],
                }
            )
    return accepted


LEASE_TRANSACTION_CASE = "tests.test_behavior.QueueTests.test_claim_locks_before_reading_jobs_and_closes_its_connection"


def _lease_cleanup(data, root, producer):
    """The configured scripted producer and current proof own these exact two reasons."""
    path = "leasequeue/__init__.py"
    pins = {path: LEASE_SOURCE, "tests/test_behavior.py": LEASE_TESTS}
    if (
        not isinstance(producer, dict)
        or producer.get("check") != "python3 -m unittest discover -s tests -t ."
        or producer.get("turn_paths")
        or not isinstance(producer.get("reference"), str)
        or not isinstance(producer.get("paths"), list)
        or not all(isinstance(name, str) for name in producer["paths"])
        or not set(pins) <= set(producer["paths"])
    ):
        return []
    try:
        reference = Path(producer["reference"])
        if reference.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            return []
        if not _fixture_pins(root, pins) or not _fixture_pins(reference, pins):
            return []
    except (OSError, RuntimeError):
        return []
    proof = data.get("regression_proof") or {}
    assessment = proof.get("changed_code_evidence") or {}
    revision, context = assessment.get("source_revision"), assessment.get("proof_context")
    if (
        proof.get("verdict") != "PASS"
        or proof.get("source_revision") != revision
        or not all(isinstance(value, str) and value for value in (proof.get("path"), revision, context))
        or LEASE_TRANSACTION_CASE not in (proof.get("fail_to_pass") or [])
    ):
        return []
    accepted = []
    signatures = {23: ("never_catch", "BaseException", "()"), 24: ("drop_call", "db.rollback()", "None")}
    for row in assessment.get("obligations") or []:
        line = row.get("line")
        identity = [revision, context, path, LEASE_SOURCE, line]
        gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        if (
            type(line) is not int
            or row.get("status") != "reason_required"
            or row.get("path") != path
            or row.get("sha256") != LEASE_SOURCE
            or row.get("gap_id") != gap
            or not _cleanup_equivalent(proof, row, LEASE_SOURCE, path, signatures)
        ):
            continue
        mutants = next(file["mutants"] for file in proof["mutation_check"]["files"] if file["path"] == path)
        mutant = next(mutant for mutant in mutants if mutant.get("line") == line)
        if LEASE_TRANSACTION_CASE not in mutant["survived_tests"]:
            continue
        accepted.append(
            {
                "gap_id": gap,
                "decision": "accept",
                "reason_kind": "equivalent_mutant",
                "reason": "Exact scripted lease queue: removing this explicit exception rollback leaves "
                "the exception propagating through the unchanged finally block. db.close() rolls back "
                "its pending SQLite transaction and releases the connection. The delivered transaction "
                "test passes on this exact mutant and detects omitted transaction start or close; only "
                "these two redundant-cleanup mutants are equivalent for this pinned fixture.",
                "evidence_refs": [proof["path"], path],
            }
        )
    return accepted


def _leasequeue(data, root, producer):
    path = "leasequeue/__init__.py"
    return (
        _lifecycle(data, root, path, {LEASE_SOURCE: LEASE_REASONS}, LEASE_TESTS)
        + _lifecycle(data, root, path, {LEASE_TOKENS_SOURCE: LEASE_TOKENS_REASONS}, LEASE_TOKENS_TESTS)
        + _lease_cleanup(data, root, producer)
    )


def _outbox(data, root):
    return _lifecycle(data, root, "outbox/__init__.py", {OUTBOX_SOURCE: OUTBOX_REASONS}, OUTBOX_TESTS)


def _cleanup_equivalent(proof, row, digest, source_path, signatures):
    """Match one observed exact cleanup mutant, never an arbitrary survivor."""
    expected = signatures.get(row.get("line"))
    if expected is None or row.get("mutation_outcomes") != ["survived"]:
        return False
    files = [file for file in (proof.get("mutation_check") or {}).get("files") or [] if file.get("path") == source_path]
    if len(files) != 1 or files[0].get("sha256") != digest:
        return False
    mutants = [mutant for mutant in files[0].get("mutants") or [] if mutant.get("line") == row["line"]]
    if len(mutants) != 1:
        return False
    mutant = mutants[0]
    return (
        (mutant.get("operator"), mutant.get("before"), mutant.get("after")) == expected
        and mutant.get("source_sha256") == digest
        and mutant.get("outcome") == "survived"
        and mutant.get("failed_tests") == []
        and all(
            isinstance(values, list) and bool(values) and all(isinstance(value, str) and value for value in values)
            for values in (mutant.get("survived_tests"), mutant.get("evidence"))
        )
    )


PACKAGE_GREETING_SOURCE = "packages/greet/greet.py"
PACKAGE_GREETING_FORMAL_TESTS = "packages/greet/test_formal.py"
PACKAGE_GREETING_PINS = {
    PACKAGE_GREETING_SOURCE: "a7089633b93f8f06dbbf60cbdca7f1353ee31c4ef39f22b0c5a356340bb1f65c",
    PACKAGE_GREETING_FORMAL_TESTS: "bac738a28d46b0f6ebe37fd98e54b394264683b1f4e093b968960ea101e5b4bd",
    "packages/greet/test_greet.py": "b85001e0ea295ae59bd645e1eca0f268ac3e7ef64327530bd651fcf950dac2e8",
}


def _package_greeting(data, root, producer):
    """The configured reference-copy Builder owns this exact signature and tests."""
    if (
        not isinstance(producer, dict)
        or not isinstance(producer.get("reference"), str)
        or producer.get("check") != "cd packages/greet && python3 -m unittest discover -v"
        or producer.get("turn_paths")
        or not isinstance(producer.get("paths"), list)
        or not all(isinstance(path, str) for path in producer["paths"])
        or not {PACKAGE_GREETING_SOURCE, PACKAGE_GREETING_FORMAL_TESTS} <= set(producer["paths"])
    ):
        return []
    try:
        reference = Path(producer["reference"]).resolve()
        if reference.is_relative_to(root.resolve()):
            return []  # Workspace bytes cannot claim to be the Builder's producer.
        for name in (PACKAGE_GREETING_SOURCE, PACKAGE_GREETING_FORMAL_TESTS):
            path = reference / name
            if (
                path.is_symlink()
                or not path.resolve(strict=True).is_relative_to(reference)
                or hashlib.sha256(path.read_bytes()).hexdigest() != PACKAGE_GREETING_PINS[name]
            ):
                return []
        workspace = root.resolve(strict=True)
        for name, pin in PACKAGE_GREETING_PINS.items():
            path = root / name
            if (
                path.is_symlink()
                or not path.resolve(strict=True).is_relative_to(workspace)
                or hashlib.sha256(path.read_bytes()).hexdigest() != pin
            ):
                return []
    except (OSError, RuntimeError):
        return []
    proof = data.get("regression_proof") or {}
    assessment = proof.get("changed_code_evidence") or {}
    source_revision, proof_context = assessment.get("source_revision"), assessment.get("proof_context")
    if (
        not isinstance(proof.get("path"), str)
        or not proof["path"].strip()
        or not all(isinstance(value, str) and value for value in (source_revision, proof_context))
    ):
        return []
    pin = PACKAGE_GREETING_PINS[PACKAGE_GREETING_SOURCE]
    identity = [source_revision, proof_context, PACKAGE_GREETING_SOURCE, pin, 8]
    gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    rows = [
        row
        for row in assessment.get("obligations") or []
        if isinstance(row, dict)
        and row.get("status") == "reason_required"
        and row.get("path") == PACKAGE_GREETING_SOURCE
        and row.get("sha256") == pin
        and type(row.get("line")) is int
        and row["line"] == 8
        and row.get("gap_id") == gap
        and row.get("mutation_outcomes") == []
    ]
    if len(rows) != 1:
        return []
    return [
        {
            "gap_id": gap,
            "decision": "accept",
            "reason_kind": "instrumentation_unavailable",
            "reason": "This exact scripted function signature has no supported mutation site. "
            "TestFormal.test_formal_greeting asserts both the omitted formal=False default returning Hello, Ada "
            "and explicit formal=True returning Good day, Ada.; the CLI flag-position tests assert both argv forms.",
            "evidence_refs": [proof["path"], PACKAGE_GREETING_SOURCE],
        }
    ]


# Only these scripted native fixtures have independently reviewed site-less lines.
# The first-Go CLI is bound to its delivered, executed stream/exit tests too.
NATIVE_FIXTURES = {
    "Add stable numeric score sorting with native Node regression proof": {
        "check": "node --test tests/*.test.js",
        "tests": ["tests/score-order.test.js"],
        "pins": {
            "src/records.js": "f3466e584bfbff36d10e80077c97d1a5cbbb55515c66f85ac8999394bad41148",
            "tests/score-order.test.js": "fd0b0b34289fcdcd82350ae054723fcfa108c48a30a22a5854f09628b46dc2aa",
            "tests/records.test.js": "9a6dcf1ba6def7521f92e9e9d18c31de55caede1ff3829a2de31afd166e93543",
        },
        "produced": ["src/records.js", "tests/score-order.test.js"],
        "cases": {
            "C1": ["tests/score-order.test.js::test_c1_numeric_score_order_in_both_directions"],
            "C2": ["tests/score-order.test.js::test_c2_stable_ties_and_caller_data_preserved"],
        },
        "fail_to_pass": [
            "tests/score-order.test.js::test_c1_numeric_score_order_in_both_directions",
            "tests/score-order.test.js::test_c2_stable_ties_and_caller_data_preserved",
        ],
        "pass_to_pass": [],
        "reasons": {
            "src/records.js": {
                7: "Native Node mutation is unavailable. test_c1_numeric_score_order_in_both_directions calls sortByScore without a direction and asserts ascending numeric order, then explicitly requests descending order; changing this exported signature or default fails those assertions.",
                8: "Native Node mutation is unavailable. test_c1_numeric_score_order_in_both_directions accepts asc/default and desc but requires sideways to throw RangeError; removing or reversing this direction guard fails a delivered assertion.",
                9: "Native Node mutation is unavailable. test_c1_numeric_score_order_in_both_directions asserts the invalid direction throws RangeError; returning normally or throwing a different error class fails the delivered assertion.",
                10: "Native Node mutation is unavailable for this structural guard-closing line. test_c1_numeric_score_order_in_both_directions requires both valid directions to sort and the invalid direction to throw; this delimiter closes that tested guard and has no independent runtime behavior.",
                11: "Native Node mutation is unavailable. test_c1_numeric_score_order_in_both_directions requires null and an object instead of an array to throw TypeError, while valid arrays sort; weakening this array guard fails those delivered assertions.",
                12: "Native Node mutation is unavailable. test_c1_numeric_score_order_in_both_directions rejects a null row, numeric id, string score and infinite score with TypeError; weakening these record-validation predicates fails a delivered assertion.",
                13: "Native Node mutation is unavailable. test_c1_numeric_score_order_in_both_directions requires each invalid record class to throw TypeError; omitting this throw or changing its error class fails the delivered assertions.",
                14: "Native Node mutation is unavailable for this structural validation-closing line. test_c1_numeric_score_order_in_both_directions requires invalid records to throw while valid arrays reach sorting; this delimiter has no independent runtime behavior.",
                15: "Native Node mutation is unavailable. test_c1_numeric_score_order_in_both_directions asserts opposite exact numeric orders for default asc and desc, including negative and fractional scores; changing either sign fails a delivered assertion.",
                16: "Native Node mutation is unavailable. test_c2_stable_ties_and_caller_data_preserved sorts frozen input, asserts original object identities and unchanged caller order, and requires an empty result to be a distinct array; sorting the input in place or discarding its rows fails those assertions.",
                17: "Native Node mutation is unavailable. test_c1_numeric_score_order_in_both_directions rejects lexical score ordering and test_c2_stable_ties_and_caller_data_preserved requires all three equal-score rows to retain their original order in both directions and their original object identities.",
                18: "Native Node mutation is unavailable for this structural function-closing line. test_c1_numeric_score_order_in_both_directions imports and invokes the completed function; this delimiter has no independent runtime behavior.",
                20: "Native Node mutation is unavailable for this CommonJS export line. test_c1_numeric_score_order_in_both_directions invokes exported sortByScore and the delivered existing identifier-order tests invoke exported byId; omitting either export fails those native tests.",
            }
        },
    },
    "Add exact per-account ledger subtotals in TypeScript": {
        "check": "node node_modules/vitest/vitest.mjs run",
        "tests": ["tests/regression.test.ts"],
        "pins": {
            "src/ledger.ts": "863100454c91b22db2903ce240b573144b5aa126483ce1373848bf06a2dc9fa8",
            "tests/regression.test.ts": "819e9abac9565797218206ef25670b93f2d86ee7d1de0600300df11eb9fd5cdd",
            "tests/ledger.test.ts": "1f0d3e06d90a8b65634c5d0d742bcf45491431ef9cbb4813ecca215d5d71d02f",
            "vitest.config.cjs": "d3c7294fa729311fb6b098ce6827a7815360e82277e495e9d31c2d600c04ebd3",
        },
        "produced": ["src/ledger.ts", "tests/regression.test.ts"],
        "cases": {
            "C1": ["tests/regression.test.ts::test_c1_account_groups_keep_exact_keys_and_zero_subtotals"],
            "C2": ["tests/regression.test.ts::test_c2_large_cents_empty_inputs_and_input_order_are_preserved"],
        },
        "fail_to_pass": [
            "tests/regression.test.ts::test_c1_account_groups_keep_exact_keys_and_zero_subtotals",
            "tests/regression.test.ts::test_c2_large_cents_empty_inputs_and_input_order_are_preserved",
        ],
        "pass_to_pass": [],
        "reasons": {
            "src/ledger.ts": {
                10: "Native Vitest mutation is unavailable for this type-only AccountSummary interface declaration. TypeScript erases it before execution; test_c1_account_groups_keep_exact_keys_and_zero_subtotals asserts the returned record shapes, but the declaration itself has no runtime behavior.",
                11: "Native Vitest mutation is unavailable for this type-only account field declaration. TypeScript erases it before execution; test_c1_account_groups_keep_exact_keys_and_zero_subtotals asserts exact returned account strings, but this declaration itself has no runtime behavior.",
                12: "Native Vitest mutation is unavailable for this type-only cents field declaration. TypeScript erases it before execution; test_c2_large_cents_empty_inputs_and_input_order_are_preserved asserts exact bigint subtotals, but this declaration itself has no runtime behavior.",
                13: "Native Vitest mutation is unavailable for this type-only count field declaration. TypeScript erases it before execution; test_c1_account_groups_keep_exact_keys_and_zero_subtotals asserts exact counts, but this declaration itself has no runtime behavior.",
                14: "Native Vitest mutation is unavailable for this type-only interface-closing line. TypeScript erases the interface before execution; test_c1_account_groups_keep_exact_keys_and_zero_subtotals asserts the returned shape, but this delimiter has no runtime behavior.",
                16: "Native Vitest mutation is unavailable. test_c1_account_groups_keep_exact_keys_and_zero_subtotals imports and invokes summarizeByAccount and test_c2_large_cents_empty_inputs_and_input_order_are_preserved requires an empty input to return []; changing or omitting this callable entry fails the delivered assertions.",
                17: "Native Vitest mutation is unavailable. test_c1_account_groups_keep_exact_keys_and_zero_subtotals requires separate Cash, cash and __proto__ groups with exact counts and sums; replacing this fresh Map with unsafe key storage or shared accumulated state breaks those delivered results.",
                18: "Native Vitest mutation is unavailable. test_c1_account_groups_keep_exact_keys_and_zero_subtotals requires all four entries to contribute to their groups; test_c2_large_cents_empty_inputs_and_input_order_are_preserved asserts the empty-input and frozen-input results. Skipping entries or modifying caller data fails those assertions.",
                19: "Native Vitest mutation is unavailable. test_c1_account_groups_keep_exact_keys_and_zero_subtotals requires repeated cash entries to combine while Cash and __proto__ stay separate; changing the exact account lookup key fails the delivered grouped result.",
                20: "Native Vitest mutation is unavailable. test_c1_account_groups_keep_exact_keys_and_zero_subtotals requires both first-entry initialization and repeated-account accumulation, with cash count 2 and total 0n; reversing or omitting this existing-group branch fails the delivered result.",
                21: "Native Vitest mutation is unavailable. test_c1_account_groups_keep_exact_keys_and_zero_subtotals requires 250n plus -250n to remain as a zero subtotal, and test_c2_large_cents_empty_inputs_and_input_order_are_preserved requires 9007199254740993n plus 2n to stay exact; dropping or rounding the sum fails those assertions.",
                22: "Native Vitest mutation is unavailable. test_c1_account_groups_keep_exact_keys_and_zero_subtotals requires cash count 2 while single-entry accounts have count 1; dropping or changing this increment fails the delivered grouped result.",
                23: "Native Vitest mutation is unavailable for this structural else boundary. test_c1_account_groups_keep_exact_keys_and_zero_subtotals requires single-entry initialization and repeated-entry accumulation to take distinct branches; the delimiter has no independent runtime behavior.",
                24: "Native Vitest mutation is unavailable. test_c1_account_groups_keep_exact_keys_and_zero_subtotals asserts exact account, cents and count 1 for new Cash and __proto__ groups; changing this key or any initial field fails the delivered grouped result.",
                25: "Native Vitest mutation is unavailable for this structural group-branch closing line. test_c1_account_groups_keep_exact_keys_and_zero_subtotals exercises both new and repeated accounts; this delimiter has no independent runtime behavior.",
                26: "Native Vitest mutation is unavailable for this structural loop-closing line. test_c1_account_groups_keep_exact_keys_and_zero_subtotals asserts all groups after all four entries, including a zero subtotal; this delimiter has no independent runtime behavior.",
                27: "Native Vitest mutation is unavailable. test_c1_account_groups_keep_exact_keys_and_zero_subtotals requires exact-key sorted output [Cash, __proto__, cash] and test_c2_large_cents_empty_inputs_and_input_order_are_preserved requires [a, z] without changing caller order. Omitting groups or changing lexical ordering fails those delivered assertions.",
                28: "Native Vitest mutation is unavailable for this structural function-closing line. test_c1_account_groups_keep_exact_keys_and_zero_subtotals invokes the exported completed function and asserts its full grouped result; this delimiter has no independent runtime behavior.",
            }
        },
    },
    "Fix touching and empty half-open intervals with native Node regressions": {
        "check": "node --test tests/*.test.js",
        "tests": ["tests/interval-boundaries.test.js"],
        "pins": {
            "src/interval.js": "31cdee3a5632cf06976477c446ae01a748dcf7e4cb43c6a171a5c8d25e5f41f7",
            "tests/interval-boundaries.test.js": "db1e5e3b50d6a534dda7be348207e461d8e439fb97723e1555ac1df6884dcd0b",
            "tests/interval.test.js": "7009236ae16ba5ce3d04e912477cdbe193604b8387c0b8144a81f8102e0326dc",
        },
        "produced": ["src/interval.js", "tests/interval-boundaries.test.js"],
        "cases": {
            "C1": ["tests/interval-boundaries.test.js::test_c1_touching_intervals_are_disjoint"],
            "C2": ["tests/interval-boundaries.test.js::test_c2_empty_interval_never_overlaps"],
        },
        "fail_to_pass": [
            "tests/interval-boundaries.test.js::test_c1_touching_intervals_are_disjoint",
            "tests/interval-boundaries.test.js::test_c2_empty_interval_never_overlaps",
        ],
        "pass_to_pass": [],
        "reasons": {
            "src/interval.js": {
                13: "Native Node mutation is unavailable. test_c2_empty_interval_never_overlaps asserts false for an empty interval on either side and for two empty intervals; test_c1_touching_intervals_are_disjoint also requires a genuine overlap to stay true. Removing either nonempty guard or returning a constant fails these delivered assertions.",
                14: "Native Node mutation is unavailable. test_c1_touching_intervals_are_disjoint asserts touching endpoints are disjoint in both orders for integer, negative and fractional inputs, and that a small real overlap is true. Replacing either strict overlap bound with an inclusive bound or a constant fails these delivered assertions.",
            }
        },
    },
    "Fix touching and empty booking intervals in a TypeScript module": {
        "check": "node node_modules/vitest/vitest.mjs run",
        "tests": ["tests/regression.test.ts"],
        "pins": {
            "src/bookings.ts": "d1b0d87270cff90f76cdc40f5c5c40dfa562234a7509e730a442cadcc35f6e9b",
            "tests/regression.test.ts": "a4398fd1c4ee0fd9590e5820bdadc6f8a9f3fb59fcb7c282d37560f546c377b1",
            "tests/bookings.test.ts": "9557db0db35190c744430be2bfdfa60095e0dd758c4dd061b1052c5a7c85cf19",
            "vitest.config.cjs": "d3c7294fa729311fb6b098ce6827a7815360e82277e495e9d31c2d600c04ebd3",
        },
        "produced": ["src/bookings.ts", "tests/regression.test.ts"],
        "cases": {
            "C1": ["tests/regression.test.ts::test_c1_touching_bookings_do_not_conflict"],
            "C2": ["tests/regression.test.ts::test_c2_empty_intervals_do_not_conflict_or_mutate_inputs"],
        },
        "fail_to_pass": [
            "tests/regression.test.ts::test_c1_touching_bookings_do_not_conflict",
            "tests/regression.test.ts::test_c2_empty_intervals_do_not_conflict_or_mutate_inputs",
        ],
        "pass_to_pass": [],
        "reasons": {
            "src/bookings.ts": {
                19: "Native Vitest mutation is unavailable. test_c2_empty_intervals_do_not_conflict_or_mutate_inputs asserts an empty request returns [] even inside a positive booking, while a nonempty request returns exactly [positive]. Removing this early empty-request guard reports the positive booking and fails the delivered assertion.",
                21: "Native Vitest mutation is unavailable. test_c2_empty_intervals_do_not_conflict_or_mutate_inputs requires an empty booking to be excluded without mutating frozen inputs; test_c1_touching_bookings_do_not_conflict requires exact inside and fractional-overlap IDs while excluding both touching neighbors. Removing the positive-width guard or relaxing either strict bound fails these delivered assertions.",
            }
        },
    },
    "Fix the exclusive upper endpoint of a Go interval": {
        "check": "go test ./...",
        "tests": ["window/regression_test.go"],
        "pins": {
            "window/interval.go": "a1a33ed1eb52e31ebf27af9c336185c5477b6a02f391b1ae07d6eac046fbfa3b",
            "window/regression_test.go": "51a1e1917c6cd23e24c77ea37d9ab3e2560789a683b9e4436181b485b8779697",
            "window/interval_test.go": "553e6649b3c6b6c58274c6a94a627522945b8857cc4fd1712a4a572e5095010d",
        },
        "produced": ["window/interval.go", "window/regression_test.go"],
        "cases": {
            "C1": ["example.test/scenario/window::TestC1ExcludesUpperEndpoint"],
            "C2": ["example.test/scenario/window::TestC2EmptyInterval"],
        },
        "fail_to_pass": [
            "example.test/scenario/window::TestC1ExcludesUpperEndpoint",
            "example.test/scenario/window::TestC2EmptyInterval",
        ],
        "pass_to_pass": [],
        "reasons": {
            "window/interval.go": {
                5: "Native Go mutation is unavailable. TestC1ExcludesUpperEndpoint rejects the upper endpoint and TestC2EmptyInterval rejects [7,7); the existing TestExistingInterior and TestExistingOutside retain true interior membership and false outside membership. An inclusive upper bound or constant result fails those delivered assertions."
            }
        },
    },
    "Count partial Java pages with real JUnit XML named proof": {
        "check": "mvn -o -B -q -Dtest=PageCountTest test",
        "tests": ["src/test/java/example/PageCountTest.java"],
        "pins": {
            "src/main/java/example/PageCount.java": "04879cef46ca696d823c095452b5fa962c2b28625b4ba3a2da79551fe7772e85",
            "src/test/java/example/PageCountTest.java": "501830cf7bde1d6ecd1d64e78e1b223ab6f7593280e9d6c5661ab5a5bf43bb2e",
        },
        "produced": ["src/main/java/example/PageCount.java", "src/test/java/example/PageCountTest.java"],
        "cases": {
            "C1": ["example.PageCountTest::test_c1_partial_page_is_counted"],
            "C2": ["example.PageCountTest::test_c2_exact_multiple_remains_two_pages"],
        },
        "fail_to_pass": ["example.PageCountTest::test_c1_partial_page_is_counted"],
        "pass_to_pass": ["example.PageCountTest::test_c2_exact_multiple_remains_two_pages"],
        "reasons": {
            "src/main/java/example/PageCount.java": {
                10: "Native JUnit mutation is unavailable. test_c1_partial_page_is_counted requires 21 items at page size 10 to yield 3 and test_c2_exact_multiple_remains_two_pages preserves 20 items yielding 2. Dropping the partial-page increment or always adding it fails one of these delivered assertions."
            }
        },
    },
    "Add a first Go package and CLI beside an existing C# policy": {
        "check": "go test ./...",
        "tests": ["cmd/retention/main_test.go", "policy/policy_test.go"],
        "pins": {
            "cmd/retention/main.go": "fe398360138742194cc2848c8e6d2e462e8eda8d8da43cd8d0cfd1c3c8ce2361",
            "policy/policy.go": "0a7fbf004ff2c10eda94790e6d274b73e28ba713055969edfbad541e5cb00ddc",
            "cmd/retention/main_test.go": "df1ddcef14676dcdf63dddd628136ab7bd511966e19aeee3e6ed7e61c02b87ad",
            "policy/policy_test.go": "1e057845778f31453d0ad34826d99cccbf87f1947c67cc1092a2b5296d721f23",
        },
        "produced": [
            "cmd/retention/main.go",
            "policy/policy.go",
            "cmd/retention/main_test.go",
            "policy/policy_test.go",
        ],
        "cases": {
            "C1": ["example.test/scenario/policy::TestC1GermanRetention"],
            "C2": ["example.test/scenario/policy::TestC2ExceptionRetention"],
        },
        "fail_to_pass": [
            "example.test/scenario/policy::TestC1GermanRetention",
            "example.test/scenario/policy::TestC2ExceptionRetention",
            "example.test/scenario/cmd/retention::TestCLIStreamsAndExits",
        ],
        "pass_to_pass": [],
        "reasons": {
            "cmd/retention/main.go": {
                1: "Native Go mutation is unavailable for this structural entrypoint/import line. TestCLIStreamsAndExits builds and executes the real retention binary and asserts exact stdout, stderr and exit codes for all eight input classes; this line is required to compile and expose that entrypoint, with no separate runtime behavior.",
                3: "Native Go mutation is unavailable for this structural entrypoint/import line. TestCLIStreamsAndExits builds and executes the real retention binary and asserts exact stdout, stderr and exit codes for all eight input classes; this line is required to compile and expose that entrypoint, with no separate runtime behavior.",
                4: "Native Go mutation is unavailable for this structural entrypoint/import line. TestCLIStreamsAndExits builds and executes the real retention binary and asserts exact stdout, stderr and exit codes for all eight input classes; this line is required to compile and expose that entrypoint, with no separate runtime behavior.",
                5: "Native Go mutation is unavailable for this structural entrypoint/import line. TestCLIStreamsAndExits builds and executes the real retention binary and asserts exact stdout, stderr and exit codes for all eight input classes; this line is required to compile and expose that entrypoint, with no separate runtime behavior.",
                7: "Native Go mutation is unavailable for this structural entrypoint/import line. TestCLIStreamsAndExits builds and executes the real retention binary and asserts exact stdout, stderr and exit codes for all eight input classes; this line is required to compile and expose that entrypoint, with no separate runtime behavior.",
                8: "Native Go mutation is unavailable for this structural entrypoint/import line. TestCLIStreamsAndExits builds and executes the real retention binary and asserts exact stdout, stderr and exit codes for all eight input classes; this line is required to compile and expose that entrypoint, with no separate runtime behavior.",
                10: "Native Go mutation is unavailable for this structural entrypoint/import line. TestCLIStreamsAndExits builds and executes the real retention binary and asserts exact stdout, stderr and exit codes for all eight input classes; this line is required to compile and expose that entrypoint, with no separate runtime behavior.",
                11: "Native Go mutation is unavailable. TestCLIStreamsAndExits/extra_arguments passes two TLDs and requires exit 2, empty stdout and the exact usage diagnostic; weakening this argument-count guard fails those delivered assertions.",
                12: "Native Go mutation is unavailable. TestCLIStreamsAndExits/extra_arguments asserts stderr is exactly usage: retention [TLD] followed by a newline, and stdout is empty. Dropping this diagnostic or writing it to stdout fails the delivered test.",
                13: "Native Go mutation is unavailable. TestCLIStreamsAndExits/extra_arguments asserts exit 2; returning successfully after the diagnostic fails the delivered test.",
                14: "Native Go mutation is unavailable for this closing brace. It delimits the excess-argument branch, whose exit and streams TestCLIStreamsAndExits/extra_arguments asserts; the delimiter has no independent behavior beyond the compiled control flow.",
                15: "Native Go mutation is unavailable. TestCLIStreamsAndExits/no_arguments asserts exactly 30 and a newline with no stderr and exit 0. A nonempty default such as de changes the policy result and fails the delivered test.",
                16: "Native Go mutation is unavailable. TestCLIStreamsAndExits requires no_arguments to succeed and german and exception to return 7 and 1. Reading argv without this single-argument branch crashes the default or ignores the supplied TLD and fails those assertions.",
                17: "Native Go mutation is unavailable. TestCLIStreamsAndExits/german and exception assert 7 and 1 with exact streams and exit 0; ignoring or replacing this argv assignment leaves the default 30 and fails those delivered tests.",
                18: "Native Go mutation is unavailable for this closing brace. It ends the optional-argument branch; TestCLIStreamsAndExits/no_arguments and german assert the separately compiled zero- and one-argument paths, with no independent delimiter behavior.",
                19: "Native Go mutation is unavailable. TestCLIStreamsAndExits asserts exact number-plus-newline stdout for no_arguments, german, exception and the case-sensitive default classes. Dropping this policy call or its print fails those delivered assertions.",
                20: "Native Go mutation is unavailable for the closing main-function brace. TestCLIStreamsAndExits builds and executes the entrypoint and asserts all returned streams and exits; this delimiter adds no behavior beyond that compiled function.",
            },
            "policy/policy.go": {
                4: "Native Go mutation is unavailable for this function signature. TestC1GermanRetention and TestC2ExceptionRetention call the exported RetentionDays function, and TestCLIStreamsAndExits builds its CLI caller; the declaration has no independent behavior beyond that tested entrypoint.",
                5: "Native Go mutation is unavailable. TestC1GermanRetention and TestC2ExceptionRetention require distinct 7 and 1 results, while TestCLIStreamsAndExits requires 30 for unknown and case-sensitive nonmatches; discarding this dispatch cannot satisfy those assertions.",
                6: "Native Go mutation is unavailable. TestC1GermanRetention requires exact lowercase de to yield 7; TestCLIStreamsAndExits/uppercase_is_default and leading_space_is_default require DE and leading-space de to yield 30. Changing this case label breaks those delivered assertions.",
                7: "Native Go mutation is unavailable. TestC1GermanRetention and TestCLIStreamsAndExits/german assert exactly 7; another return value fails those delivered tests.",
                8: "Native Go mutation is unavailable. TestC2ExceptionRetention and TestCLIStreamsAndExits/exception require exact exception to select 1, while the unknown CLI name selects 30; removing this case label fails those assertions.",
                9: "Native Go mutation is unavailable. TestC2ExceptionRetention and TestCLIStreamsAndExits/exception assert exactly 1; another return value fails those delivered tests.",
                10: "Native Go mutation is unavailable. TestCLIStreamsAndExits/no_arguments, uppercase_is_default, leading_space_is_default, domain_is_default and unknown_is_default require the fallback branch to return exactly 30 with a newline and exit 0.",
                11: "Native Go mutation is unavailable. TestCLIStreamsAndExits/no_arguments and the four exact case-sensitive fallback classes assert stdout is 30 followed by one newline; another fallback return fails those delivered assertions.",
            },
        },
    },
}


def _fixture_pins(root, pins):
    """Read only fixed fixture names, refusing borrowed leaf or ancestor links."""
    if root.is_symlink():
        return False
    boundary = root.resolve(strict=True)
    for name, pin in pins.items():
        path = root
        for part in Path(name).parts:
            path = path / part
            if path.is_symlink():
                return False
        if (
            not path.resolve(strict=True).is_relative_to(boundary)
            or hashlib.sha256(path.read_bytes()).hexdigest() != pin
        ):
            return False
    return True


def _native_fixture(data, root, producer):
    """Judge only exact reference-copy output and its current offered native proof."""
    if not isinstance(producer, dict) or not isinstance(producer.get("title"), str):
        return []
    spec = NATIVE_FIXTURES.get(producer["title"])
    if (
        spec is None
        or producer.get("check") != spec["check"]
        or producer.get("turn_paths")
        or not isinstance(producer.get("reference"), str)
        or not isinstance(producer.get("paths"), list)
        or not all(isinstance(name, str) for name in producer["paths"])
        or not set(spec["produced"]) <= set(producer["paths"])
    ):
        return []
    try:
        reference = Path(producer["reference"])
        if (
            reference.resolve(strict=True).is_relative_to(root.resolve(strict=True))
            or not _fixture_pins(reference, {name: spec["pins"][name] for name in spec["produced"]})
            or not _fixture_pins(root, spec["pins"])
        ):
            return []
    except (OSError, RuntimeError):
        return []
    proof = data.get("regression_proof") or {}
    assessment = proof.get("changed_code_evidence") or {}
    mutation = proof.get("mutation_check") or {}
    revision, context = assessment.get("source_revision"), assessment.get("proof_context")
    if (
        not isinstance(proof.get("path"), str)
        or not proof["path"].strip()
        or proof.get("verdict") != "PASS"
        or proof.get("source_revision") != revision
        or not all(
            isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef")
            for value in (revision, context)
        )
        or proof.get("test_files") != spec["tests"]
        or proof.get("case_tests") != spec["cases"]
        or not isinstance(proof.get("source_files"), list)
        or not all(isinstance(name, str) for name in proof["source_files"])
        or not set(spec["reasons"]) <= set(proof["source_files"])
        or mutation.get("collected") is not False
        or mutation.get("files") != []
        or mutation.get("survivors")
    ):
        return []
    for name in ("fail_to_pass", "pass_to_pass"):
        results = proof.get(name)
        if (
            not isinstance(results, list)
            or not all(isinstance(value, str) for value in results)
            or not set(spec[name]) <= set(results)
        ):
            return []
    accepted = []
    for row in assessment.get("obligations") or []:
        if not isinstance(row, dict):
            continue
        path, line = row.get("path"), row.get("line")
        if (
            row.get("status") != "reason_required"
            or not isinstance(path, str)
            or path not in spec["reasons"]
            or row.get("sha256") != spec["pins"][path]
            or type(line) is not int
            or line not in spec["reasons"][path]
            or row.get("mutation_outcomes") != []
        ):
            continue
        identity = [revision, context, path, spec["pins"][path], line]
        gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        if (
            row.get("gap_id") == gap
            and sum(
                isinstance(offered, dict) and offered.get("gap_id") == gap
                for offered in assessment.get("obligations") or []
            )
            == 1
        ):
            accepted.append(
                {
                    "gap_id": gap,
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": spec["reasons"][path][line],
                    "evidence_refs": [proof["path"], path],
                }
            )
    return accepted


BY_PROJECT_PINS = {
    "timesheet/__main__.py": "5a365c43e8d82dabee0935a562b903bbc7e3a704300d8937c556d1a85808b246",
    "timesheet/report.py": "fbb35625fffc8d27534aef844e0bdd10c7a3f58cdb68f1d1a5a18e6dee60d757",
    "tests/test_by_project.py": "c837b72fe58def24ef3afeb99531712e6f4c4ad837e205a6a85164fb58ef41dd",
}


def _by_project(data, root, producer):
    """The scripted Python fixture owns only its unsupported function declaration."""
    if (
        not isinstance(producer, dict)
        or producer.get("check") != "python3 -m unittest discover -s tests -t ."
        or not isinstance(producer.get("reference"), str)
        or producer.get("turn_paths")
        or not isinstance(producer.get("paths"), list)
        or not all(isinstance(name, str) for name in producer["paths"])
        or not set(BY_PROJECT_PINS) <= set(producer["paths"])
    ):
        return []
    try:
        reference = Path(producer["reference"])
        if (
            reference.resolve(strict=True).is_relative_to(root.resolve(strict=True))
            or root.resolve(strict=True).is_relative_to(reference.resolve(strict=True))
            or not _fixture_pins(reference, BY_PROJECT_PINS)
            or not _fixture_pins(root, BY_PROJECT_PINS)
        ):
            return []
    except (OSError, RuntimeError):
        return []
    proof = data.get("regression_proof") or {}
    assessment = proof.get("changed_code_evidence") or {}
    revision, context = assessment.get("source_revision"), assessment.get("proof_context")
    selected = (proof.get("mutation_check") or {}).get("selected_tests") or []
    required = {
        "tests.test_by_project.ByProjectTests.test_combines_with_project_filter",
        "tests.test_by_project.ByProjectTests.test_rows_per_week_and_project",
        "tests.test_by_project.ByProjectTests.test_cli_preserves_default_and_filters_project_rows",
    }
    if (
        proof.get("verdict") != "PASS"
        or not isinstance(proof.get("path"), str)
        or not proof["path"].strip()
        or proof.get("source_revision") != revision
        or not all(
            isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef")
            for value in (revision, context)
        )
        or not isinstance(selected, list)
        or not all(isinstance(name, str) for name in selected)
        or not required <= set(selected)
    ):
        return []
    path, line = "timesheet/report.py", 21
    identity = [revision, context, path, BY_PROJECT_PINS[path], line]
    gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    rows = [row for row in assessment.get("obligations") or [] if isinstance(row, dict) and row.get("gap_id") == gap]
    if len(rows) != 1 or any(
        (
            rows[0].get("path") != path,
            rows[0].get("sha256") != BY_PROJECT_PINS[path],
            type(rows[0].get("line")) is not int,
            rows[0].get("line") != line,
            rows[0].get("status") != "reason_required",
            rows[0].get("mutation_outcomes") != [],
        )
    ):
        return []
    return [
        {
            "gap_id": gap,
            "decision": "accept",
            "reason_kind": "instrumentation_unavailable",
            "reason": "The exact scripted weekly_project_totals declaration has no supported mutation site. "
            "test_rows_per_week_and_project observes the omitted project=None default, test_combines_with_project_filter "
            "observes the public project keyword, and test_cli_preserves_default_and_filters_project_rows observes "
            "default, grouped and filtered CLI rows. The annotations have no separate runtime behavior.",
            "evidence_refs": [proof["path"], path],
        }
    ]


CACHE_FIXTURE_PINS = {
    "app/metadata.py": "db7721cc1cfc93849a61cb9fc284e5f0dc0e8aa1699f4762622491b97302f75d",
    "app/shared_cache.py": "df2552a0f0801f4260131285136450a6434026231f25d83cb592c79f5fc44141",
    "tests/test_shared_cache.py": "c52afa211aaa1d0c8ffba02b364c5c2bd3bc6d97142ced3ca7785f456fa91882",
    "tests/test_metadata.py": "0c82a7d1e7be1d9a0be00ee8681504002bcc7c57ba84d54ad707bd6ecbeebe6f",
}
CACHE_FIXTURE_REASONS = {
    "app/metadata.py": {
        10: "The os import initializes CACHE at module load and has no supported import mutation. The configured-directory public metadata test reloads the module under METADATA_CACHE_DIR and observes reuse of the cached response; removing the import fails that named test.",
        11: "The time import initializes the public CACHE clock at module load and has no supported import mutation. The public metadata test controls time.time before module reload and asserts warm reuse and refreshed metadata at exactly 3600 seconds; removing the import fails the named test.",
        14: "The SharedFileCache import is consumed at module load and has no supported import mutation. The named public metadata test reloads the module, fetches once, reuses a fresh entry and refreshes at expiry; removing this import fails that test.",
    },
    "app/shared_cache.py": {
        20: "The bare None return has no supported mutation site. test_missing_nested_directory_and_corrupt_entry_are_replaced_and_reused observes successful fetching both without a cache file and after invalid JSON, then reuses the persisted replacement; omitting this return raises from the unbound entry and fails the test.",
    },
}


def _cache_fixture(data, root, producer):
    """The scripted Python fixture owns only its unsupported function declaration."""
    if (
        not isinstance(producer, dict)
        or producer.get("check") != "python3 -m unittest discover -s tests -t ."
        or not isinstance(producer.get("reference"), str)
        or producer.get("turn_paths") != [["docs/decisions/"], ["docs/design/"], ["app/", "tests/"]]
        or not isinstance(producer.get("paths"), list)
        or not all(isinstance(name, str) for name in producer["paths"])
        or not set(CACHE_FIXTURE_PINS) <= set(producer["paths"])
    ):
        return []
    try:
        reference = Path(producer["reference"])
        if (
            reference.resolve(strict=True).is_relative_to(root.resolve(strict=True))
            or root.resolve(strict=True).is_relative_to(reference.resolve(strict=True))
            or not _fixture_pins(reference, CACHE_FIXTURE_PINS)
            or not _fixture_pins(root, CACHE_FIXTURE_PINS)
        ):
            return []
    except (OSError, RuntimeError):
        return []
    proof = data.get("regression_proof") or {}
    assessment = proof.get("changed_code_evidence") or {}
    revision, context = assessment.get("source_revision"), assessment.get("proof_context")
    selected = (proof.get("mutation_check") or {}).get("selected_tests") or []
    required = {
        "tests.test_metadata.ParseTests.test_metadata_uses_configured_shared_cache_and_refreshes_at_one_hour",
        "tests.test_shared_cache.SharedFileCacheTests.test_missing_nested_directory_and_corrupt_entry_are_replaced_and_reused",
        "tests.test_shared_cache.SharedFileCacheTests.test_fresh_hit_returns_without_waiting_for_a_writer_lock",
        "tests.test_shared_cache.SharedFileCacheTests.test_miss_rechecks_after_lock_when_another_worker_has_published",
    }
    if (
        proof.get("verdict") != "PASS"
        or not isinstance(proof.get("path"), str)
        or not proof["path"].strip()
        or proof.get("source_revision") != revision
        or not all(
            isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef")
            for value in (revision, context)
        )
        or not isinstance(selected, list)
        or not all(isinstance(name, str) for name in selected)
        or not required <= set(selected)
    ):
        return []
    accepted = []
    for path, lines in CACHE_FIXTURE_REASONS.items():
        for line, reason in lines.items():
            identity = [revision, context, path, CACHE_FIXTURE_PINS[path], line]
            gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            rows = [
                row for row in assessment.get("obligations") or [] if isinstance(row, dict) and row.get("gap_id") == gap
            ]
            if len(rows) != 1 or any(
                (
                    rows[0].get("path") != path,
                    rows[0].get("sha256") != CACHE_FIXTURE_PINS[path],
                    type(rows[0].get("line")) is not int,
                    rows[0].get("line") != line,
                    rows[0].get("status") != "reason_required",
                    rows[0].get("mutation_outcomes") != [],
                )
            ):
                continue
            accepted.append(
                {
                    "gap_id": gap,
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], path],
                }
            )
    return accepted


REFUND_FIRST_PINS = {
    "shop/refunds.py": "54439eaa62acb4b63b11e6ec566df475494b1b08587ebc374244ab788f259fd2",
    "tests/test_refunds.py": "c6c979de956ef5357d4bd48248454f0d4575809fdd4dd339d40dd21e06279da7",
}
REFUND_FINAL_PINS = {
    "shop/clock.py": "1b1a5900adcd01624aed6be08e2854141c6451c7953d9c9423b791b1ceaf2c22",
    "shop/refunds.py": "54439eaa62acb4b63b11e6ec566df475494b1b08587ebc374244ab788f259fd2",
    "tests/test_refunds.py": "7a79d7a8fcb03e37ce06bde526bcde48cbd2af3d5e2a9989bf3a1b757d55e288",
}
REFUND_SEED_CLOCK = "f71c6afb9a9c266ebad93ecfbaf20d0bfe337b8ea179a07b9d4e9245210c7f19"
REFUND_FIRST_REASONS = {
    3: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "timedelta import is exercised by the isolated inclusive-day and next-day refund tests; omitting it "
    "makes their named calls fail.",
    5: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "store_date import is exercised by the isolated window tests, which assert both delivery and current "
    "timestamp calls in order.",
    7: "The named-test proof cannot collect mutations while its two planned integration tests are absent. "
    "WINDOW_DAYS=30 is checked at exactly the thirtieth supplied store day and the following day; either "
    "adjacent value breaks one assertion.",
    10: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "exception declaration is observed by tests requiring RefundRefused for invalid amounts, disputed "
    "orders, excess totals and expired windows.",
    14: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "refund declaration has no supported mutation site; named tests call the public function, assert its "
    "returned remaining cents, and exercise the omitted versus supplied delivery timestamp. The return "
    "annotation has no separate runtime behavior.",
    15: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "amount guard is checked with booleans, None, a string, a float, zero, negative values and the valid "
    "one-cent boundary; every invalid amount leaves existing refunds unchanged.",
    16: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "isolated non-integer tests require RefundRefused with the positive-integer message and unchanged prior "
    "refund history.",
    17: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "disputed-order branch is observed by the existing refusal test with a positive amount, which must "
    "raise and preserve the order.",
    18: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "disputed-order refusal is checked with a valid positive amount; continuing would append a refund and "
    "fail the no-change assertion.",
    19: "The named-test proof cannot collect mutations while its two planned integration tests are absent. "
    "Orders without a delivery timestamp still allow valid partial refunds, while delivered orders execute "
    "the supplied-date window checks.",
    20: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "isolated window tests assert both supplied store-date calls and distinguish the inclusive thirtieth "
    "day from the following day.",
    21: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "exact thirtieth store day succeeds and the following day is refused, detecting either adjacent window "
    "boundary.",
    22: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "expired-window test requires RefundRefused naming the final store day 2026-07-01 and preserves the "
    "prior refund list.",
    23: "The named-test proof cannot collect mutations while its two planned integration tests are absent. Two "
    "partial refunds are capped by their running total; tests assert 400 cents remaining after 600 and zero "
    "after the last 400.",
    24: "The named-test proof cannot collect mutations while its two planned integration tests are absent. A "
    "second 500-cent refund after 600 of a 1000-cent order must be refused; the exact remaining 400-cent "
    "refund succeeds.",
    25: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "cap test requires RefundRefused on the excess partial refund and checks that only the first 600 cents "
    "remain recorded.",
    26: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "successful isolated window and one-cent tests assert the appended refund, and the cap test observes "
    "earlier refunds on the next call.",
    27: "The named-test proof cannot collect mutations while its two planned integration tests are absent. The "
    "tests assert exact remaining balances of 800, 999, 400 and zero after valid refunds.",
}
REFUND_FINAL_REASONS = {
    "shop/clock.py": {
        3: "The datetime/date/timezone/timedelta import is consumed when the module loads and in store_date; no supported import mutation exists. The delivered real store-day tests call the clock through refund and assert the UTC-8 day boundary, including late evening and the exact last day."
    },
    "shop/refunds.py": {
        10: "The exception declaration has no supported mutation site. The delivered refusal and expired-window tests require RefundRefused and unchanged refund history.",
        14: "The refund signature has no supported mutation site. Delivered tests call it with partial and one-cent amounts, disputed and invalid inputs, plus delivered_at absent or supplied, and assert exact balances and store-day acceptance/refusal. Its return annotation has no separate runtime behavior.",
    },
}


def _refund_fixture(data, root, producer):
    if (
        not isinstance(producer, dict)
        or producer.get("fault") != "refund_store_clock"
        or producer.get("check") != "python3 -m unittest discover -s tests -t ."
        or not isinstance(producer.get("reference"), str)
        or producer.get("turn_paths")
        or not isinstance(producer.get("paths"), list)
        or not all(isinstance(name, str) for name in producer["paths"])
    ):
        return []
    try:
        reference = Path(producer["reference"])
        if reference.resolve(strict=True).is_relative_to(root.resolve(strict=True)) or root.resolve(
            strict=True
        ).is_relative_to(reference.resolve(strict=True)):
            return []
        first = _fixture_pins(reference, REFUND_FIRST_PINS) and _fixture_pins(
            root, {**REFUND_FIRST_PINS, "shop/clock.py": REFUND_SEED_CLOCK}
        )
        pins = REFUND_FIRST_PINS if first else REFUND_FINAL_PINS
        if (
            not set(pins) <= set(producer["paths"])
            or not _fixture_pins(reference, pins)
            or not _fixture_pins(root, pins)
        ):
            return []
    except (OSError, RuntimeError):
        return []
    proof = data.get("regression_proof") or {}
    assessment = proof.get("changed_code_evidence") or {}
    revision, context = assessment.get("source_revision"), assessment.get("proof_context")
    mutation = proof.get("mutation_check") or {}
    required = {
        "tests.test_refunds.RefundTests." + name
        for name in (
            "test_partial_refunds_are_capped_by_their_total",
            "test_refused_refunds_leave_the_order_unchanged",
            "test_one_cent_is_a_valid_amount",
            "test_non_integer_amounts_are_refused_before_any_mutation",
            *(
                [
                    "test_refund_policy_uses_supplied_dates_and_includes_the_last_day",
                    "test_refund_policy_refuses_the_following_day_without_mutating_the_order",
                ]
                if first
                else ["test_window_ends_after_the_30th_store_day", "test_window_counts_store_days_not_utc_days"]
            ),
        )
    }
    selected = (
        [*(proof.get("fail_to_pass") or []), *(proof.get("pass_to_pass") or [])]
        if first
        else mutation.get("selected_tests") or []
    )
    if (
        not isinstance(proof.get("path"), str)
        or not proof["path"].strip()
        or proof.get("source_revision") != revision
        or not all(
            isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef")
            for value in (revision, context)
        )
        or not isinstance(selected, list)
        or not all(isinstance(name, str) for name in selected)
        or not required <= set(selected)
        or any(proof.get(key) for key in ("unverified", "failed_on_candidate", "unstable"))
        or not isinstance(proof.get("not_run_on_base", []), list)
        or not all(isinstance(name, str) for name in proof.get("not_run_on_base", []))
        or not set(proof.get("not_run_on_base", [])) <= set(selected)
    ):
        return []
    if first:
        failures = proof.get("failures") or []
        missing = ("test_window_ends_after_the_30th_store_day", "test_window_counts_store_days_not_utc_days")
        if (
            proof.get("verdict") != "FAIL"
            or len(failures) != 2
            or not all(isinstance(f, str) and "has no test named " in f for f in failures)
            or not all(any("has no test named " + name + " " in failure for failure in failures) for name in missing)
            or mutation.get("collected") is not False
            or mutation.get("files") != []
        ):
            return []
        reasons = {"shop/refunds.py": REFUND_FIRST_REASONS}
    else:
        if proof.get("verdict") != "PASS" or proof.get("failures") or mutation.get("collected") is not True:
            return []
        reasons = REFUND_FINAL_REASONS
    accepted = []
    for path, lines in reasons.items():
        for line, reason in lines.items():
            identity = [revision, context, path, pins[path], line]
            gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            rows = [
                row for row in assessment.get("obligations") or [] if isinstance(row, dict) and row.get("gap_id") == gap
            ]
            if len(rows) != 1 or any(
                (
                    rows[0].get("path") != path,
                    rows[0].get("sha256") != pins[path],
                    type(rows[0].get("line")) is not int,
                    rows[0].get("line") != line,
                    rows[0].get("status") != "reason_required",
                    rows[0].get("mutation_outcomes") != [],
                )
            ):
                continue
            accepted.append(
                {
                    "gap_id": gap,
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], path],
                }
            )
    return accepted


def judgments(data, root=None, *, producer=None):
    data = _review_data(data)
    if data is None:
        return []
    root = Path.cwd() if root is None else Path(root)
    return (
        _stock(data, root)
        + _totals(data, root)
        + _greeting(data, root)
        + _greeting_js(data, root)
        + _todo(data, root)
        + _diamond(data, root)
        + _weeks(data, root)
        + _regclient(data, root)
        + _leasequeue(data, root, producer)
        + _outbox(data, root)
        + _csvcheck(data, root)
        + _package_greeting(data, root, producer)
        + _native_fixture(data, root, producer)
        + _by_project(data, root, producer)
        + _cache_fixture(data, root, producer)
        + _refund_fixture(data, root, producer)
    )


GREETING_JS = """const args = process.argv.slice(2);
if (args.length !== 1 || !args[0].trim()) {
  process.stderr.write("usage: greet.py NAME\\n");
  process.exit(2);
}
process.stdout.write("Hello, " + args[0] + "\\n");
"""

GREETING_JS_REASONS = {
    "if (args.length !== 1 || !args[0].trim()) {": "No JavaScript mutation support in this runner. test_exact_streams_and_exits drives greet.js through its unchanged Python adapter for all five input classes (one name, empty, whitespace, none, two); weakening either side of this guard fails the empty, whitespace or extra-argument case.",
}


def _greeting_js(data, root):
    """The recovery-novelty JavaScript repair: its product has no mutation support, its adapter test reproduces it."""
    proof = data.get("regression_proof") or {}
    try:
        source = (root / "greet.js").read_text()
    except OSError:
        return []
    # The controlled fixture Builder appends `// controlled attempt N` lines; comments are never obligations.
    if [line for line in source.splitlines() if line.strip() and not line.lstrip().startswith("//")] != [
        line for line in GREETING_JS.splitlines() if line.strip()
    ]:
        return []
    if not proof.get("path"):
        return []
    digest, lines, accepted = hashlib.sha256(source.encode()).hexdigest(), source.splitlines(), []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        line = row.get("line")
        if (
            row.get("status") != "reason_required"
            or row.get("path") != "greet.js"
            or row.get("sha256") != digest
            or type(line) is not int
            or not 0 < line <= len(lines)
            or row.get("mutation_outcomes")
        ):
            continue
        if reason := GREETING_JS_REASONS.get(lines[line - 1].strip()):
            accepted.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], "greet.js"],
                }
            )
    return accepted


GREETING_REFERENCE = (
    "4611627c9ef9c3b8ae32bb1b4946ae4fa3c735aff704dd160a92ff2dbbdc1e70",
    "52eccb4f679487a8e767237fb93d2418057ecf6deafdc79040b6789152fc5776",
)
# capacity-recovery's broken/accepts-empty-name: the reference without the empty-name guard, and the
# reference tests without test_cli_empty_name (which that product fails). Its scripted run must still
# complete, so the scenario oracle, not the changed-line gate, is what catches the wrong delivery.
GREETING_ACCEPTS_EMPTY_NAME = (
    "f032639aa67dc3f07e8a9de264f7f99268390c5a43ea90bbe92af158563c14ec",
    "2de57dd8e6ab85beaf56b94fcd9f200750faa72645615e9d72ac211a232e4021",
)
GREETING_ACCEPTS_EMPTY_NAME_REASONS = {
    'USAGE = "usage: greet.py NAME"': "No supported assignment mutation. test_no_arg requires a usage message on stderr with exit 2; deleting or emptying the literal fails that assertion.",
    "raise SystemExit(main())": "No supported exit-adapter mutation. The CLI tests require exit zero on success and exit 2 on missing and extra names; discarding main's return value fails those checks.",
}


def _greeting(data, root):
    proof = data.get("regression_proof") or {}
    try:
        source = (root / "greet.py").read_bytes()
        digest = hashlib.sha256(source).hexdigest()
        # The adversarial-planning cases move the CLI tests into tests/ to exercise
        # subdirectory planning; both layouts must keep serving the same judgment.
        tests = next(
            (candidate for candidate in (root / "test_greet.py", root / "tests/test_greet.py") if candidate.is_file()),
            None,
        )
        if tests is None:
            return []
        inputs = (digest, hashlib.sha256(tests.read_bytes()).hexdigest())
        if inputs not in (GREETING_REFERENCE, GREETING_ACCEPTS_EMPTY_NAME):
            return []
    except OSError:
        return []
    if not proof.get("path"):
        return []
    reasons = {
        "import sys": "No supported import mutation. The CLI tests execute argv, stderr and exit behavior; removing the import breaks those observed flows.",
        'USAGE = "usage: greet.py NAME"': "No supported assignment mutation. test_no_arg and test_cli_empty_name require a usage message on stderr with exit 2; deleting or emptying the literal fails those assertions.",
        "def greet(name: str) -> str:": "The header declares the callable and builtin type annotations, with no independent CLI behavior. test_greet and test_unicode assert the separately measured returned greetings.",
        "def main(argv=None) -> int:": "The header declares the optional argv input and builtin return type. The CLI tests exercise the default argument, parsing, stdout and exit behavior on the body lines; the header adds no separate task behavior.",
        "print(greet(argv[0]))": "No supported call mutation. test_cli_success asserts exact Hello, Ada plus newline on stdout, empty stderr and exit zero; removing or redirecting this call fails the test.",
        'if __name__ == "__main__":': "The mutator deliberately excludes entrypoint guards. The CLI success and invalid-input tests start this entrypoint and assert its outputs and exits; disabling it fails those tests.",
        "raise SystemExit(main())": "No supported exit-adapter mutation. The CLI tests require exit zero on success and exit 2 on missing, extra and empty names; discarding main's return value fails those checks.",
    }
    if inputs == GREETING_ACCEPTS_EMPTY_NAME:
        reasons.update(GREETING_ACCEPTS_EMPTY_NAME_REASONS)
    lines, accepted = source.decode().splitlines(), []
    capture = (
        _killed_capture(proof, "greet.py", digest, lines.index('    return f"Hello, {name}"') + 1)
        if inputs == GREETING_REFERENCE
        else []
    )
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        line = row.get("line")
        if (
            row.get("status") != "reason_required"
            or row.get("path") != "greet.py"
            or row.get("sha256") != digest
            or type(line) is not int
            or not 0 < line <= len(lines)
            or row.get("mutation_outcomes")
        ):
            continue
        if reason := reasons.get(lines[line - 1].strip()):
            accepted.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [
                        proof["path"],
                        "greet.py",
                        # Entrypoint guards stay unmutated as call and assignment coverage grows.
                        *(capture if lines[line - 1].strip() == 'if __name__ == "__main__":' else []),
                    ],
                }
            )
    return accepted


CSVCHECK_SOURCES = {
    "csvcheck/__init__.py": "e1bf7e66b36a78babfc904506780ff71b331bcf1a4d6bde320f8fac6fb72dafb",
    "csvcheck/__main__.py": "0472e2ffee01c7582c667fb3eb1ef01023e7d584f175cef8cb475e45e6bd1736",
}
CSVCHECK_TESTS = {
    "tests/test_csvcheck.py": "0f38d7a9365dec107086cd5f6f61517bfb1c36f20cf9138d0e974320c1459dcc",
    "tests/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}
CSVCHECK_REASONS = {
    "csvcheck/__init__.py": {
        "import argparse": "No supported import mutation. main builds its parser from argparse; tests/test_csvcheck.py drives `python3 -m csvcheck PATH` through the CLI, so removing the import breaks every observed flow.",
        "import csv": "No supported import mutation. The reader, field_size_limit and csv.Error paths below are what the quoting and header tests observe through the CLI exit code and stderr.",
        "import json": "No supported import mutation. test_valid and test_reports_errors parse the CLI's stdout as JSON; removing the import breaks that output.",
        "import re": "No supported import mutation. The age and email field checks use re.fullmatch; the error-row tests observe those fields in the stdout JSON.",
        "import sys": "No supported import mutation. sys.maxsize seeds the field-size search and the error path writes to sys.stderr, both observed by the CLI tests.",
        "def strict_quoted_lines(source):": "The header declares the generator; its body lines carry the quoting behavior. The CLI tests observe quoting only through exit codes and stderr after csv.reader consumes its output.",
        '"""Keep CSV parsing in csv.reader, rejecting quotes in unquoted fields."""': "A docstring carries no runtime behavior; the suite asserts the quoting behavior its body implements, so there is no mutation site here.",
        'state = "start"': "No supported assignment mutation. This initializes and resets the quoting state machine; csv.reader consumes its output and the CLI tests observe malformed quoting as exit 2 with stderr.",
        "for line in source:": "No supported loop mutation. The state machine advances per line; dropping lines changes which inputs csv.reader sees and fails the quoting-driven CLI checks.",
        "for char in line:": "No supported loop mutation. Quoting state advances per character; skipping characters misclassifies quotes and fails the malformed-quoting exit check.",
        'if state == "quoted":': "No supported comparison mutation. The quoting state machine's branch decides closed-quote and unterminated handling; the CLI tests observe both as specific exit codes and stderr.",
        "if char == '\"':": "No supported comparison mutation. Detecting the closing quote drives the state machine; misreading it fails the malformed-quoting CLI check.",
        'state = "closed"': "No supported assignment mutation. The closed state is what later doubled quotes and delimiters branch on; the CLI tests observe its effect through exit 2 on malformed quoting.",
        'elif state == "closed" and char == \'"\':': "No supported comparison mutation. A quote directly after a closing quote is the doubled-quote form; this branch is what keeps it legal, which the valid-input CLI test requires.",
        'state = "quoted"  # doubled quote inside a quoted field': "No supported assignment mutation. Doubled quotes inside a quoted field are legal CSV; this transition keeps them parseable, which the valid-input CLI test requires.",
        'elif char in ",\\r\\n":': "No supported membership mutation. Delimiters reset the field state; mishandling them misclassifies later quotes and fails the CLI quoting checks.",
        'elif state == "start":': "No supported comparison mutation. Only start-state opening quotes begin a quoted field; the CLI tests observe the difference as malformed-quoting failures.",
        'state = "quoted" if char == \'"\' else "unquoted"': "No supported assignment or comparison mutation. Opening quotes classify the field; the quoting and valid-input CLI tests observe this through the exit code.",
        'elif char == \'"\' or state == "closed":': "No supported comparison mutation. A bare quote outside a quoted field is malformed CSV; the CLI tests require exit 2 with stderr for it.",
        'raise csv.Error("malformed CSV quoting")': "No supported raise mutation. The CLI tests observe rejected quoting only through this error reaching stderr with exit 2.",
        "yield line": "No supported yield mutation. csv.reader consumes exactly these lines; withholding one starves the parser and fails the row-count and quoting checks.",
        'raise csv.Error("unterminated quoted field")': "No supported raise mutation. An unterminated quoted field must surface as exit 2 with stderr; dropping the raise fails that CLI check.",
        "def main(argv=None):": "The header declares the CLI entrypoint and its optional argv; every test observes main through the module CLI's exit code, stdout and stderr.",
        'parser = argparse.ArgumentParser(description="Validate contact CSV")': "No supported call mutation. The CLI tests invoke `python3 -m csvcheck PATH`; the parser is what turns argv into the path every flow reads.",
        'parser.add_argument("path")': "No supported call mutation. Without the positional path argument the CLI cannot be invoked as the tests require.",
        "args = parser.parse_args(argv)": "No supported call mutation. The parsed path feeds the open below; the CLI tests observe a missing argument as argparse's own exit and usage.",
        "limit = sys.maxsize": "No supported assignment mutation. This seeds the largest field-size search; the CLI flows below observe the resolved limit through csv.field_size_limit.",
        "while True:": "No supported loop mutation. The search loop walks the limit down until the platform accepts it; breaking it starves field_size_limit and fails the CLI flows.",
        "try:": "No supported statement mutation. Both guarded blocks exist to route their failures to the CLI error path the tests observe as exit 2 with stderr.",
        "csv.field_size_limit(limit)": "No supported call mutation. The accepted limit is what lets long fields parse; the CLI tests observe a rejected limit as an uncaught failure.",
        "break": "No supported statement mutation. Stopping at the first accepted limit is what keeps the resolved value; without it the search degrades the limit and changes parsing.",
        "except OverflowError:": "No supported exception mutation. Platforms rejecting huge limits raise here; the search loop depends on catching it to continue.",
        "limit //= 10": "No supported assignment mutation. The search step is what eventually satisfies field_size_limit on every platform the suite runs on.",
        'with open(args.path, encoding="utf-8", newline="") as source:': "No supported call mutation. The CLI tests write the exact CSV this open reads; changing the mode or encoding surfaces through the error path or wrong parsing.",
        "reader = csv.reader(strict_quoted_lines(source), strict=True)": "No supported call mutation. The strict reader over the guarded lines is the parser every row assertion observes.",
        "header = next((row for row in reader if row), None)": "No supported call mutation. Skipping blank leader rows finds the header the bad-header test rejects.",
        'if header != ["name", "age", "email"]:': "No supported comparison mutation. test_bad_header requires a wrong header to exit 2 with empty stdout.",
        'raise ValueError("expected header name,age,email")': "No supported raise mutation. The wrong-header CLI check observes this through the shared error path as exit 2 and stderr.",
        "errors = []": "No supported assignment mutation. Every error row the tests assert is appended to this list; changing it breaks the stdout JSON.",
        "count = 0": "No supported assignment mutation. The row count the valid-input test asserts starts here.",
        "for row in reader:": "No supported loop mutation. Dropping rows changes the asserted count and error rows in the stdout JSON.",
        "if not row:": "No supported comparison mutation. Blank data rows are not counted; keeping them would change the asserted rows value.",
        "continue": "No supported statement mutation. Skipping blank rows is what keeps the asserted row count exact.",
        "count += 1": "No supported assignment mutation. The valid-input test asserts rows equals the data records this counts.",
        "if len(row) != 3:": "No supported comparison mutation. Wrong-width rows are reported with the columns field in the asserted error JSON.",
        'fields = ["columns"]': "No supported assignment mutation. The column-count error the tests assert names this exact field list.",
        "else:": "No supported statement mutation. The three-field rows the valid-input test asserts flow through this branch.",
        "name, age, email = (field.strip() for field in row)": "No supported assignment mutation. Stripped fields feed every name, age and email check the error rows assert.",
        "fields = []": "No supported assignment mutation. The per-field error list starts empty; the asserted error JSON is built from it.",
        "if not name:": "No supported comparison mutation. Empty names must appear in the error rows the tests assert.",
        'fields.append("name")': "No supported call mutation. The asserted error JSON names the invalid fields exactly.",
        'bounded_age = age.lstrip("0") or "0"': "No supported assignment mutation. Leading zeros are bounded before the length and range checks; the two-record criterion keeps age 0 valid through it.",
        'if not re.fullmatch(r"[0-9]+", age) or len(bounded_age) > 3 or int(bounded_age) > 130:': "No supported comparison mutation. test_reports_errors asserts the age field error for a non-numeric age, and the two-record criterion keeps ages 0 and 130 valid.",
        'fields.append("age")': "No supported call mutation. The asserted error JSON names the age field exactly.",
        'if not re.fullmatch(r"[^@\\s]+@[^@\\s]+", email):': "No supported comparison mutation. test_reports_errors asserts the email field error for an invalid address.",
        'fields.append("email")': "No supported call mutation. The asserted error JSON names the email field exactly.",
        "if fields:": "No supported comparison mutation. Only faulty records reach the asserted error rows.",
        'errors.append({"row": count + 1, "fields": fields})': "No supported call mutation. test_reports_errors asserts the exact row number and fields of each error.",
        "except (OSError, UnicodeError, csv.Error, ValueError) as exc:": "No supported exception mutation. Every failure path the CLI tests observe as exit 2 with stderr routes through this handler.",
        "print(str(exc), file=sys.stderr)": "No supported call mutation. test_bad_header requires the failure message on stderr.",
        "return 2": "No supported return mutation. test_bad_header asserts exit 2 with empty stdout.",
        'print(json.dumps({"rows": count, "errors": errors}))': "No supported call mutation. test_valid and test_reports_errors assert this exact JSON on stdout.",
        "return 1 if errors else 0": "No supported return mutation. test_valid asserts exit 0 and test_reports_errors asserts exit 1.",
    },
    "csvcheck/__main__.py": {
        "from . import main": "No supported import mutation. The CLI tests invoke `python3 -m csvcheck`; this adapter is what makes the package runnable.",
        "raise SystemExit(main())": "No supported exit-adapter mutation. The tests assert exits 0, 1 and 2; discarding main's return value fails those checks.",
    },
}


def _csvcheck(data, root):
    """adversarial planning on ladder-03: the reference CLI's lines without a mutation site, tests pinned."""
    proof = data.get("regression_proof") or {}
    try:
        sources = {path: (root / path).read_bytes() for path in CSVCHECK_SOURCES}
        if any(hashlib.sha256(sources[path]).hexdigest() != digest for path, digest in CSVCHECK_SOURCES.items()):
            return []
        if any(hashlib.sha256((root / path).read_bytes()).hexdigest() != pin for path, pin in CSVCHECK_TESTS.items()):
            return []
    except OSError:
        return []
    if not proof.get("path"):
        return []
    accepted = []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        path, line = row.get("path"), row.get("line")
        if (
            row.get("status") != "reason_required"
            or path not in CSVCHECK_SOURCES
            or row.get("sha256") != CSVCHECK_SOURCES[path]
            or type(line) is not int
            or row.get("mutation_outcomes")
        ):
            continue
        lines = sources[path].decode().splitlines()
        if not 0 < line <= len(lines):
            continue
        if reason := CSVCHECK_REASONS[path].get(lines[line - 1].strip()):
            accepted.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], path],
                }
            )
    accepted.extend(_csvcheck_equivalent_mutant(data, root, proof, sources))
    return accepted


def _csvcheck_equivalent_mutant(data, root, proof, sources):
    """line 60's len(bounded_age) > 3 guard: 3 -> 4 is the one equivalent survivor."""
    mutants = [
        m
        for f in (proof.get("mutation_check") or {}).get("files") or []
        if f.get("path") == "csvcheck/__init__.py"
        for m in f.get("mutants") or []
    ]
    surviving = [m for m in mutants if m.get("outcome") == "survived"]
    if (
        not mutants
        or not surviving
        or any(m.get("outcome") not in ("killed", "survived") for m in mutants)
        or any(
            (m.get("line"), m.get("operator"), m.get("before"), m.get("after")) != (60, "shift_bound", "3", "4")
            for m in surviving
        )
    ):
        return []
    digest = CSVCHECK_SOURCES["csvcheck/__init__.py"]
    return [
        {
            "gap_id": row["gap_id"],
            "decision": "accept",
            "reason_kind": "equivalent_mutant",
            "reason": "The len(bounded_age) > 3 guard's only surviving mutant (3 -> 4) is equivalent: an age of four or more digits after leading zeros are stripped is at least 1000, which the adjacent int(bounded_age) > 130 comparison already rejects, so the guard's exact digit threshold cannot change any observable verdict. tests/test_csvcheck.py::CsvCheckTests.test_age_131_and_1000_are_invalid kills the neighboring 130 -> 131 and > -> >= mutants, and every other mutant on the line is killed; no other surviving or incomplete mutation is accepted.",
            "evidence_refs": [proof["path"], "csvcheck/__init__.py"],
        }
        for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []
        if row.get("status") == "reason_required"
        and row.get("path") == "csvcheck/__init__.py"
        and row.get("sha256") == digest
        and row.get("line") == 60
        and row.get("mutation_outcomes")
        and set(row["mutation_outcomes"]) <= {"killed", "survived"}
    ]


PROGRESSIVE_SOURCE_PINS = {
    "lessons.py": "129848968512dcc13d3ce6ab0fd390551b8d0939173c468ae236826e7172b1e9",
    "progress.py": "dc0d4f690b6aa11e30b8c68e60d51daf76d937441e383b21acd7a08c16b850d1",
}
PROGRESSIVE_S1_PIN = "d8bfc46841de7ceddb30d67d74d207dcd29d79391fe3e0614b4f23c3882771b0"
PROGRESSIVE_TEST_PIN = "7c6635275a9dc2ae27128e377543d64645aa71eea941acb3b7d078e1d6eb45ec"


def progressive_sources(reference, slice_id, *, catalog=False, corrupt=False, note=False):
    """Exact bytes authored by the progressive Builder, shared with its scripted Tester.

    Refuse unknown producer inputs rather than deriving permissions from workspace bytes.
    """
    reference = Path(reference)
    sources = {name: (reference / name).read_bytes() for name in PROGRESSIVE_SOURCE_PINS}
    if any(hashlib.sha256(sources[name]).hexdigest() != pin for name, pin in PROGRESSIVE_SOURCE_PINS.items()):
        raise ValueError("Unknown progressive reference bytes")
    if slice_id == "S1":
        scenario = next(parent for parent in reference.parents if (parent / "scenario.toml").is_file())
        sources["lessons.py"] = (scenario / "slices/S1/lessons.py").read_bytes()
        if hashlib.sha256(sources["lessons.py"]).hexdigest() != PROGRESSIVE_S1_PIN:
            raise ValueError("Unknown progressive S1 bytes")
    elif catalog:
        sources["lessons.py"] += b"\n\ndef catalog():\n    return list(LESSONS)\n"
    elif corrupt:
        sources["lessons.py"] = sources["lessons.py"].replace(
            b"if response != LESSONS[lesson][1]:", b"if lesson == 'addition' or response != LESSONS[lesson][1]:"
        )
    if note:
        sources["lessons.py"] += b"\n# Scoped consent: lesson note\n"
    return sources


def progressive_judgments(data, reference, slice_id, *, root=None, **variant):
    """Only unsupported imports in the exact Builder variant receive reasons."""
    data = _review_data(data)
    if data is None:
        return []
    root = Path.cwd() if root is None else Path(root)
    proof = data.get("regression_proof") or {}
    try:
        scripted = progressive_sources(reference, slice_id, **variant)
        if (
            not proof.get("path")
            or hashlib.sha256((root / "test_journey.py").read_bytes()).hexdigest() != PROGRESSIVE_TEST_PIN
        ):
            return []
        if any((root / name).read_bytes() != content for name, content in scripted.items()):
            return []
    except (OSError, ValueError, StopIteration):
        return []
    imports = {"lessons.py": {1: "progress"}, "progress.py": {1: "json", 2: "pathlib.Path"}}
    accepted = []
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        name, line = row.get("path"), row.get("line")
        if (
            row.get("status") != "reason_required"
            or name not in imports
            or type(line) is not int
            or line not in imports[name]
            or row.get("sha256") != hashlib.sha256(scripted[name]).hexdigest()
            or row.get("mutation_outcomes")
            or not row.get("gap_id")
        ):
            continue
        accepted.append(
            {
                "gap_id": row["gap_id"],
                "decision": "accept",
                "reason_kind": "instrumentation_unavailable",
                "reason": (
                    f"The scripted {imports[name][line]} import has no supported mutation site. "
                    "Skeleton.test_answer_survives_reopen checks wrong-answer refusal, durable accepted answers "
                    "and duplicate preservation through lessons and progress; removing this import breaks those assertions."
                ),
                "evidence_refs": [proof["path"], name],
            }
        )
    return accepted
