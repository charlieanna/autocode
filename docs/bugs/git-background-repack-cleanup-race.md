# Git's detached repack races test fixture cleanup

`tests.test_protected_test_links` failed intermittently on Ubuntu CI with every
assertion passing: `TemporaryDirectory.cleanup()` in `test_verify.Project.close`
raised `OSError: [Errno 39] Directory not empty` on `'.git'` or `'info'`
(`.git/objects/info`). Runs 37298732915 and 37332183338 (2026-10-05), both
before the fixture turned automatic maintenance off.

The writer is Git, not AutoCode. Every `git commit` starts `git maintenance run
--auto --quiet --detach`, which daemonizes. Git 2.55 (CI's version, from the
git-core PPA) defaults to the geometric strategy. Its `geometric-repack` check
estimates loose objects as 256 times the count in `objects/17/`, and runs once
that exceeds 256. So two loose objects whose ids start with `17` are enough to
start `git repack -d -l --cruft --write-midx` in the background. The repack
writes `objects/pack/tmp_pack_*`, `objects/info/packs` and `info/refs` while
`rmtree` is deleting the tree. Homebrew Git 2.52 runs no task in the same state,
so it never reproduced on a Mac.

This module was the one hit because its oracle blob (`ORIGINAL`) always hashes
to `17ea5d7a…`. Each `setUp` therefore needs only one of its two commits (their
ids depend on the clock) to land in `17/`. That starts the repack in about 2 of
256 tests, or 7% of module runs. The test fails only if the repack is still
writing during cleanup. AutoCode's replay is not involved: `run_command` kills
each test command's process group, and `git worktree add/remove/prune` never
start maintenance.

To reproduce, use Ubuntu 24.04 with `ppa:git-core/ppa`. Build the seed, set
`GIT_COMMITTER_DATE` so the `links` commit's id starts with `17`, commit, and
clean up after a random delay of up to 20 to 100 ms. 2 of 45 trials raised Errno
39, leaving `tmp_pack_*` or `tmp_idx_*` behind. With the fixture's config, none
of 50 did, and no maintenance child started.

`Project` sets `maintenance.auto=false` and `gc.auto=0` before its first commit.
With those set, Git starts no maintenance process at all. `ProjectFixtureTests`
checks that through `GIT_TRACE2_EVENT`.

Dozens of other test modules committed in temporary repositories without this
config. `tests/__init__.py` now sets the same two values for every test through
`GIT_CONFIG_COUNT`. That outranks every config file and reaches every git
process a test starts, AutoCode's included. `tests/test_git_maintenance.py`
checks a commit in a fresh repository. With every git process in the suite
traced on Git 2.55, 132 of 291 modules started maintenance 1,935 times before
the change, and none did after. Two cases set the config in the repository
instead. Git drops `GIT_CONFIG_COUNT` when it starts `receive-pack` for a push
into a local repository, so `test_issue`'s bare remote sets it itself. A
subprocess given an environment built from scratch would need it too; none in
the suite runs git. The scenario harness tests and the dashboard tests do not
import `tests/`; the harness turns maintenance off for its own fixtures, as
below.

The same writer broke a copy. In master run 37349094592 (2026-10-05) two
scenario-harness tests failed in `setUp`: `shutil.copytree` of the diagnosis
fixture that `setUpClass` had just committed raised `No such file or directory`
on `.git/objects/maintenance.lock`, which maintenance removed mid-copy. The
harness's `materialize` now gives its seed commit `-c maintenance.auto=false -c
gc.auto=0` without changing the scenario project's configuration, so live runs
keep Git's default. Fixtures that commit again and are then copied or deleted
call `harness.project.without_maintenance`. `FixtureMaintenanceTests` in
`scenarios/test_harness.py` checks both through `GIT_TRACE2_EVENT`.

The "none in the suite runs git" claim above did not age a day: #538's
verification copy (`autocode_verification_copy.create`) builds its git
environment from scratch and runs `git init`/`add`/`commit` in it, so the
suite-wide `GIT_CONFIG_COUNT` never reached it. In master run 37482744577
(2026-10-06) the commit's detached maintenance repacked under `.git/objects`
after the manifest walk, and `execution()` read the change as `Verification
directory changed: .git/objects/a6`. The copy now writes `maintenance.auto=false`
and `gc.auto=0` into its own repository config — the third case to set them
there — and `tests.test_source_snapshot` probes that config with an environment
in which only the repository's own config can answer.
