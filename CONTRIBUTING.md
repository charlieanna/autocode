# Contributing to AutoCode

Thanks for your interest. AutoCode is young (first commit September 2026) and
maintained by one person, so this guide is short and honest about what is and
isn't ready. Read the [README](README.md) first for what the project is and the
[reliability priorities](RELIABILITY.md) for what gets worked on first.

## Ground rules

- **Reliability before features.** A reproducible bug fix with a regression
  test is the most welcome contribution. New features need an identified user
  need and an agreed scope *before* code; open an issue and wait for a reply
  rather than opening a large pull request cold.
- **Keep the README honest.** Nothing moves from "planned" to "implemented"
  without the code, a supported entry point, and test coverage. See
  [Keep this README honest](README.md#keep-this-readme-honest).
- **Never weaken a gate to get green.** Do not skip, disable or quarantine a
  test, relax an approval check, or make missing evidence count as passed.
  A pull request that does this will be closed even if everything else is good.
- **Be kind.** This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Set up a development checkout

Requirements: Python 3.11+, Git, macOS or Linux (Windows needs WSL).

```sh
git clone https://github.com/charlieanna/autocode.git
cd autocode
python3 -m venv .venv
.venv/bin/pip install -e .          # installs the one runtime dependency, psutil
.venv/bin/autocode --help
scripts/install-hooks.sh            # optional: run the suite gate before every push
```

Running AutoCode against a real model needs a configured provider (OpenCode,
Codex or a command-tool adapter); see [Install](docs/install.md) and
[Providers](docs/providers.md). **Running the tests does not**: the suite uses
fake providers and temporary Git repositories, makes no model requests and
reads no credentials.

## Run the tests

The suite gate is what CI runs. It discovers the tests, applies the recorded
exclusions in `tests/suite_exclusions.json`, and fails if any non-excluded test
fails:

```sh
python3 tools/run_suite.py
```

The full gate takes 7–8 minutes. While iterating, run one module:

```sh
python3 -m unittest tests.test_goals
python3 -m unittest -k approval tests.test_autocode
```

Dashboard tests (need localhost socket access):

```sh
python3 -m unittest discover -s tools/dashboard/tests -p 'test_*.py'
for f in tools/dashboard/tests/test_*.js; do node "$f" || exit 1; done
```

Known limits of the current suite, so you are not surprised:

- A few tests are macOS-specific (they hard-code `/bin/zsh`) and error on
  Linux; CI runs on macOS. Fixing that is welcome.
- Some tests need OpenCode on `PATH` or an installed `autocode` command and
  error without them.
- The suite writes scenario bundles under `.tmp-autopilot-testkit/`; that
  directory is ignored by Git.

## Make a change

1. **Open or find an issue first** for anything bigger than a typo. Say what
   you plan to do; the maintainer will confirm scope or redirect you.
2. **Branch from `master`.** Any name is fine.
3. **Write the test first** when fixing a bug. The test should fail before your
   fix and pass after. Fixture tests with fake providers are the standard;
   see the existing `tests/test_*.py` modules for the patterns and
   `tools/fake_codex.py` / `tools/fake_opencode.py` for the fake providers.
4. **Keep the change small.** One fix or one feature per pull request. A
   250-line diff gets reviewed this week; a 2,500-line diff may not get
   reviewed at all.
5. **Match the surrounding code.** The codebase is plain Python 3.11 with no
   framework and few dependencies; keep it that way. Match the comment
   density and naming of the file you are in.
6. **Update the docs in the same pull request** when behaviour, flags or
   defaults change (`README.md`, `docs/*.md`).
7. **Run the suite gate** before pushing.

### Saved-run compatibility

People have half-finished tasks in `.autocode/runs/*/state.json` when they
upgrade. A change to the state format, stage IDs, approval tokens or evidence
records must either read the old shape or refuse it with a clear message.
Silently reinterpreting a saved run, or silently treating it as approved, is a
bug. Call out any state-format change explicitly in the pull request.

## Pull requests

Use the pull request template. A good pull request says:

- what changed and why (link the issue);
- how you tested it (the exact commands);
- whether any documentation, state format or default changed.

The maintainer reviews pull requests roughly weekly. Review comments are
requests, not verdicts; push back if you disagree, with reasons.

## Contributions written with AI tools

AI-assisted contributions are welcome; much of this repository was written
that way. The rules:

- **You are responsible for the code.** Read every line before you submit it.
  "The model wrote it" is not an answer to a review question.
- **Disclose it** with a `Co-Authored-By:` trailer on the commit naming the
  tool, as the existing history does.
- **Tests and docs still apply.** Generated code without tests, or with tests
  that don't exercise the behaviour, will be sent back.
- **Don't paste transcripts.** Pull request descriptions and issue comments
  should be written for a human reader.

## Reporting bugs and proposing features

Use the issue templates. A bug report with a saved `state.json` (secrets
removed), the exact command, and what you expected is fixable; "it got stuck"
is not. Existing write-ups live in [`docs/bugs/`](docs/bugs/) and are a good
model.

Security problems: **do not** open a public issue; see [SECURITY.md](SECURITY.md).

## Licence

AutoCode is licensed under the Apache License, Version 2.0 (see `LICENSE`).
By contributing you agree that your contribution is licensed under the same
terms, as described in section 5 of that licence. There is no separate
contributor agreement to sign.
