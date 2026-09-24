# Autocode vs a direct agent — 2026-09-24

Same task, two real runs. One went through Autocode. The other was one OpenCode request to the builder model, with no Autocode workflow.

Task:

> Build greet.py, a Python 3 CLI in this repository. With no arguments it prints exactly Hello, World! and exits 0. With one argument NAME it prints exactly Hello, NAME! and exits 0. Add test_greet.py using only unittest for both cases. No third-party packages, no network, and no other product files.

Direct agent: `opencode run --model openai/gpt-5.6-terra --variant medium` in an empty Git repository. That is the Builder model and effort Autocode would have used later.

Autocode: `tools/autocode.py` from checkout `29edb5b`, `--in-place --no-chat --reasoning-effort medium`, in a second empty Git repository.

## Result

| | Direct agent | Autocode |
| --- | --- | --- |
| Time | 32s | 412s, then paused |
| `greet.py` | Written | Not written |
| `test_greet.py` | Written | Not written |
| `python3 greet.py` | `Hello, World!`, exit 0 | No program to run |
| `python3 greet.py Ada` | `Hello, Ada!`, exit 0 | No program to run |
| `python3 -m unittest test_greet.py` | 2 tests passed | No tests to run |
| Where it stopped | The agent finished after writing both files and running them | `PAUSED_REPEATED_FAILURE` during planner report repair. Requirement R1 is not covered by a behavior or criterion. Repair was attempted twice. Approval and the Builder never started |

The direct agent also treats two or more arguments as no name, so `python3 greet.py A B` prints `Hello, World!`. The task did not specify that case. The two requested cases pass.

Autocode saved a requirements report and then rejected the planner draft. It produced no CLI.
