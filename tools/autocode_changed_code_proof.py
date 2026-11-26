"""Runner-owned measurements of changed product lines for a regression proof.

The existing proof owns the candidate tree and command budget. This adapter
retains the producing receipts and source pins, then asks the pure policy for
its per-line obligations. It never turns an advisory summary into authority.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from . import autocode_changed_code_evidence as policy
    from . import autocode_changed_code_measurement as measurement
    from . import autocode_command_receipt as command_receipt
    from . import autocode_dependency_dirs as dependency_dirs
    from . import autocode_mutation_check as mutation
    from . import autocode_mutation_checkpoint as mutation_checkpoint
    from . import autocode_package_scope as package_scope
    from . import autocode_proof_stability as proof_stability
    from . import autocode_python_tests as python_tests
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_test_results as test_results
    from . import autocode_util as util
    from . import autocode_verify as verify
except ImportError:
    import autocode_changed_code_evidence as policy
    import autocode_changed_code_measurement as measurement
    import autocode_command_receipt as command_receipt
    import autocode_dependency_dirs as dependency_dirs
    import autocode_mutation_check as mutation
    import autocode_mutation_checkpoint as mutation_checkpoint
    import autocode_package_scope as package_scope
    import autocode_proof_stability as proof_stability
    import autocode_python_tests as python_tests
    import autocode_source_snapshot as source_snapshot
    import autocode_test_results as test_results
    import autocode_util as util
    import autocode_verify as verify

VERSION = 6


def compact_receipt(receipt):
    """A receipt as the proof stores it: without the observer's bulk, but with what authenticates it.

    A mutation continuation authenticates the receipts an earlier window stored
    (``autocode_verification_schedule.intact``), and that recomputes the source binding from its pinned
    capture. The stored binding keeps every field that check reads, the probe's own receipt included,
    and drops only the observation list it recomputes.
    """
    binding = receipt.get("source_binding") if isinstance(receipt, dict) else None
    if isinstance(binding, dict):
        return {**receipt, "source_binding": _compact_binding(binding)}
    return receipt


def shown_receipt(receipt):
    """A stored receipt as a stage's handoff shows it: its source binding's verdict only.

    A model reads what the binding decided, never re-authenticates it, so the pinned paths the proof
    keeps for that would only crowd the handoff."""
    binding = receipt.get("source_binding") if isinstance(receipt, dict) else None
    if isinstance(binding, dict):
        return {
            **receipt,
            "source_binding": {key: binding.get(key) for key in ("version", "framework", "status", "reason")},
        }
    return receipt


def _compact_binding(binding):
    if not isinstance(binding, dict):
        return binding
    kept = {key: value for key, value in binding.items() if key != "observations"}
    if isinstance(kept.get("children"), list):
        kept["children"] = [_compact_binding(child) for child in kept["children"]]
    return kept


def collect(
    proof,
    result,
    framework,
    workspace,
    out,
    tree,
    execution_context,
    *,
    source_paths=(),
    previous=None,
    evidence_dir=None,
    deadline=None,
    progress=None,
):
    """Measure one exact candidate; keep gaps when a tool or attribution is absent."""
    source_revision = proof["source_revision"]
    context = util.digest(execution_context)
    changes = result.get("changes") or {}
    files = test_results.product_files(changes, source_files=result.get("source_files"))
    installed = dependency_dirs.summary(
        path for path, status in changes.items() if status != "deleted" and test_results.is_code_path(path)
    )
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
    kept = ((previous or {}).get("changed_code_inputs") or {}).get("coverage")
    if (
        previous is not None
        and not coverage["files"]
        and previous.get("diff_coverage") == coverage_report
        and isinstance(kept, dict)
        and (kept.get("source_revision"), kept.get("proof_context")) == (source_revision, context)
    ):
        # A continuation window measures in a new copy, and the copy the coverage report was measured in
        # is gone, so its bytes cannot be read again. The same report bound to the same candidate and
        # execution context was authenticated when it was measured: keep that envelope.
        coverage = json.loads(json.dumps(kept))
    inventory = measurement.refine_inventory(
        inventory,
        coverage,
        source_revision=source_revision,
        proof_context=context,
    )
    if previous is not None and (
        previous.get("execution_context") != execution_context or not intact(previous, result)
    ):
        raise ValueError("Retained changed-code evidence is not intact in this execution context")
    coverage_outputs = {row["output"] for row in coverage_receipts}
    receipts = [
        row for row in (previous or {}).get("changed_code_receipts", []) if row["output"] not in coverage_outputs
    ]
    evidence = Path(evidence_dir) if evidence_dir is not None else Path(out) / "mutation-check"
    # Scratch copies contain the nested repository's files, not its Git
    # metadata. Compare copyable file inventories rather than a submodule's
    # revision token against a flattened directory. Still pin the full original
    # workspace identity so its nested Git/source boundaries cannot drift.
    expected = source_snapshot.inventory(workspace, paths=source_paths)
    baseline = source_snapshot.copied_files(tree, expected)
    original_workspace = source_snapshot.snapshot(workspace, paths=source_paths)
    package, suite, selected = selected_tests(framework, result)

    def run(command, label, limit):
        if package:
            command = package_scope.command(package, command)
        receipt = verify.run_suite(framework, command, tree, evidence, label, timeout=limit, source_workspace=workspace)
        # Ownership uncertainty must retain the scratch tree and stop normal
        # cleanup. A truncated/missing capture is never a killed mutant.
        if not measurement.known_capture(receipt, root=out):
            raise command_receipt.OwnershipUncertain("Changed-code command has no intact completed capture")
        receipts.append(receipt)
        return receipt

    report = (
        mutation.collect_selected_tests(
            (result.get("framework") or {}).get("name"),
            suite,
            selected,
            tree,
            evidence,
            files,
            lambda chosen: verify.product_diff(workspace, result["base"], chosen),
            run,
            enforcement=True,
            previous=previous.get("mutation_check") if previous is not None else None,
            deadline=deadline,
            progress=progress,
        )
        if proof.get("verdict") == verify.PASS
        else mutation.record("The regression proof has not passed")
    )
    proof["changed_code_receipts"] = [compact_receipt(row) for row in [*coverage_receipts, *receipts]]
    mutated = measurement.mutation_envelope(
        report,
        source_revision=source_revision,
        proof_context=context,
        receipts=receipts,
        root=out,
    )
    observed = source_snapshot.copied_files(tree, expected)
    # Tests may create reports or fixtures inside their owned scratch tree.
    # They are not delivered inputs. Every actual workspace input must retain
    # its bytes, and the workspace itself must remain exactly as measured.
    custody_gaps = [
        f"{name} ({phase})"
        for phase, inputs in (("before checks", baseline), ("after checks", observed))
        for name, digest in expected.items()
        if inputs.get(name) != digest
    ]
    if (
        source_snapshot.inventory(workspace, paths=source_paths) != expected
        or source_snapshot.snapshot(workspace, paths=source_paths) != original_workspace
    ):
        custody_gaps.append("original workspace changed during checks")
    if custody_gaps:
        # A test changed the measured inputs. Preserve every observation but
        # grant no execution or mutation authority from this tree.
        coverage = {"source_revision": source_revision, "proof_context": context, "files": []}
        mutated = {"source_revision": source_revision, "proof_context": context, "files": []}
        inventory = None
    texts = line_texts(tree, inventory)
    assessment = policy.assess(
        inventory,
        coverage,
        mutated,
        source_revision=source_revision,
        proof_context=context,
        texts=texts,
    )
    if custody_gaps:
        assessment["reason"] = "Changed-code input custody failed: " + ", ".join(custody_gaps[:20])
    proof["changed_code_inputs"] = {
        "inventory": inventory,
        "coverage": coverage,
        "mutation": mutated,
        "command_outputs": [receipt["output"] for receipt in proof["changed_code_receipts"]],
        "custody_gaps": custody_gaps,
        "line_texts": texts,
        # Recorded so a reviewer sees what the inventory left out, and why (#3164).
        "installed_dependencies": installed,
        **({"test_selection": report["test_selection"]} if isinstance(report.get("test_selection"), dict) else {}),
    }
    proof["changed_code_evidence"] = assessment
    return mutation.final(report, proof["verdict"], proof.get("case_tests"))


def line_texts(tree, inventory):
    """Each inventoried file's changed-line texts, read from its exact measured bytes in the tree (#2977).

    A file that cannot be read, or whose bytes no longer match its inventoried hash, has no entry: its
    obligations then carry no source_line, and their review is checked by handle alone, as before.
    """
    result = {}
    root = Path(tree).resolve()
    for row in inventory if isinstance(inventory, list) else []:
        try:
            data = measurement.source(root, row["path"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        entry = policy.line_texts(data, row.get("sha256"), row.get("lines") or [])
        if entry is not None:
            result[row["path"]] = entry
    return result


def selected_tests(framework, result):
    """(package, command, tests): the proof command the mutation check runs, and the passing tests it
    observed on the candidate, which every mutant must then fail.

    The regression command is preferred: its independently observed passing tests target the change.
    The suite remains the fallback when the regression command cannot select observed tests. When
    neither can be narrowed, the suite is returned and the mutation check records that it ran no test.
    """
    name = (result.get("framework") or {}).get("name")
    commands, checks = result.get("commands") or {}, result.get("checks") or {}
    choices = []
    for key, label in (("regression", "regression_on_candidate"), ("suite", "suite_on_candidate")):
        package, command = package_scope.split(framework, commands.get(key))
        tests = sorted(((checks.get(label) or {}).get("results") or {}).get("passed") or [])
        choices.append((package, command, tests))
        narrowed = proof_stability.narrow(name, command, tests) if command and tests else None
        if narrowed and python_tests.parse(narrowed, include_owned=True) is not None:
            return package, command, tests
    return choices[-1]


def reviewed(proof, exceptions):
    """Reassess using only exceptions admitted from an independent source-bound report."""
    inputs = proof.get("changed_code_inputs") or {}
    original = proof.get("changed_code_evidence") or {}
    result = policy.assess(
        inputs.get("inventory"),
        inputs.get("coverage"),
        inputs.get("mutation"),
        source_revision=original.get("source_revision"),
        proof_context=original.get("proof_context"),
        exceptions=exceptions,
        texts=inputs.get("line_texts"),
    )
    if inputs.get("inventory") is None and inputs.get("custody_gaps"):
        result["reason"] = "Changed-code input custody failed: " + ", ".join(inputs["custody_gaps"][:20])
    if mutation_checkpoint.FAILURE in inputs:
        failure = inputs[mutation_checkpoint.FAILURE]
        reason = failure.get("reason") if isinstance(failure, dict) else None
        result = {
            **result,
            "status": "UNVERIFIED",
            "reason": "Mutation refresh did not establish a new measurement: " + str(reason or "reference unavailable"),
        }
    return result


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
    if mutation_checkpoint.FAILURE in inputs:
        failure = inputs[mutation_checkpoint.FAILURE]
        if not isinstance(failure, dict) or not isinstance(failure.get("command_outputs"), list):
            return False
        if not all(isinstance(output, str) and output for output in failure["command_outputs"]):
            return False
        required.update(failure["command_outputs"])
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


def cited_mutation_pins(proof, refs, *, source_revision):
    """Authenticate cited kills against the exact saved proof and capture receipts."""
    cited = set(refs) & policy.mutation_citations(proof).keys()
    if not cited or proof.get("source_revision") != source_revision:
        return {}
    try:
        root = Path(proof["path"]).parent
        result = json.loads(measurement.pinned(proof["path"], proof["receipt_sha256"], root))
        if result.get("source_revision") != source_revision or result.get("changed_code_inputs") != proof.get(
            "changed_code_inputs"
        ):
            return {}
        pins = {}
        seen = set()
        for row in result["changed_code_receipts"]:
            output = row.get("output")
            if output not in cited:
                continue
            if output in seen:
                pins.pop(output, None)
                continue
            seen.add(output)
            if (
                type(row.get("exit_code")) is int
                and row["exit_code"] != 0
                and not row.get("timed_out")
                and not row.get("interrupted")
                and measurement.known_capture(row, root)
            ):
                pins[output] = row["output_sha256"]
        return pins
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def ready(proof, result, validation):
    """Current immutable measurement plus still-pinned independent reasons."""
    if not intact(proof, result):
        return False
    exceptions = (validation or {}).get("changed_code_decisions") or []
    if exceptions:
        pins = validation.get("evidence_hashes") or {}
        if (
            validation.get("source_revision") != proof.get("source_revision")
            or validation.get("reviewer_role") not in ("tester", "task_planner")
            or not pins
            or any(not Path(p).is_file() or util.file_hash(p) != h for p, h in pins.items())
        ):
            return False
    return reviewed(result, exceptions).get("status") == "PASS"
