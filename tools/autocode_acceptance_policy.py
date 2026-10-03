"""Acceptance examples exercise the requested domain; they cannot redefine it.

Shared by planning and independent validation so a general testing suggestion
cannot override an approved design, or make one example stand for all inputs.
"""

DOMAIN = """
NUMERIC BOUNDARIES: derive the valid numeric domain from the requested behavior, existing API and any approved
 design. Test 2**63-1, 2**63, and 10**5000 (and large negatives for signed domains) when that contract requires
 unbounded integer semantics. Probe persistence, exact arithmetic and rollback wherever the requested behavior
 uses them. SQLite INTEGER bindings stop at 64 bits and SQL overflow can promote to REAL; use lossless storage
 and application integer arithmetic for unbounded values. Decimal int/str conversion can hit Python's digit limit.
An int annotation alone does not impose arbitrary-precision semantics on a design that explicitly uses floats.
Preserve that float representation and public return type; do not demand 10**5000 support, replace it with
Fraction/Decimal, invent an expanded numeric requirement, or ask the user to revise an approved design just to
satisfy this testing advice. Test relevant boundaries within the specified domain and mixed-type interactions.
If the source contract itself genuinely conflicts, cite both binding source requirements; testing advice is
not a source requirement. Do not invent a bound for an explicitly unbounded integer contract.
"""

COVERAGE = """
REQUIREMENT COVERAGE: concrete acceptance examples are necessary checks, not replacements for the general
rules in the request and approved brief. Preserve those rules in required_behaviors and plan tests for each
relevant input class. A trace to one criterion ID proves only the behavior its examples actually cover; it
does not establish the rest of a broad requirement. Read each source_quote, split combined requirements into
independent obligations, and check them against the proposed examples before accepting coverage. Raise a
blocking plan concern for an omitted required input class. Keep testable variants in executable criteria;
file inspection alone cannot prove input behavior. Do not expand the requested domain or reopen settled choices.
For parsing rules that ignore blank records or separators, cover them before the first meaningful record,
between records, after the last record, and when only ignored records remain. Exercise combinations with
required quoting or multiline behavior; the first logical record need not be the first physical line.
Independent validation checks these classes even when the Builder's named example tests already pass.
"""
