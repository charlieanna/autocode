# Assignment no-progress fixture omitted the Investigator

Master CI run `37738004050`, at `8f80ae06f77de66bec0b01c70e67aa3120b80b54`,
failed one of 5,556 tests across 365 modules. The assignment scenario expected
a pause containing `without source changes`; it instead received two rejected
`investigate_stuck_report_repair` reports, each missing `diagnosis`.

The runner correctly routed the unchanged Builder attempt to classification.
The offline `scenario_builder.py` fixture handled only Builder reports and
Builder report repair, so it answered the Investigator with the wrong schema.
The repair branch merely restored `summary`, leaving the wrong schema intact.

The fixture now handles only its `no_change` Investigator request with an
unknown, pause-only classification bound to the runner's supplied failure ID
and pinned evidence. It grants no retry, invents no probe, and performs no
source edits. The scenario retains its original pause, no-progress and
no-completion checks, and checks accepted public classification artifacts,
one Builder attempt, and isolation of the successful sibling. Malformed
Builder report repair is unchanged. This is a red-master test-fixture fix;
it requires no live-model qualification. The original CI failure remains
separate from the follow-up test results.
