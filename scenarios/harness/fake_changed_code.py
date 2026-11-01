"""Handwritten independent judgments for exact, tested scenario fixtures.

This is not a generic reference-solution waiver. Unknown bytes, weak tests and
surviving/unrun mutants receive no judgment; the core still authenticates every
offered gap, source pin and cited runner proof.
"""

import hashlib
from pathlib import Path

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
    "ca4befa627cc78630e8527a189254648ec880635e945e917b0aab0851154edfe",
    "68b50358a20e551c82ea64ef10a3b8ba570bbc294c544742e417185506c835f6",
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
                    "evidence_refs": [proof["path"], "timesheet/weeks.py"],
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
            "falls through to this line.",
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
            "No supported assignment mutation (a list display, not a literal). "
            "test_retryable_errors_are_deduplicated_and_sorted asserts the exact merged tuples; any other start "
            "fails it.",
        ),
        "for group in groups:": _REGCLIENT_LOOP,
        "for code in group:": _REGCLIENT_LOOP,
        'TRANSIENT = ("SERVER_BUSY", "RATE_LIMITED")': (
            "instrumentation_unavailable",
            "No supported assignment mutation (a tuple, not a scalar literal). "
            "test_retryable_errors_are_deduplicated_and_sorted asserts DEFAULT's and .com's exact codes, and "
            "test_server_busy_is_retried retries SERVER_BUSY; another tuple fails them.",
        ),
        'SESSION = ("SESSION_LIMIT_EXCEEDED",)': (
            "instrumentation_unavailable",
            "No supported assignment mutation (a tuple, not a scalar literal). test_com_retries_session_limit "
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
            or row.get("mutation_outcomes")
        ):
            continue
        lines = sources[path].decode().splitlines()
        if not 0 < line <= len(lines):
            continue
        if judged := REGCLIENT_REASONS[path].get(lines[line - 1].strip()):
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
LEASE_TESTS = "d736b080b6d9cd3095de44622bf20ccd54ac815569f2e55a6fcd875d1772b8ce"
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
OUTBOX_TESTS = "21bb3e3db547ed67f9018ba8d9c6de211cce3dcd95e6a34b0dbbcc40d0e90d4e"
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
        if (
            row.get("status") != "reason_required"
            or row.get("path") != source_path
            or row.get("sha256") != digest
            or row.get("mutation_outcomes")
        ):
            continue
        if reason := reasons.get(row.get("line")):
            accepted.append(
                {
                    "gap_id": row["gap_id"],
                    "decision": "accept",
                    "reason_kind": "instrumentation_unavailable",
                    "reason": reason,
                    "evidence_refs": [proof["path"], source_path],
                }
            )
    return accepted


def _leasequeue(data, root):
    sources = {LEASE_SOURCE: LEASE_REASONS, LEASE_TOKENS_SOURCE: LEASE_TOKENS_REASONS}
    return _lifecycle(data, root, "leasequeue/__init__.py", sources, LEASE_TESTS)


def _outbox(data, root):
    return _lifecycle(data, root, "outbox/__init__.py", {OUTBOX_SOURCE: OUTBOX_REASONS}, OUTBOX_TESTS)


def judgments(data, root=None):
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
        + _leasequeue(data, root)
        + _outbox(data, root)
        + _csvcheck(data, root)
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
            (
                candidate
                for candidate in (root / "test_greet.py", root / "tests/test_greet.py")
                if candidate.is_file()
            ),
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
                    "evidence_refs": [proof["path"], "greet.py"],
                }
            )
    return accepted


CSVCHECK_SOURCES = {
    "csvcheck/__init__.py": "e1bf7e66b36a78babfc904506780ff71b331bcf1a4d6bde320f8fac6fb72dafb",
    "csvcheck/__main__.py": "0472e2ffee01c7582c667fb3eb1ef01023e7d584f175cef8cb475e45e6bd1736",
}
CSVCHECK_TESTS = {
    "tests/test_csvcheck.py": "a2d7a8c0baf8c87a9a13bf14bc6e42968427a9f0aa19773ddea0474293202fec",
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
        "elif state == \"closed\" and char == '\"':": "No supported comparison mutation. A quote directly after a closing quote is the doubled-quote form; this branch is what keeps it legal, which the valid-input CLI test requires.",
        'state = "quoted"  # doubled quote inside a quoted field': "No supported assignment mutation. Doubled quotes inside a quoted field are legal CSV; this transition keeps them parseable, which the valid-input CLI test requires.",
        'elif char in ",\\r\\n":': "No supported membership mutation. Delimiters reset the field state; mishandling them misclassifies later quotes and fails the CLI quoting checks.",
        'elif state == "start":': "No supported comparison mutation. Only start-state opening quotes begin a quoted field; the CLI tests observe the difference as malformed-quoting failures.",
        "state = \"quoted\" if char == '\"' else \"unquoted\"": "No supported assignment or comparison mutation. Opening quotes classify the field; the quoting and valid-input CLI tests observe this through the exit code.",
        "elif char == '\"' or state == \"closed\":": "No supported comparison mutation. A bare quote outside a quoted field is malformed CSV; the CLI tests require exit 2 with stderr for it.",
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
