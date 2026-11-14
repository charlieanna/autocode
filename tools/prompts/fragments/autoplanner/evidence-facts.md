
CONTRACT DELTA: contract_changes describes only changes from the current goal_contract revision in this
handoff, not cumulative history. A permission already incorporated into that revision is not a new change:
retain its approved text, cite the saved authorization in the summary, and omit it from contract_changes.
If no protected item changes against the current revision, return contract_changes=[].
Use exact protected item identities: an acceptance criterion ID such as AC1, or the previous verbatim
protected list/permission string. Do not use field labels such as "AC1 verification_method",
"AC8 (new criterion added)", "technical_approach", "M1" or "initial_task" as contract_changes.item.
Allowed draft proof corrections, new criteria and implementation proposal edits need no delta;
return [] for them. This does not authorize changing existing behavior, permissions or approved proofs.
SOURCE CITATIONS: code_refs contains existing repository source paths, optionally :line, never a runner
state file, .autocode/ artifact, cache, or explanatory sentence. state_file is context to read, not source
to cite. Read the workspace_inventory candidates; a citation repair changes citations, not requirements.
SETTLED REQUIREMENTS: preserve literal inputs and outputs from the task, approved design and saved answers.
Create examples that match those literals. The DRAFT EXAMPLE CORRECTIONS rule is the sole exception for
numeric stdout in model-written drafts; other protected criterion changes need a saved user basis and delta.
Verification changes follow the narrow draft-proof policy. Gather remaining decisions before drafting;
do not reopen answered questions or invent extra clarification cycles for report wording.
Internal technical_decision receipts are engineering context, not saved human intent.
Never cite one as user_answer, user_feedback or delegated authority for a protected delta.
Keep existing test names and assertions. A planned case needs a separate new test if matching its id
would otherwise require renaming an existing test; a guard must keep the original coverage as well.
REPLACED TESTS: an existing test that the request itself makes impossible to keep as it is (it calls or imports
something the request renames or removes, or the request asks for that test to go) is listed in supersedes on the
test: criterion that replaces it, as {"file": "<its test file>", "test": "<its own name>"}, one entry for each such
test. Read the test files first and list every test that names what is renamed or removed; list no test that only
has to keep passing, and put no supersedes on a guard. The person approves that list with the plan, and the runner
refuses a change to any existing test that is not on it. A rename with no new behavior still needs one test:
criterion to carry the list: name an existing test that must change with the rename.
