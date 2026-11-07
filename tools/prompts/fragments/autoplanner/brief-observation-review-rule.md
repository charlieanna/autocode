
BRIEF DECLARATIONS. brief_declaration_inventory comes from the user's authenticated brief.
Check every supported declaration against the plan. When this report schema includes
brief_observations, cover every supported declaration with an observation: declaration_id,
criterion_ids from the contract, steps with argv, observe_step, and bindings with placeholder,
step and argument. Propose commands that exercise the declared program and match its literal arguments and output;
do not weaken an expectation or invent an observation the brief does not support. Reuse the
existing observation for an unchanged declaration. Changes require brief_observation_changes
entries naming previous_hash, declaration_id and the authenticated source_event_id that
changes that declaration. A review whose schema lacks observation fields reports a
coverage gap as a blocking concern; a reviewer with those fields supplies the observations.

OBSERVATION FORMAT. This is a proposed, runner-executed check, not a check you run during
planning. steps[].argv contains only arguments after the declared program (for example,
["add", "buy milk"], not a shell command). step, observe_step and argument are zero-based
indices. A binding names the placeholder in the source command at that exact argument;
for ["add", "buy milk"], TEXT binds to argument 1. A generated output-only ID needs no
binding unless a later source-declared command supplies it. expect names items that must
appear exactly once; absent names items that must be absent. Use empty arrays when no
such assertion is needed. These entries have the same placeholder/step/argument shape;
they are not literal expected output strings. Literal brackets and status alternatives
come from the authenticated declaration; do not invent a regex or expected-output field.
For a declaration with add TEXT and list printing ID TEXT [open|done], this example is a
valid shape (substitute the actual declaration and criterion IDs; it is not task evidence):
{"declaration_id":"example-declaration","criterion_ids":["AC1"],"steps":[{"argv":["add","buy milk"]},{"argv":["list"]}],"observe_step":1,"bindings":[{"placeholder":"TEXT","step":0,"argument":1}],"expect":[{"placeholder":"TEXT","step":0,"argument":1}],"absent":[]}
An initial review whose schema includes observations may be the final independent review
when it has no blocking concerns, so supply complete coverage there too. A schema without
observations can only report a coverage concern. Do not search runner state or old logs
for additional format rules: use this protocol and the supplied report schema.
