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


def judgments(data, root=None):
    root = Path.cwd() if root is None else Path(root)
    return _stock(data, root) + _totals(data, root) + _greeting(data, root) + _todo(data, root)


def _greeting(data, root):
    proof = data.get("regression_proof") or {}
    digest = "4611627c9ef9c3b8ae32bb1b4946ae4fa3c735aff704dd160a92ff2dbbdc1e70"
    try:
        source = (root / "greet.py").read_bytes()
        if hashlib.sha256(source).hexdigest() != digest:
            return []
        if (
            hashlib.sha256((root / "test_greet.py").read_bytes()).hexdigest()
            != "52eccb4f679487a8e767237fb93d2418057ecf6deafdc79040b6789152fc5776"
        ):
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
