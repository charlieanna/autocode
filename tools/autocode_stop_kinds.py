"""The supervisor's stops that end a stage which had not failed.

The person's Stop now or Send now (``person``, #3263) and the person's cost cap reached while the
stage ran (``cost``, autocode_cost_watch) end a provider through the deadline path, but nothing timed
out or broke: the attempt is archived with its partial work, never counted as a failure, a stall or an
automatic recovery, and the same stage runs again when the run continues. Every place that treats a
stopped attempt this way reads this one list. Imports nothing.
"""

UNFAILED = ("person", "cost")


def unfailed(kind) -> bool:
    """Whether a ``timeout_kind`` names a stop that ended a stage which had not failed."""
    return kind in UNFAILED
