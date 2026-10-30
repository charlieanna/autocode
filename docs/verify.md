# Verify an existing change

`autocode verify` checks a change made by any tool, without Requirements,
planning, approval or building. Supply the exact comparison base and your
acceptance criteria. The command refuses missing criteria rather than inventing
what completion means.

```sh
autocode verify --workspace /path/to/project --base origin/master \
  --branch my-finished-change --criteria-file criteria.json --json
```

Omit `--branch` to verify the current commit plus its staged, unstaged and
untracked changes. Use `--patch change.patch` instead to apply a diff to the base
in a private checkout. Verification preserves the original checkout and index.
It creates a fresh candidate and a separate review checkout, retaining the
reports under `.autocode/verification/` by default. `--out PATH` selects a fresh
output directory; an existing destination is refused. Output inside the original
checkout must be Git-ignored, and symlinked output parents are refused, except a
root-owned system link such as macOS's `/tmp`, which is followed to its target
(`/private/tmp`). A refused link is named with the path to use instead. The
`git -c` parameters an editor's terminal exports (`GIT_CONFIG_COUNT`,
`GIT_CONFIG_KEY_n`, `GIT_CONFIG_VALUE_n`) are cleared from verification's own
environment and their names recorded in `verification.json` as
`original.cleared_git_config`. Other ambient Git repository or configuration
overrides are refused by name before any work, with the `unset` to run.
Authenticated review-artifact and checkout-protection support must be installed
before a review starts. Selected input using `.autocode/`, `.autocode-ui/` or
`review/` (including case aliases), `__pycache__/` paths or `.pyc` files is
refused because source/proof snapshots omit runtime and cache artifacts.
Reviewer-created files are refused even when the selected `.gitignore` hides
them; only the captured source and exact runtime/evidence namespaces are allowed.
The independent checkout prevents incidental Python bytecode files, retains the
setup identities, and refuses preexisting cache paths or changed protections.

The criteria file is a JSON object. Inline `--criteria` accepts the same object:

```json
{
  "intended_outcome": "Double every integer",
  "proof_kind": "bugfix",
  "acceptance_criteria": [
    {
      "id": "C1",
      "criterion": "Doubling three returns six",
      "verification_method": "python3 -m unittest discover -s tests -v",
      "human_review": false
    }
  ]
}
```

Every criterion needs its own ID, wording, explicit executable check command and
`human_review` boolean. Commands run from the candidate's root in fresh scratch
checkouts. Use a command that demonstrates the promised behavior. Prose,
empty criteria, duplicate IDs or keys, and nonbehavioral placeholder commands
are refused. Criteria and the exact candidate source are bound to the report.

The independent Code Reviewer uses the existing review workflow. It judges the
selected change against the caller's criteria and cannot repair the candidate.
The runner then executes the criterion commands itself and requires independent
external-change proof. `proof_kind` defaults to `bugfix`, which requires a new
regression test that fails on the base and passes on the candidate. `feature`
requires evidence of newly working behavior; `preserve` checks preservation
without requiring a base failure. These retain the existing proof runner's
checks rather than accepting a model's claim that tests passed.

A PASS also has to satisfy the existing completion evidence predicate. A
blocking review, failed proof, failed check, missing evidence or changed source
cannot pass. A criterion requiring human acceptance remains UNVERIFIED: a model
approval and passing tests do not establish a person's approval.

Verification writes the same canonical `evidence.json` and `evidence.md` format
used by task runs, with kind `verification`, caller criteria, source revision,
runner checks and proof, the authenticated review artifact, model usage and
provenance. It reports verification of the external change, without creating a
plan approval or declaring an ordinary task run complete.

```sh
autocode verify --record /path/to/verification-output --json
```

Reading a saved record performs no model call or check command. It rechecks the
source, index and evidence pins. Later source or evidence changes make the
verdict UNVERIFIED while retaining the original report for inspection.

Select the review provider with `--engine codex|opencode`, optional `--provider`,
`--model` and `--reasoning-effort low|medium|high|xhigh|max`. OpenCode is the default.
`--evidence-provenance fake|live|unknown` labels the supplied provider evidence;
fake results do not establish real-model effectiveness. `--suite-command`
selects the project suite used by the external-change proof; otherwise the
existing runner detects the project framework and derives regression commands.
Time limits are `--check-timeout` per runner command (default 900 seconds),
`--max-seconds` for the review (900), and `--max-stage-seconds` (600).
All must be positive. Provider setup is described in [Providers](providers.md).

Coordinated reviews use `--related-profile PATH` with its exact
`--related-profile-sha256` to retain saved routing and provenance. These paired
options refuse explicit `--engine`, `--provider`, `--model`, `--reasoning-effort`
and `--evidence-provenance` flags. Time and cost caps can still bound the review;
the review keeps its single-iteration limit and disables adaptive planning.

Exit codes are 0 for PASS, 2 for FAIL or UNVERIFIED, and 4 for an invalid input or
refused operation. `--json` prints the verdict and canonical evidence metadata;
input refusals are JSON on stderr. Normal output prints the verdict, retained
record path and reasons.
