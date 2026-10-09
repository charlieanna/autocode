# Protected test replay (#229)

The public CLI reproduction completed a task with `height() == 20` after the
Builder changed an existing required-height assertion from 40 to 20. Clean
verification copied the modified test, so source isolation alone did not
preserve the original gate.

New code runs retain eligible existing test files, including dirty/untracked
inputs, and the original suite command before model dispatch. After a
protected file changes, independent replay executes the candidate suite and
then the original test files over the same current implementation. Neither
model-written PASS nor another successful reported command replaces this
gate. Added coverage runs in the candidate suite; the original assertions
remain separately executable. Completion requires the current binding and
unchanged executed receipt. Scratch overlays cannot write through a replaced
file symlink into the source checkout.

The CLI controls cover changed expectations, comparing two equally wrong
values, removed assertions, skip, rename and unconditional PASS. The real-fix
control adds coverage and passes both suites. Binding, failed receipts and
original files survive restart. Explicit user revisions retain the previous
bundle and record the exact proposal, old/new identity and rationale. Changes
to ordinary verification commands cannot silently revise this binding.

Old saved runs are not retroactively bound after implementation edits. Start a
new run to establish an original inventory. This guard authenticates the
retained files and executes their command; it is not a hostile-code sandbox,
assertion-semantics classifier or automatic approval of changed requirements.
It does not automatically protect unrelated application files or guess a
framework that was unavailable at creation.

Live qualification uses three capped actual OpenCode Builder stages (GLM 5.3,
MiMo 2.6 Pro and GPT-6 Sol), each repairing the small layout fixture after a
weakened candidate assertion. Planning and model review in these probes use
explicit offline providers; the model edits/check events and runner's
candidate/original replay are real. These are targeted repairs, not three
complete live product builds. Each provider stage is capped at 180 seconds;
evidence stays in ignored `.scenario-runs/protected-tests-live/`.

Initial live-harness attempts stopped before native Builder dispatch because
its offline requirements report omitted the custom brief's source trace and
its answer action omitted the published token. Those records are retained;
they are not product or model failures. The corrected fixture has an explicit
approved layout obligation and sends the answer token through the public CLI.
