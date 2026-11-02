# Failure attribution records

Arena and scenario outcomes remain separate from explanations. New records have
an unknown `failure_attribution` block; old records project the same defaults.
An oracle failure or a model's `CORRECT` diagnosis score does not confirm a cause.

Investigators record evidence for the earliest observed failed handoff, keeping
unknown handoffs unknown. Paths are relative to the Arena attempt directory or
the scenario run directory; pins are lowercase SHA-256 digests of retained files.

```json
{
  "evidence_refs": [{"handoff": "requirements-to-plan", "path": "handoff.json", "sha256": "FILE_SHA256"}],
  "suspected_cause": "The plan may have omitted the required literal.",
  "confirmed_diagnosis": null,
  "regression": null
}
```

Replace `FILE_SHA256` with the file's 64-digit digest. Append a suspicion with:

```sh
autocode arena annotate ATTEMPT --report attribution.json
python scenarios/run.py annotate RUN_DIRECTORY --report attribution.json
```

After independent review, set `confirmed_diagnosis` to the reviewed explanation
and `regression` to an object with `check`, `before` and `after`. Each before/after
reference contains `path` and `sha256`, and points to an evaluator JSON report:

```json
{
  "command": "python -m unittest test_regression",
  "source_sha256": "SOURCE_SHA256",
  "checks": [{"name": "the_named_regression", "ok": false}]
}
```

The named check must be false before and true after, with the same command,
distinct source digests and no evaluator errors. Use real 64-digit source pins
and the exact check name. Append that investigation with `--reviewer NAME`;
`confirmed_by` is recorded from this explicit action, not supplied by a model.

This records the caller's assertion of independent review and validates retained
file pins and the regression contrast. It does not execute commands from the
report, authenticate the reviewer's identity, or certify that an explanation is
scientifically correct. AutoCode never invokes confirmation automatically.

Annotations append separately from completed results and are bound to their
original bytes. Arena `propose` and the scenario result loader recheck evidence
before exposing the latest annotation. Stale or damaged evidence becomes unknown
without changing the original outcome, model diagnosis score or comparison gate.
An invalid latest annotation never restores an older confirmation. Scenario
annotations are kept under `failure-attributions/`; the original `result.json`
remains intact. No annotation authorizes a deployment or promotes a candidate.
