# Complete repair reports use the ordinary validation rules

A Completion Reviewer repair can copy the canonical report's criterion text,
which AutoCode itself fills from the approved contract. The ordinary report
loader accepts this legacy shape only when every criterion matches the approved
text exactly. A full report inside the bounded repair envelope was instead
validated against the generation schema first, rejecting that text before the
ordinary compatibility and evidence checks could run.

The envelope decoder now passes a complete object to the ordinary loader, just
as it passes the object reconstructed by bounded edits. The loader still checks
the full schema, approved criterion IDs and text, provenance and execution
evidence. Malformed patches cannot fall back to complete reports; scalar
reports, unknown fields, missing criteria and altered criterion text remain
rejected. Raw responses remain preserved.

The regression covers the public CLI completing a full Completion Reviewer
repair with exact approved text, and loader-level refusal cases. This corrects
the extra transport failure seen in progressive scenario verification; it does
not grant completion to a scenario whose regression proof is unverified.
