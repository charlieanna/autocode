# Autocode vs a direct agent — reloaded — 2026-09-24

Same greeting task, run again after reloading Autocode at `d22321e`. The direct side was one OpenCode request to `openai/gpt-5.6-terra` at medium effort, with no Autocode workflow.

Task:

> Build greet.py, a Python 3 CLI in this repository. With no arguments it prints exactly Hello, World! and exits 0. With one argument NAME it prints exactly Hello, NAME! and exits 0. Add test_greet.py using only unittest for both cases. No third-party packages, no network, and no other product files.

## Result

| | Direct agent | Autocode |
| --- | --- | --- |
| Time | 38s | 832s |
| Finished | Yes | Yes, `TASK_COMPLETE` |
| `python3 greet.py` | `Hello, World!`, exit 0 | `Hello, World!`, exit 0 |
| `python3 greet.py Ada` | `Hello, Ada!`, exit 0 | `Hello, Ada!`, exit 0 |
| `python3 greet.py 'Ada Lovelace'` | `Hello, Ada Lovelace!`, exit 0 | `Hello, Ada Lovelace!`, exit 0 |
| `python3 greet.py A B` | exit 2, no output | exit 2, `usage: greet.py [NAME]` on stderr |
| Unittest | 2 tests passed | 3 tests passed |

The two requested commands pass on both sides. Autocode also tests a name containing a space, checks that the specified commands write nothing to stderr, and records an independent validation pass for six acceptance criteria, including the suite run from another directory. The extra-argument usage line is recorded as agent-proposed, not as a requested requirement.

The direct agent is correct for the two requested cases and finishes much faster. Two arguments exit silently, and its tests do not cover a multi-word name.
