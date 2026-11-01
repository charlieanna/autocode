# False transport drift across sibling Git workspaces

**Issue:** #2347
**Manifest:** `cdd1553f3f2f466b3995893ba4a6907f5c310609ae5287c26ac16eca6d664b46`
**Discovered:** 2026-10-31

## Symptom

TUI multi-repository coordination paused runs with `PAUSED_TRANSPORT_CHANGED` before any model launch. Fresh sibling Git workspaces had identical executables, versions, global configs, and inline environment settings. Different absolute absent project config paths caused false drift.

## Fix

The v2 comparison (lines 283-289) now normalizes both identities by omitting `None` config hashes before comparing every other identity field. The v1 legacy code path (lines 290-299), inert stub normalization (lines 268-281), exact bytes check, symlink guard, and file existence test remain unchanged.

## Status

Focused regressions pass. New sibling-workspace regression independently fails on base. Fresh clean live TUI qualification remains pending.
