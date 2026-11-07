"""Bounded display snapshots of a coordinated task's distinct public usage ledgers."""

from copy import deepcopy


def snapshot(anchor, views, pending=()):
    """Keep source, members and retained joint attempts, counting each run once.

    Pending members have not launched a provider yet. Missing usage on an
    observed run stays unknown; a client must not price it as a free attempt.
    This display snapshot does not change budget policy or any saved ledger.
    """
    rows = {}
    for view in views:
        run = view["run_dir"]
        cost = view.get("cost") or {}
        rows[run] = {
            "run_dir": run,
            "cost": {key: cost.get(key) for key in ("spent_usd", "inherited_usd", "complete")},
            "usage": {
                "accounting": {"tokens": deepcopy((view.get("usage") or {}).get("accounting", {}).get("tokens", {}))}
            },
        }
    for run in pending:
        rows.setdefault(
            run,
            {
                "run_dir": run,
                "cost": {"spent_usd": 0, "inherited_usd": 0, "complete": True},
                "usage": {"accounting": {"tokens": {"input_tokens": 0, "output_tokens": 0}}},
            },
        )
    return {"version": 1, "anchor": str(anchor), "runs": list(rows.values())}
