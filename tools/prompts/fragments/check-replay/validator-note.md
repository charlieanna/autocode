
CHECKS IN A PASS: every check in a PASS report must exit 0; the runner re-runs each one in a clean copy and
refuses the report otherwise. To show that something fails as it should (a negative control), write a check
that exits 0 exactly when the failure happens, for example sh -c '! python3 -m unittest tests/test_x.py' or a
test that asserts the error. Never cite a check that exits non-zero in a PASS.
The clean copy is the repository's source, including explicitly approved ignored deliverables; no .autocode/. A check that reads run files
(state.json, regression/proof-*/verification.json) cannot pass there. regression_proof in your handoff is the
runner's own executed evidence: cite its verdict and source_revision directly, never a command that reads it.
Replay uses a clean Git worktree: .git may be a file or a directory. Exclude .git in either form
from product-file inventories; filtering only directory names leaves its worktree pointer file behind.
Never cite a check that runs git status: what it lists is the worktree's state, not the product, and a
program re-runs your checks after your work is committed and merged, where it lists nothing (a grep over
it then exits 1). The runner refuses such a check.
Git metadata is not a delivered product file. Keep the actual source-file and behavioral assertions intact.
The runner also executes explicit commands from the approved verification methods and current_task.validation_plan;
another successful command cannot replace them. Empty Python test bodies cannot establish behavioral coverage.
An explicit planned exit-code expectation is replayed as an assertion: a usage-error probe expected to exit 2
must actually exit 2. Your reported checks in a PASS still need to exit 0 themselves.
When the runner supplies a verification copy (including read-only Codex and configured-provider judging stages), capture commands execute
in that copy of the current source. The copy does not itself impose an OS sandbox; the selected backend's
containment still applies. In native Linux/Kilo containment the authenticated verification tree is
read-only, including directories where a test may create fixtures. Keep source and tests unchanged.
If a check writes beside source, create a fresh writable sibling under tool_containment.scratch INSIDE
the captured command, copy the current source without .git or .autocode, and run the check there.
Use a distinct CHECK_COPY for each check. Replace CHECK_COPY below with a shell-quoted absolute child
of this stage's tool_containment.scratch and CHECK_COMMAND with the actual suite command. The full setup
and check belong in one captured command so clean replay can reproduce them. Never relax the source
protection or count a permission-denied setup as a passing check.
```sh
test ! -e CHECK_COPY && mkdir -p CHECK_COPY && tar --exclude=.git --exclude=.autocode -cf CHECK_COPY/source.tar . && tar -C CHECK_COPY -xf CHECK_COPY/source.tar && rm CHECK_COPY/source.tar && cd CHECK_COPY && CHECK_COMMAND
```
Receipts are still written
to the --output path you give capture. Use repository-relative paths for product files. Each reported check must
include its own setup (for example build and execute in the same command): clean replay does not retain
artifacts from earlier checks. An execution in the prepared copy is still subject to clean-source replay.
Create any extra probe or test fixture INSIDE the command passed after capture's --, using repository-relative
paths. Do not create it with a preceding heredoc, shell command or file tool: capture redirects only its child
command, not your preceding shell operations, which still touch the original repository. Make each check
self-contained so the same fixture setup runs in clean replay. Do not install dependencies or change the
original dependency lockfiles, supplied environments or setup inputs during validation; use the provided
dependencies, or report missing prerequisites as blocked.
Keep every scratch copy and test artefact inside the workspace under .autocode/ (for example .autocode/scratch/,
or tool_containment.scratch when your handoff has one); the runner's changed-file measurement ignores .autocode/.
Capture receipts where your output contract's capture example says. A later repair re-verifies each pin, so
under .autocode/ cite only this run directory, .autocode/evidence/, your own tool_containment.scratch, or
runner-written design captures and inputs: never .autocode/scratch/ or another stage's tool-containment
scratch (the Builder's or an earlier attempt's). The runner refuses a report that cites them.
Never use /tmp, mktemp or any path outside the workspace: the provider sandbox denies external directories
and the whole attempt is lost (a live run paused after three such denials, 2026-10-01).
Probe mixed-type numeric interactions. For staged/transactional operations inject failures after work begins:
assert the public error contract, unchanged persistent state and complete cleanup across failure modes.
