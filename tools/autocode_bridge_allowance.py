"""Each person's monthly spending allowance on a shared bridge, enforced through the run's own cost cap.

A team that shares one bridge shares its backend credential, so the operator may give each GitHub login
a monthly allowance in USD (``AUTOCODE_BRIDGE_PERSON_CAPS``). Nothing here adds a second cost check: at a
run's start the requester's remaining allowance becomes that run's ``--max-cost-usd`` (or lowers a cap the
launch options already give), so the core pauses the run at the cap exactly as it pauses any capped run.
That cap is reserved from the allowance while the run is in flight; once it settles, only what it spent
counts. A run whose spend is unknown or unpriced counts what is known and is reported as partly unknown:
it does not block a start (docs/plans/multi-user.md, open questions).

Like every spending cap, an allowance is the owner's: it is read from the host's configuration, never
raised by AutoCode, and a person at their allowance waits for the next month or for the operator.

Pure functions over plain bridge records; imports nothing from AutoCode.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime

CAP_OPTION = "--max-cost-usd"
# Below a cent a run cannot do useful work, and the core reads a 0 cap as no cap at all.
SMALLEST_RUN_USD = 0.01


def parse_caps(text: str | None) -> dict[str, float]:
    """``AUTOCODE_BRIDGE_PERSON_CAPS``: a JSON object of GitHub login to monthly USD; blank means none."""
    if text is None or not text.strip():
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("must be a JSON object of GitHub login to monthly USD") from None
    if not isinstance(value, dict):
        raise ValueError("must be a JSON object of GitHub login to monthly USD")
    caps = {}
    for login, amount in value.items():
        if not isinstance(login, str) or not login.strip():
            raise ValueError("names a login that is not a non-empty string")
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            raise ValueError(f"gives {login!r} {amount!r}: an allowance is a finite nonnegative amount in USD")
        caps[login.strip().lower()] = float(amount)
    return caps


def period(now: datetime | None = None) -> str:
    """The allowance period a run starting now belongs to: its calendar month in UTC."""
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m")


def requester(record: dict) -> str:
    return str((record.get("requested_by") or {}).get("login") or "").lower()


def charge(record: dict, in_flight: bool) -> tuple[float, bool]:
    """What one run counts against its requester's allowance, and whether part of it is unknown.

    In flight, its reserved cap or what it has spent, whichever is more. Settled, what it spent.
    """
    cost = record.get("cost")
    cost = cost if isinstance(cost, dict) else {}
    spent = _amount(cost.get("spent_usd"))
    unknown = spent is None or bool(cost.get("unpriced_stages")) or cost.get("complete") is False
    if in_flight:
        reserved = _amount(record.get("reserved_usd")) or 0.0
        return max(reserved, spent or 0.0), unknown and not reserved
    return spent or 0.0, unknown


def _amount(value) -> float | None:
    """A finite nonnegative USD amount, or None for anything else (a missing, damaged or negative value)."""
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def used(records, login: str, month: str, in_flight) -> dict:
    """A login's runs in one month: the USD they count and how many have spend partly unknown.

    ``in_flight`` is the bridge's own test of whether a record's run still holds its slot.
    """
    login = login.lower()
    total, unknown, runs = 0.0, 0, 0
    for record in records:
        if requester(record) != login or record.get("period") != month or record.get("queued"):
            continue
        if record.get("failed") and not record.get("run_dir"):
            continue  # a start that never happened spent nothing
        amount, partly_unknown = charge(record, in_flight(record))
        total += amount
        unknown += int(partly_unknown)
        runs += 1
    return {"login": login, "period": month, "used_usd": round(total, 6), "runs": runs, "unknown_runs": unknown}


def remaining(caps: dict[str, float], usage: dict) -> float | None:
    """What is left of a login's allowance, or None when the login has none."""
    cap = caps.get(usage["login"])
    return None if cap is None else max(0.0, round(cap - usage["used_usd"], 6))


def capped_options(options: list[str], allowance: float) -> tuple[list[str], float]:
    """Launch options whose cost cap is at most ``allowance``, and the cap the run gets.

    A cap the options already give that is lower stays; a higher one, or 0 (no cap), is lowered. The
    amount is rounded down to the cent so the reservation never exceeds what is left.
    """
    allowance = math.floor(allowance * 100) / 100
    words, given, index = [], None, 0
    while index < len(options):
        word = options[index]
        if word == CAP_OPTION and index + 1 < len(options):
            given, index = options[index + 1], index + 2
            continue
        if word.startswith(CAP_OPTION + "="):
            given, index = word.split("=", 1)[1], index + 1
            continue
        words.append(word)
        index += 1
    try:
        existing = float(given) if given is not None else 0.0
    except ValueError:
        existing = 0.0
    chosen = allowance if not existing or not math.isfinite(existing) or existing > allowance else existing
    return [*words, CAP_OPTION, f"{chosen:.2f}".rstrip("0").rstrip(".")], chosen
