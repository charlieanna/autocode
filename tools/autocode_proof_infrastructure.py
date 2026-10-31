"""Authenticate disk-only collection failures superseded by fresh runner proof."""

from datetime import datetime
from pathlib import Path

try:
    from . import autocode_util as util
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_util as util
    import autocode_verification_schedule as schedule


def disk_collection_failure(state, row, revision):
    """True only for retained runner checks that collected no behavioral failure.

    The caller must authenticate the current passing proof. A report's assertion
    about infrastructure cannot settle a finding: the old owned receipt and logs
    must show disk exhaustion and collection errors, with no failing test names.
    """
    try:
        proof = state.get("regression_proof") or {}
        if proof.get("verdict") != "PASS" or proof.get("source_revision") != revision:
            return False
        root = Path(state["run_dir"]) / "regression"
        path = Path(row["last_reported_in"])
        if (
            not path.resolve().is_relative_to(root.resolve())
            or not path.is_file()
            or path.stat().st_nlink != 1
            or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root))
        ):
            return False
        record = next(
            (
                r
                for r in reversed(state.get("stages", []))
                if r.get("stage") == "regression_proof" and r.get("runner_owned") and r.get("output") == str(path)
            ),
            None,
        )
        if not record or datetime.fromisoformat(proof["proved_at"]) <= datetime.fromisoformat(record["finished_at"]):
            return False
        prior = util.read_object(path)
        checks = prior["checks"]
        labels = {"regression_on_base", "regression_on_candidate", "suite_on_candidate"}
        if (
            prior.get("verdict") != "FAIL"
            or prior.get("source_revision") != revision
            or not isinstance(checks, dict)
            or not labels <= checks.keys()
            or not {"suite_on_base", "suite_on_base_with_tests"} & checks.keys()
            or not schedule.proof_intact(checks, root=path.parent)
        ):
            return False
        for label, check in checks.items():
            result = check.get("results") or {}
            failed = result.get("failed") or []
            collection = result.get("collection_errors") or []
            output = Path(check["output"])
            if (
                check.get("exit_code") != 1
                or not failed
                or not set(failed) <= set(collection)
                or any(not name.endswith("::[build failed]") for name in failed)
                or label.startswith("regression_")
                and result.get("passed")
                or util.file_hash(output) != check.get("output_sha256")
                or "no space left on device" not in output.read_text(errors="replace").lower()
            ):
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError, AttributeError, RuntimeError):
        return False
