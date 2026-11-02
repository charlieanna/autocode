# Launchers and open chats blocked a run

The legacy process guard (`tools/autocode_legacy_process.py`) scans `ps` for a
Python process whose command names `autocode.py` and this run, and stops with
`PAUSED_WORKSPACE_BUSY` ("Existing run process N is active; leave it
untouched"). It skipped only the checking process and its parent, so three kinds
of process that run no stage stopped a run anyway:

- a Python launcher further up the process tree whose own arguments name the
  runner command, such as a terminal holder (`python3 hold.py ... autocode.py chat
  --run-dir RUN`), a test harness or a supervisor;
- an `autocode chat` open on the same run, in another terminal or left behind,
  although the chat starts an `autocode` child for every action and never runs a
  stage itself;
- any other Python program that merely carries a runner command line.

Found while driving an etcd bug fix through the terminal chat: the chat's own
approval child refused to start, naming the terminal holder two levels up.

The guard now skips every process this command descends from (`own_lineage`,
from psutil, falling back to the parent alone), and settles a flagged process
from its exact argument vector (`not_a_runner`), which keeps the argument
boundaries `ps` text loses: `autocode.py chat` is a chat, `autocode.py "chat
about it"` is a task. The exact vector is used only while it joins to the very
text `ps` listed, so a reused PID or an unreadable process keeps the `ps`
verdict. A second runner, the parallel Builder worker, the controller scripts,
provider commands, `python -c`, `python -m` and unknown interpreter switches
still block.

Focused coverage: `python -m unittest tests.test_legacy_process`; its
`LiveProcessGuardTests` start real processes and fail without the fix.
