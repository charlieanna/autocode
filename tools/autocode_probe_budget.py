"""What a probe may spend, request by request, and what a short or unpriced result means (#3855-#3857).

``autocode probe`` measures models under a cost cap the person set, and the cap is theirs: it must
hold exactly, in every probe stage (Requirements, the Planner, the Plan Reviewer, the Builder, the
Tester and the Completion Reviewer). This module is the one place that decides it:

- a probe's cap is a finite positive number of USD (``Budget`` refuses anything else);
- each request's run is given what remains, floored to the cent (``Budget.allowance``), and written
  as its ``--max-cost-usd`` floored to the cent (``cap_argument``). A run's cap is written to the
  cent and 0.00 would mean no cap at all to a regular run, so no run is launched when less than
  ``MIN_CHILD_CAP`` remains;
- a known charge is the reply's own cost; a cost that is not a finite non-negative number is unknown
  and is charged at the whole allowance its run was given, the most it could have cost
  (``Budget.charge``), so an unknown charge never renews the same spending authority. Since each run
  is given everything that remains, that ends the probe, and the reason says so;
- a run that paused at its own cost cap (``COST_CAP_STATUS``) ends the probe;
- a model's measurement is a partial attempt, never a result, when one of those budget stops ended
  the probe while the model was still due a request (``Budget.cut_off``). A measurement short for
  any other reason (a failed quick pass, a plan that never reached approval, a provider quota stop)
  is a result as before.

Amounts are kept as exact decimals (``Decimal(repr(value))``), so float rounding never grants or
withholds a cent.

Layer: domain; imports only the standard library.
"""

from __future__ import annotations

import math
from decimal import ROUND_FLOOR, Context, Decimal

# The smallest cap a child run accepts: its --max-cost-usd is written to the cent.
MIN_CHILD_CAP = Decimal("0.01")
# The status of a run that paused at its own cost cap (autocode_coordination_limits.cost_pause).
COST_CAP_STATUS = "PAUSED_COST_LIMIT"
_CENT = Decimal("0.01")
# Enough digits for any float amount to the cent, so no sum or floor is ever rounded.
_EXACT = Context(prec=800)


def amount(value) -> Decimal | None:
    """``value`` as an exact decimal when it is a finite non-negative number, else None (an unknown amount)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    result = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    return result if result >= 0 else None


def cap(value) -> Decimal:
    """A probe's cap in USD: a finite positive number, or ValueError (the regular run's finite rule, and more than 0)."""
    result = amount(value)
    if result is None or result <= 0:
        raise ValueError(f"the probe needs a finite positive cost cap in USD, not {value!r}")
    return result


def floor_cent(value: Decimal) -> Decimal:
    """``value`` rounded down to a whole cent."""
    return value.quantize(_CENT, rounding=ROUND_FLOOR, context=_EXACT)


def cap_argument(value) -> str:
    """A child run's ``--max-cost-usd`` text for an allowance of ``value``: rounded down to the cent.

    Raises ValueError when less than ``MIN_CHILD_CAP`` would remain, so no child is launched with
    more than it is allowed, or with 0.00, which a regular run reads as no cap.
    """
    result = amount(value)
    if result is None:
        raise ValueError(f"a probe run needs a finite non-negative cost cap, not {value!r}")
    floored = floor_cent(result)
    if floored < MIN_CHILD_CAP:
        raise ValueError(f"a probe run needs at least {money(MIN_CHILD_CAP)}, and only {money(result)} is allowed")
    return str(floored)


def money(value: Decimal) -> str:
    """``value`` in dollars, with two decimals, or more when they are needed to be exact ($0.016)."""
    cents = value.quantize(_CENT, context=_EXACT)
    if cents == value:
        return f"${cents}"
    return "$" + format(value.normalize(_EXACT), "f")


class Budget:
    """One probe's spending under its cap: what each request may spend, and what each reply charged."""

    def __init__(self, cap_usd):
        self.cap = cap(cap_usd)
        self.spent = Decimal(0)
        self.unpriced = 0
        self.stopped: str | None = None
        # The allowance the last reply was charged when its cost was unknown, else None.
        self._unknown: Decimal | None = None

    def remaining(self) -> Decimal:
        return _EXACT.subtract(self.cap, self.spent)

    def allowance(self) -> Decimal | None:
        """What the next request's run may spend, floored to the cent, or None (with ``stopped`` set) when no run may start."""
        if self.stopped:
            return None
        remaining = self.remaining()
        allowed = floor_cent(max(remaining, Decimal(0)))
        if allowed >= MIN_CHILD_CAP:
            return allowed
        reason = f"the probe reached its {money(self.cap)} cap"
        if self._unknown is not None:
            reason += f": a reply had no price on record and was charged its whole {money(self._unknown)} allowance"
        elif remaining > 0:
            reason += f": {money(remaining)} remains, less than the {money(MIN_CHILD_CAP)} a run accepts"
        self.stopped = reason
        return None

    def charge(self, cost, allowance: Decimal, run_status=None) -> None:
        """Charge one reply: its ``cost`` when known, else the whole ``allowance`` its run was given.

        A run that paused at its own cost cap ends the probe.
        """
        known = amount(cost)
        if known is None:
            self.unpriced += 1
            self._unknown = allowance
            known = allowance
        else:
            self._unknown = None
        self.spent = _EXACT.add(self.spent, known)
        if run_status == COST_CAP_STATUS and not self.stopped:
            self.stopped = f"a run stopped at its {money(allowance)} cost cap"

    def cut_off(self, still_due) -> bool:
        """True when a budget stop ended the probe while a model was ``still_due`` a request: a partial attempt."""
        return bool(self.stopped) and bool(still_due)

    def report(self) -> dict:
        """The probe outcome's spending: the charged total, unpriced replies, whether spend is uncertain, and why it stopped."""
        return {
            "spent_usd": round(float(self.spent), 4),
            "unpriced_replies": self.unpriced,
            "spend_uncertain": self.unpriced > 0,
            "stopped": self.stopped,
        }
