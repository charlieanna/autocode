
CONCERN ROWS: responses and decisions answer only the concern IDs in the supplied Plan Reviewer report.
Return exactly one substantive row per concern, and an empty list when that review has no concerns.
Do not invent audit/checklist IDs or emit extra decisions for checks that passed. Keep your stage's
required review or investigation; limiting these rows does not narrow that work. Preserve substantive
reasoning and investigated evidence for each real concern. A new blocker still belongs in
contract.open_blocking_questions, even if all supplied concerns are resolved or there were none.
Use the full contract form if the offered revision reference cannot carry that field; never put
questions in its changes object. A final retain_contract reference permits open_blocking_questions
directly. Keep independent observations, source_trace and obligation_decisions in their own
schema-defined fields. Neither an empty list nor resolved decisions authorize implementation;
the person must still approve the exact reviewed plan.
