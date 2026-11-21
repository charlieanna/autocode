"""Allowlisted read-only status observations; owns no run actions."""

import json
import sys


def _worker(request):
    if not isinstance(request, dict):
        raise ValueError("Invalid status observation")
    payload = request["payload"]
    if request.get("operation") == "retained_retry":
        import autocode_acceptance_retry_next as retry_next

        return retry_next.decide(payload["state"], payload["workspace"])
    if request.get("operation") == "report_repair":
        import autocode_source_scope as source_scope
        import autocode_stage_recovery as stage_recovery

        state, workspace = payload["state"], payload["workspace"]
        revision = payload.get("revision") or source_scope.snapshot(workspace, state)["revision"]
        return {
            "revision": revision,
            "stale": stage_recovery.stale_report_repair(state, workspace, revision=revision) is not None,
        }
    if request.get("operation") == "catalog_offer":
        import autocode_catalog_reconciliation as catalog

        return catalog.offer(payload["state"], controller=payload["controller"])
    if request.get("operation") == "source_offer":
        import autocode_source_reconciliation as source

        return source.offer(
            payload["state"], payload["workspace"], payload["inspection"], controller=payload["controller"]
        )
    raise ValueError("Unsupported status observation")


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit(2)
    print(json.dumps({"result": _worker(json.load(sys.stdin))}))
