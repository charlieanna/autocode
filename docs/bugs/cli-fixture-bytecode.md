# CLI fixture calls timed out on a loaded host (#2997)

`tests.test_workflow_pin` and the other `SubprocessFlow` tests intermittently hit
their 240-second per-call deadline on a heavily loaded Linux host. The stdout tail
ended at whichever prompt the run had last flushed (the clarifying question in one
attempt, the plan approval in another), which read like a stall on supplied input.

## Cause

Nothing stalled. Each call was slow, and the place it was killed moved with the
load. The fixture runs its CLI children with `PYTHONDONTWRITEBYTECODE=1`, so that
nothing is written into the checkout or the fixture workspace. As a result every
child compiled the roughly 560 AutoCode modules it imports from source, about
10 MB, before printing anything. Unloaded, `import autocode` costs 1.5 s cold
against 0.26 s from bytecode. A run starts the CLI several times, so this
repeated work grew with the host's load.

Reproduced on 4 cores with 200 busy loops (load average about 200), one
`Build greeting --chat` call with the fixture's `CLI\nyes\n` answers:

| | first line | whole call |
| --- | --- | --- |
| before | 142 s | 325 s, past the deadline |
| after | 52 s | 242 s |

The answers were read promptly in both runs. After each prompt the next stage
started within two seconds.

## Fix

`tests.test_subprocess.compiled_tools()` compiles `tools/` once per test process
into a temporary `PYTHONPYCACHEPREFIX` outside the checkout. The fixture's
children read it and, with `PYTHONDONTWRITEBYTECODE` still set, write nothing.
The deadline, assertions, host admission and approval binding are unchanged.
Unloaded, `tests.test_workflow_pin` went from 43.9 s to 34.8 s (CPU 39.7 s to
30.2 s).

A host loaded far beyond its cores can still exceed the deadline. That is host
scheduling, and the deadline stays where it is.

```sh
.venv/bin/python -m unittest tests.test_subprocess_bytecode   # fails on the old fixture
.venv/bin/python -m unittest tests.test_workflow_pin tests.test_subprocess
```
