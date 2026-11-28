"""Authenticate the tested source behind a plan-correction handoff, not a new verdict."""

from pathlib import Path

try:
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_util as util
    import autocode_verification_schedule as schedule


def checked_source(validation, run_dir, revision, previous):
    """Return immutable receipt pins only for passing named tests of this exact source and assignment."""
    replay = validation.get("check_replay") or {}
    if (
        validation.get("verdict") != "PASS"
        or validation.get("source_revision") != revision
        or replay.get("verdict") != "PASS"
        or replay.get("source_revision") != revision
        or replay.get("simulated")
    ):
        return None
    pins, named_tests = {}, False
    for row in replay.get("checks") or []:
        try:
            metadata = row["scheduling"]
            path = Path(metadata["receipt"])
            if (validation.get("evidence_hashes") or {}).get(str(path)) != metadata["receipt_sha256"]:
                return None
            saved = util.read_object(path)
            receipt = {**saved["result"], "scheduling": metadata}
            root = Path(run_dir) / "check-replay" / "obligations"
            if schedule.authenticated_attempt(receipt, root) != path.parent:
                return None
            identity = saved["identity"]
            obligation = identity["obligation"]
            if (
                identity["execution"]["source_revision"] != revision
                or obligation["contract"]["hash"] != previous["contract_hash"]
                or obligation["task_id"] != previous["id"]
            ):
                return None
            result = saved["result"]
            if identity.get("check_kind") == "compile":
                if (
                    result.get("exit_code") != 0
                    or result.get("timed_out")
                    or result.get("error")
                    or not schedule.intact(result, root=path.parent)
                ):
                    return None
            elif schedule.reusable(result, root=path.parent):
                named_tests = True
            else:
                return None
            pins[str(path)] = metadata["receipt_sha256"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
    return pins if named_tests else None
