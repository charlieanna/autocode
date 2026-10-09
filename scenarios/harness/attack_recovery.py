"""Scripted provider faults for the black-box recovery adversarial tests.

This module runs inside the fake provider process. It changes only that
provider's report/stream and its own bookkeeping, never AutoCode's state.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import sys
from pathlib import Path


FAULTS = frozenset({
    "investigator_iteration", "investigator_run_root", "investigator_missing_citation",
    "investigator_uncited_input", "truncated_once", "truncated_repeated",
    "completion_denial", "builder_permission_corrected", "builder_permission_repeated",
    "builder_permission_distinct",
})
MISSING_REF = "README.md.adversarial-missing"


def install(fake, configuration: dict, trace) -> None:
    """Wrap the existing provider; bookkeeping is outside the tested project.

    The caller must give each test its own control directory. Separate provider
    launches share only the counters and selected report saved here. Evidence
    files are read only when the public provider handoff explicitly names them.
    """
    attack = configuration["case"]
    if attack not in FAULTS:
        raise ValueError(f"unknown recovery attack: {attack}")
    control = Path(configuration["root"]) / "recovery-control"
    control.mkdir(parents=True, exist_ok=True)
    counts_file = control / "recovery-counts.json"
    trace_file = control / "recovery-trace.jsonl"
    saved_report = control / "investigator-report.json"
    counts = json.loads(counts_file.read_text()) if counts_file.exists() else {}
    original_report, original_emit = fake.report_for, fake.emit
    pending_truncation = False

    def mark(event: str, **fields) -> None:
        trace(event, **fields)
        with trace_file.open("a") as stream:
            stream.write(json.dumps({"event": event, "attack": attack, **fields}) + "\n")

    def investigator(data: dict) -> dict:
        # A report-only repair does not repeat the original public handoff.
        # Replay the exact original answer, retaining the failing probe rather
        # than silently removing the evidence requirement to obtain a pass.
        if data.get("report_repair"):
            report = json.loads(saved_report.read_text())
            mark("investigator_repair", probe=report["probe"])
            return report
        candidates = []
        for row in data.get("recent_stages", []):
            path = Path(row.get("output", ""))
            if str(row.get("stage", "")).startswith("astra_discovery") and path.is_file():
                try:
                    report = json.loads(path.read_text())
                except (ValueError, OSError):
                    continue
                if MISSING_REF in report.get("code_refs", []):
                    candidates.append(path)
        if not candidates:
            raise RuntimeError("fault never reached Investigator with an actual rejected planner report")
        rejected = candidates[-1]
        refs = [str(rejected)]
        checks = [
            "import json; from pathlib import Path",
            f"r=json.loads(Path({('run/' + rejected.name)!r}).read_text())",
            f"assert {MISSING_REF!r} in r['code_refs']",
        ]
        state_file = Path(data["state_file"])
        if attack == "investigator_run_root":
            refs.append(str(state_file))
            checks.append("s=json.loads(Path('run/state.json').read_text())")
            checks.append("assert s['task']")
        elif attack == "investigator_uncited_input":
            # A genuinely necessary uncited file must remain unavailable.
            checks.append("assert json.loads(Path('run/state.json').read_text())['task']")
        elif attack == "investigator_missing_citation":
            refs.append(str(rejected.with_name("never-produced-evidence.json")))
        report = {
            "diagnosis": f"The Planner cites {MISSING_REF}, which does not exist.",
            "cause": "stage_output",
            "guidance": "Remove the nonexistent code_refs entry and cite only existing repository files.",
            "recommendation": "retry", "user_question": "", "evidence_refs": refs,
            "example": f"The saved planner report cites {MISSING_REF}; checking that nonexistent path rejects it.",
            "probe": shlex.join([sys.executable, "-c", "; ".join(checks)]), "untestable": "",
        }
        saved_report.write_text(json.dumps(report))
        mark("investigator_probe", evidence_refs=refs, probe=report["probe"],
             cited_files=[{"path": ref, "exists": Path(ref).is_file(),
                           "sha256": hashlib.sha256(Path(ref).read_bytes()).hexdigest()
                           if Path(ref).is_file() else None} for ref in refs])
        return report

    def report_for(stage: str, data: dict) -> dict:
        nonlocal pending_truncation
        counts[stage] = counts.get(stage, 0) + 1
        counts_file.write_text(json.dumps(counts))
        repair = bool(data.get("report_repair"))
        mark("provider_stage", stage=stage, invocation=counts[stage], repair=repair)
        if attack.startswith("builder_permission_") and stage == "terra":
            partial = Path("greet.py")
            if counts[stage] == 1:
                partial.write_text("# retained partial implementation\n")
            mark("builder_permission_handoff", recovery=data.get("recovery_context"),
                 artifact_policy=data.get("builder_artifact_policy"),
                 partial=partial.read_text() if partial.exists() else None)
            if counts[stage] == 1 or attack in ("builder_permission_repeated", "builder_permission_distinct"):
                mark("builder_permission_denied", invocation=counts[stage])
                # A distinct denied path per invocation makes each denial a new
                # incident, so only the permission ceiling can bound this loop.
                path = (f"/tmp/diagnostic-{counts[stage]}/*" if attack == "builder_permission_distinct"
                        else "/tmp/diagnostic/*")
                print(f"permission requested: external_directory ({path}); auto-rejecting", flush=True)
                raise SystemExit(0)
            recovery = data.get("recovery_context") or {}
            directory = Path(recovery.get("diagnostic_directory", "missing-recovery-directory"))
            if not directory.is_dir() or not directory.resolve().is_relative_to(Path.cwd().resolve()):
                raise RuntimeError("recovery did not provision a workspace-contained diagnostic directory")
            policy = data.get('builder_artifact_policy') or {}
            if not directory.is_relative_to(Path(policy.get('evidence_directory', 'missing-artifact-directory'))):
                raise RuntimeError('recovery scratch path contradicts the Builder artifact policy')
            if partial.read_text() != "# retained partial implementation\n":
                raise RuntimeError("partial work was lost before the corrected diagnostic")
            receipt = directory / "diagnostic.txt"
            receipt.write_text("corrected diagnostic executed\n")
            mark("builder_permission_corrected", directory=str(directory), receipt=str(receipt),
                 denied_operation=recovery.get("denied_operation"))
        if attack.startswith("investigator_"):
            if stage == "investigate_stuck":
                return investigator(data)
            if stage == "astra_discovery":
                report = original_report(stage, data)
                if "INVESTIGATOR GUIDANCE" not in fake.PROMPT:
                    report["code_refs"] = [MISSING_REF]
                    mark("planner_citation_injected", stage=stage, repair=repair)
                else:
                    mark("guided_planner_retry", code_refs=report.get("code_refs"))
                return report
        if attack in ("truncated_once", "truncated_repeated") and stage == "astra_discovery":
            if attack == "truncated_repeated" or counts[stage] == 1:
                pending_truncation = True
        if attack == "completion_denial" and stage == "astra_review":
            validation = data.get("validation") or {}
            mark("completion_handoff", validation_verdict=validation.get("verdict"),
                 source_revision=data.get("source_revision"),
                 validation_revision=validation.get("source_revision"),
                 repair=repair)
            if counts[stage] == 1:
                if validation.get("verdict") != "PASS":
                    raise RuntimeError("completion denial was not injected after accepted validation")
                mark("completion_permission_denied", validation=validation)
                # Match the provider's real nonterminal permission-denial stream.
                # There is no write to the project and no completed-turn event.
                print("permission requested: external_directory (/tmp/adversarial/*); auto-rejecting", flush=True)
                raise SystemExit(0)
        return original_report(stage, data)

    def emit(event: dict) -> None:
        if pending_truncation and event.get("type") == "turn.completed":
            output = Path(sys.argv[sys.argv.index("-o") + 1])
            output.write_text('{"summary":"truncated provider report","contract":')
            mark("truncated_report_emitted", output=str(output))
        original_emit(event)

    fake.report_for, fake.emit = report_for, emit
