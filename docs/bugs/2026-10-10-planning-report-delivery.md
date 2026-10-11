# Planning report delivery after long handoffs

An original Go Arena trial on e18cf660 stopped before implementation: the Gemini
Plan Reviewer returned a chat response and exited without writing its required
report file. The uncertain attempt was retained, and native cleanup completed.

Planning told the model to return its report because the runner would save it,
while the configured command provider required a JSON file. Its delivery contract
also preceded a large handoff.

Planning now defers report delivery to the provider contract. File-report
providers repeat the required artifact path after the schema and immediately
before the handoff, keeping its JSON intact, with reporting
scratch confined to run artifacts and repository source left read-only. Native
Codex persistence and event-output providers keep their transport instructions.
Schema validation, genuine capture evidence, missing-file rejection, and
uncertain-attempt handling remain required. A chat response is not salvaged into
a successful stage.
