"""Pure Arena scoring and paired candidate gates; no model or repository access."""
from __future__ import annotations


def verdict(status: str, checks: list[dict], error: str | None = None) -> str:
    if error or not checks:
        return "ERROR"
    passed = all(row["ok"] is True for row in checks)
    complete = status in ("TASK_COMPLETE", "COMPLETE")
    if complete:
        return "PASS" if passed else "FALSE_COMPLETE"
    # A pause is an observation, not proof that stopping was the right decision.
    return "STOPPED" if status.startswith(("PAUSED", "AWAITING")) else "FAIL"


def summarize(rows: list[dict]) -> dict:
    counts = {name: sum(r["verdict"] == name for r in rows)
              for name in ("PASS", "FALSE_COMPLETE", "STOPPED", "FAIL", "ERROR", "RUNNING")}
    complete = counts["PASS"] + counts["FALSE_COMPLETE"]
    return {"attempts": len(rows), "outcomes": counts,
            "solve_rate": counts["PASS"] / len(rows) if rows else None,
            "false_complete_per_attempt": counts["FALSE_COMPLETE"] / len(rows) if rows else None,
            "false_complete_per_completion": counts["FALSE_COMPLETE"] / complete if complete else None,
            "elapsed_seconds": sum(r.get("elapsed_seconds") or 0 for r in rows)}


def compare(cases: list[dict], baseline: list[dict], candidate: list[dict]) -> dict:
    """One fresh paired attempt per case; failures and missing evidence block the gate.

    Improvements may be developed on development cases. Regression and holdout
    cases must be scored too; the gate never modifies or promotes a version.
    """
    reasons, pairs = [], []
    for name, rows in (("baseline", baseline), ("candidate", candidate)):
        if len({r["version_sha256"] for r in rows}) != 1:
            reasons.append(f"{name}: require one source version per cohort")
        if any(r["case_id"] not in {c["id"] for c in cases} for r in rows):
            reasons.append(f"{name}: undeclared case in cohort")
    if not cases or not {"regression", "holdout"}.issubset({c["split"] for c in cases}):
        reasons.append("declare nonempty regression and holdout sets")
    for case in cases:
        left = [r for r in baseline if r["case_id"] == case["id"]]
        right = [r for r in candidate if r["case_id"] == case["id"]]
        if len(left) != 1 or len(right) != 1:
            reasons.append(f"{case['id']}: require exactly one baseline and one candidate attempt")
            continue
        a, b = left[0], right[0]
        if a["case_sha256"] != case["sha256"] or b["case_sha256"] != case["sha256"]:
            reasons.append(f"{case['id']}: case identity changed")
        if (a["execution_kind"] != b["execution_kind"] or a["options"] != b["options"]
                or a.get("plan_approval") != b.get("plan_approval")):
            reasons.append(f"{case['id']}: execution configuration differs")
        if "live" != a["execution_kind"] or "live" != b["execution_kind"]:
            reasons.append(f"{case['id']}: fixture evidence cannot qualify a version")
        if a["version_sha256"] == b["version_sha256"]:
            reasons.append(f"{case['id']}: versions are identical")
        if a["verdict"] in ("ERROR", "RUNNING") or b["verdict"] in ("ERROR", "RUNNING"):
            reasons.append(f"{case['id']}: incomplete evaluation")
        if b["verdict"] == "FALSE_COMPLETE":
            reasons.append(f"{case['id']}: candidate falsely claimed completion")
        if a["verdict"] == "PASS" and b["verdict"] != "PASS":
            reasons.append(f"{case['id']}: previously passing case regressed")
        pairs.append({"case": case["id"], "split": case["split"],
                      "baseline": a["verdict"], "candidate": b["verdict"]})
    unseen = [p for p in pairs if p["split"] == "holdout"]
    if not any(p["baseline"] != "PASS" and p["candidate"] == "PASS" for p in unseen):
        reasons.append("no measured solve improvement on holdout cases")
    return {"decision": "REJECT" if reasons else "CANDIDATE_FOR_HUMAN_REVIEW",
            "reasons": reasons, "pairs": pairs, "promoted": False}
