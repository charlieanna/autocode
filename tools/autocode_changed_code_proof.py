"""Runner-owned measurements of changed product lines for a regression proof.

The existing proof owns the candidate tree and command budget. This adapter
retains the producing receipts and source pins, then asks the pure policy for
its per-line obligations. It never turns an advisory summary into authority.
"""

from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_changed_code_evidence as policy
    from . import autocode_changed_code_measurement as measurement
    from . import autocode_command_receipt as command_receipt
    from . import autocode_mutation_check as mutation
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_util as util
    from . import autocode_verify as verify
except ImportError:
    import autocode_changed_code_evidence as policy
    import autocode_changed_code_measurement as measurement
    import autocode_command_receipt as command_receipt
    import autocode_mutation_check as mutation
    import autocode_source_snapshot as source_snapshot
    import autocode_util as util
    import autocode_verify as verify

VERSION = 1


def collect(proof, result, framework, workspace, out, tree, execution_context, *, source_paths=()):
    """Measure one exact candidate; keep gaps when a tool or attribution is absent."""
    source_revision = proof["source_revision"]
    context = util.digest(execution_context)
    files = verify.product_files(result.get("changes") or {})
    diff = verify.product_diff(workspace, result["base"], files)
    inventory = measurement.inventory(tree, files, diff)
    coverage_report = proof.get("diff_coverage") or {}
    coverage_receipts = coverage_report.get("command_receipts") or []
    coverage = measurement.coverage_envelope(
        coverage_report,
        inventory,
        source_revision=source_revision,
        proof_context=context,
        receipts=coverage_receipts,
        root=out,
    )
    inventory = measurement.refine_inventory(
        inventory,
        coverage,
        source_revision=source_revision,
        proof_context=context,
    )
    receipts = []
    evidence = Path(out) / "mutation-check"
    baseline = source_snapshot.snapshot(tree, paths=source_paths)["files"]
    expected = source_snapshot.snapshot(workspace, paths=source_paths)["files"]

    def run(command, label, limit):
        receipt = verify.run_suite(framework, command, tree, evidence, label, timeout=limit)
        # Ownership uncertainty must retain the scratch tree and stop normal
        # cleanup. A truncated/missing capture is never a killed mutant.
        if not measurement.known_capture(receipt, root=out):
            raise command_receipt.OwnershipUncertain("Changed-code command has no intact completed capture")
        receipts.append(receipt)
        return receipt

    candidate = (result.get("checks") or {}).get("suite_on_candidate") or {}
    selected = sorted((candidate.get("results") or {}).get("passed") or [])
    report = (
        mutation.collect_selected_tests(
            (result.get("framework") or {}).get("name"),
            (result.get("commands") or {}).get("suite"),
            selected,
            tree,
            evidence,
            files,
            lambda chosen: verify.product_diff(workspace, result["base"], chosen),
            run,
            enforcement=True,
        )
        if proof.get("verdict") == verify.PASS
        else mutation.record("The regression proof has not passed")
    )
    proof["changed_code_receipts"] = [*coverage_receipts, *receipts]
    mutated = measurement.mutation_envelope(
        report,
        source_revision=source_revision,
        proof_context=context,
        receipts=receipts,
        root=out,
    )
    observed = source_snapshot.snapshot(tree, paths=source_paths)["files"]
    # Tests may create reports or fixtures inside their owned scratch tree.
    # They are not delivered inputs. Every actual workspace input must retain
    # its bytes, and the workspace itself must remain exactly as measured.
    if (
        any(baseline.get(name) != digest or observed.get(name) != digest for name, digest in expected.items())
        or source_snapshot.snapshot(workspace, paths=source_paths)["files"] != expected
    ):
        # A test changed the measured inputs. Preserve every observation but
        # grant no execution or mutation authority from this tree.
        coverage = {"source_revision": source_revision, "proof_context": context, "files": []}
        mutated = {"source_revision": source_revision, "proof_context": context, "files": []}
        inventory = None
    assessment = policy.assess(
        inventory,
        coverage,
        mutated,
        source_revision=source_revision,
        proof_context=context,
    )
    proof["changed_code_inputs"] = {
        "inventory": inventory,
        "coverage": coverage,
        "mutation": mutated,
        "command_outputs": [receipt["output"] for receipt in proof["changed_code_receipts"]],
    }
    proof["changed_code_evidence"] = assessment
    return mutation.final(report, proof["verdict"], proof.get("case_tests"))


def reviewed(proof, exceptions):
    """Reassess using only exceptions admitted from an independent source-bound report."""
    inputs = proof.get("changed_code_inputs") or {}
    original = proof.get("changed_code_evidence") or {}
    return policy.assess(
        inputs.get("inventory"),
        inputs.get("coverage"),
        inputs.get("mutation"),
        source_revision=original.get("source_revision"),
        proof_context=original.get("proof_context"),
        exceptions=exceptions,
    )


def intact(proof, result):
    """Artifacts still authenticate this measurement, regardless of its verdict."""
    if proof.get("changed_code_inputs") != result.get("changed_code_inputs"):
        return False
    if not isinstance(result.get("changed_code_inputs"), dict):
        return False
    if not isinstance(result.get("changed_code_receipts"), list):
        return False
    inputs = result["changed_code_inputs"]
    receipts = result["changed_code_receipts"]
    outputs = [row.get("output") for row in receipts if isinstance(row, dict)]
    if len(outputs) != len(receipts) or len(set(outputs)) != len(outputs):
        return False
    expected = inputs.get("command_outputs")
    if expected is not None and expected != outputs:
        return False
    required = set()
    coverage = result.get("diff_coverage") or {}
    if (inputs.get("coverage") or {}).get("files") and coverage.get("collected") is not True:
        return False
    required.update(coverage.get("command_outputs") or [])
    mutation_report = result.get("mutation_check") or {}
    if mutation_report.get("reference_output"):
        required.add(mutation_report["reference_output"])
    for file in (inputs.get("mutation") or {}).get("files") or []:
        for mutant in file.get("mutants") or []:
            required.update(mutant.get("evidence") or [])
    if not required <= set(outputs):
        return False
    try:
        if any(
            not measurement.known_capture(row, root=Path(proof["path"]).parent)
            for row in result.get("changed_code_receipts") or []
        ):
            return False
    except (OSError, ValueError, TypeError, KeyError):
        return False
    if coverage.get("collected") is True or (inputs.get("coverage") or {}).get("files"):
        if not coverage.get("command_outputs"):
            return False
        try:
            measurement.pinned(coverage["report_path"], coverage["report_sha256"], Path(proof["path"]).parent)
        except (OSError, ValueError, KeyError, TypeError):
            return False
    return True


def ready(proof, result, validation):
    """Current immutable measurement plus still-pinned independent reasons."""
    if not intact(proof, result):
        return False
    exceptions = (validation or {}).get("changed_code_decisions") or []
    if exceptions:
        pins = validation.get("evidence_hashes") or {}
        if (
            validation.get("source_revision") != proof.get("source_revision")
            or validation.get("reviewer_role") not in ("sol", "astra")
            or not pins
            or any(not Path(p).is_file() or util.file_hash(p) != h for p, h in pins.items())
        ):
            return False
    return reviewed(result, exceptions).get("status") == "PASS"
