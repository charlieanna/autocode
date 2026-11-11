# Project notes

Every run used to rediscover the same facts about a repository: how to reproduce
a failure, which test flakes, which quirk the build has, which module owns what.
The Investigator often pays to learn one of them when a stage gets stuck, and the
knowledge ended with the run. Project notes keep those facts for the next task in
the same project (#2943).

The notes are `.autocode/project-notes.json`, next to the learned
[project memory](project-memory.md) record. They are advisory context, the same
standing as documentation a person maintains: they never approve work, grant a
permission, name a path or remote to act on, or become a requirement. The current
brief, configuration, answers and the repository as it is now win over a note.

## How a note is learned

A note is written only with a receipt: the run, the stage and the observation that
produced it. There are two writers: the Investigator, when a stage gets stuck, and the
completion path, when a run completes.

### From the Investigator

The [Investigator](workflow.md#when-a-stage-stops-making-progress) may name up
to three facts in its report's `project_notes`, each with a `kind`
(`reproduce_command`, `test_command`, `flaky_test`, `build_quirk`, `ownership` or
`other`) and a sentence or command as `text`. The runner keeps them only when the
report's probe, which it ran over the files the report cited, showed the diagnosed
cause and failed on its counterexample. That probe is the receipt's observation. A
report with no passing probe keeps nothing, and its investigation records why.

### When a run completes

The runner sometimes proves the same code twice: the Completion Owner replays a proof
that failed only once, and a Builder that changes nothing is proved again. When one
of the run's regression proofs failed a test and a later one passed it, on the same
source revision and the same proof context, the test's outcome did not follow the
code. Once the completed run is saved, each such test, up to three, is kept as a
`flaky_test` note: "`<test>` is flaky: it failed and then passed on unchanged code, so
one failure of it alone does not show a regression." Its receipt's stage is
`regression_proof`, and its observation names the two proofs. The runner reads only
its own proof receipts in the run folder, never model text, and counts only the
candidate's checks whose results were complete. The run's
`project_notes.completions` records, for each completed proof, the notes written or
why there were none.

### Writing the file

The notes reach the project file after the investigation, or the completed run, is saved. A note that holds
a credential-shaped value or a local absolute or private path is omitted whole, never
rewritten. A failure to write the file is recorded on the investigation and never
stops the run. The same fact, by kind and text, is one note however many runs learn it.

## How a note is used

A fresh task freezes the project's active notes at launch. Requirements and the
Planner, and the Investigator if the run gets stuck, are given them as
`project_notes` in their handoff, with the rule that they are advisory and are to be
checked against the repository. A task already running keeps the notes it started
with, so a correction reaches the next task, not this one.

The status view's `project_notes` shows the notes the run was given (`notes`), the
notes it learned (`learned`, from its Investigator and its completion), each with its receipt, and `next_command`, the
argument array that lists the project's current notes
([status view](schemas/status-view.schema.json)).

## Correcting a note

```sh
autocode notes list --workspace /path/to/project
autocode notes retract note-0123456789abcdef --reason "The flake was fixed in #123"
autocode notes correct note-0123456789abcdef --text "Run make test-unit; make test needs Docker"
```

`retract` marks a note wrong. It stays in the file as retracted, so a later run that
learns the same fact does not bring it back over your correction. `correct` retracts
the note and adds your text as a new note whose receipt names your correction. `list
--all` shows retracted notes too. `--json` returns the view clients read: each active
note carries `actions.retract`, an argument array taken as it is, and
`actions.correct`, which a client completes with the person's text as its last
element. These commands are operator-only through the unattended wrapper.

The file keeps at most 64 notes; when it is full the oldest active note goes first,
so retractions outlive the notes they correct. Like project memory, it is read
without following links and replaced atomically under the same lock, and an invalid
file is reported and ignored, never overwritten.
