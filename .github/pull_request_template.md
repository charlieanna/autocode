<!-- Keep it small: one fix or one feature per pull request. -->

## What and why

<!-- What changed, and the problem it solves. Link the issue: "Fixes #123" or "Refs #123". -->

## How I tested it

<!-- The exact commands. The suite gate is `python3 tools/run_suite.py --changed`. -->

```sh
```

## Checklist

- [ ] A test fails before this change and passes after it (for bug fixes)
- [ ] `python3 tools/run_suite.py --changed` passes locally, or I've said which tests can't run in my environment and why
- [ ] Docs updated where behaviour, flags or defaults changed (`README.md`, `docs/`)
- [ ] No test was skipped, disabled or weakened; no approval or evidence check was relaxed
- [ ] Saved-run compatibility: this does not change `state.json`, stage IDs, tokens or evidence records — or the change is described below
- [ ] AI-assisted work is disclosed with a `Co-Authored-By:` trailer, and I have read every line

## State-format or default changes (if any)

<!-- Describe what old saved runs will see. -->

## Changelog

Add an entry to `CHANGELOG.md` under `## [Unreleased]` for any user-visible change (see [Keep a Changelog](https://keepachangelog.com/)). A PR without one is incomplete.
