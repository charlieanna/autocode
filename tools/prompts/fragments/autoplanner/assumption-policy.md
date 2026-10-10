
ASSUMPTIONS. Each proposed_assumptions entry is {id, text, kind, category, convention_ref,
rationale, supports}. Give it a stable id (A1, A2, ...) and keep ids across refreshes.
Use kind="inferable" with a convention_ref (repository path:line, or a saved event id) and a
rationale that establish the convention. supports lists the requirement ids it underpins.
Never mark cost, quota, permission, external_side_effect or requested_outcome inferable; ask a
decision question instead. When previous_requirements_handoff exists and you drop one of its
requirements, list it in ignored_requirements as {requirement_id, reason, basis, event_id}
citing the saved user answer or feedback event that authorizes it; otherwise use [].
