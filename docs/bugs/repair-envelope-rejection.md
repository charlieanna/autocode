# Repair envelope rejection

Issue #2347

A malformed first `report_only` repair envelope previously caused the next attempt to offer preferred text-edits, even though `expand` rejects reconstructed repair envelopes. A deterministic malformed repair-response marker now declines this offer, falling back to the original complete-report schema. All provenance, schema and evidence gates remain.

**Changes:**

- `autocode_report_source.repair_report_source`: adds `repair_response=True` metadata for `format=text` AND `record.report_only`
- `autocode_report_edits.eligible`: declines text sources with that marker

**Limitations:**

- Does not fix invented requirement quotes or coordinator obligation exclusions; attempt6 remains unqualified.
- Live acceptance is not established.
- Preexisting failures in final Linux CLI module: 71 total tests with 3 object-mode failures; same 3 failures reproduce unchanged on master 27fa (4 CLI tests, 24.630sec), while text-mode CLI case passes both.

**Regression evidence:**

- Two expected failures before two runtime lines.
- 67 focused tests passed.

Preserves JSON full report edits, initial malformed report text edits, and all normal validation.
