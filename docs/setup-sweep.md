# Setup sweep

A nightly check, with no model, that AutoCode's own setup steps work on real repositories. Language and
layout support was otherwise proven on fixtures of a few dozen lines, and a new language often broke
AutoCode's setup the first time a real project met it. The [real-repo sweep](real-repo-sweep.md) measures
whole runs with models on a few tasks; this sweep covers about fifty repositories across ten languages,
costs nothing, and runs every night.

## What it checks

[`tools/setup_sweep.py`](../tools/setup_sweep.py) fetches each repository in
[`tools/setup_sweep_repos.json`](../tools/setup_sweep_repos.json) at its pinned commit, prepares it as a
person prepares a checkout (its `prepare` commands: a virtualenv, `npm ci`, `bundle install`; AutoCode
itself installs nothing), and then runs the steps AutoCode takes on its own:

| Step | What AutoCode does | Observed as |
| --- | --- | --- |
| `runner` | Detects the project's suite (`autocode_verify.detect_framework`) | The runner's name, or none |
| `base` | Runs that suite on a clean copy of the commit, as the regression proof runs it on the base (`autocode_verify.baseline`) | The suite's health: `passing`, `failing_tests`, `failing`, `broken` or `timeout` |
| `targeted` | Narrows the suite to two tests that passed there (`autocode_proof_stability.narrow`) and runs that on another clean copy | Whether exactly those tests reported, passing |
| `mutation` | The mutation check needs a runner with a mutation source and a working narrowed run | True or false |
| `coverage` | Wraps the narrowed command, or the whole suite's when no test can be named, in the project's coverage tool (`autocode_diff_coverage.prepare`, its own JaCoCo or gcov included), runs it after that command and reads the report as the proof does | The tool, when its report names files of the checkout, or none |

Each entry's `expect` names what each step should show. Any difference fails the sweep. A `note` says
why an entry expects less than everything: a suite whose own tests fail at that commit, a runner that
reports only an exit code, a known gap in AutoCode. A note that starts with "Open:" is a gap in AutoCode
that nobody has fixed yet; the list of them is the sweep's backlog.

## When it fails

The nightly run (`.github/workflows/setup-sweep.yml`) opens or updates the issue "Nightly setup sweep is
red", and a green night closes it. Each language group's artifact holds `results.json`, `summary.md` and
the logs of every step. A difference is one of:

- **an AutoCode defect**: fix it where the cause is shared ([AGENTS.md](../AGENTS.md#fix-the-shared-cause-not-each-case)),
  and change the entry's `expect` in the same pull request, so the sweep shows the fix;
- **an AutoCode improvement**: a step now works where the entry expected it not to. Update `expect`;
- **the repository's environment** (a registry outage, a toolchain the runner image changed): rerun
  the group, or fix the entry's `prepare`. A pin never moves to make a difference go away.

## Run it

```sh
.venv/bin/python tools/setup_sweep.py list
.venv/bin/python tools/setup_sweep.py run --out .scenario-runs/setup-sweep --language go
.venv/bin/python tools/setup_sweep.py run --out .scenario-runs/setup-sweep --only itoa --keep
```

`--keep` leaves each checkout and its clean copies' evidence under `--out`. A run needs the toolchains the
selected languages use (Python, Node with pnpm and yarn, Go, Rust, a JDK with Maven, Ruby with Bundler,
Perl, a C compiler and make; the PHP entries install nothing until AutoCode detects a PHP runner) and
network access to GitHub and the package registries.
The workflow can also be started by hand with a list of repository ids.

Adding a repository: pin its full commit hash, give the `prepare` commands a person would run, run it
with `--only`, and write the `expect` the run should show, with a `note` for anything less than every
step working.
