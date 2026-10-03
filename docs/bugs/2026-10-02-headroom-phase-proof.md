# Actual application phase isolation

Issue #225 identified synthetic stats credentials becoming an undeclared
input to later compatibility startup. The existing maintained phase harness
only populated generic `PHASE_*` roots; Headroom reads `CLAUDE_CONFIG_DIR`.
Its synthetic catalog consumer therefore could pass without establishing
isolation for the real application.

`PhaseSequence` now snapshots a complete prepared environment before creating
roots. Each phase binds named application variables to its owned credential,
config, state and cache roots. Ambient account variables can be omitted from
acceptance children without mutating the provider parent or rewriting HOME.
The new opt-in HTTPX and socket observers span imports, pytest collection,
setup, call and teardown. They record refusals before I/O (including malformed destinations and
undeclared service-name ports) and preserve the existing declared-loopback policy. A caught exception and zero pytest exit
cannot make the aggregate observer green.

The opt-in `scenarios/headroom_phase_check.py` qualifies this seam with a
frozen local Headroom package at `5bf661232667fe2573caafcd9b48f86c6388b5bd`,
using its existing Python 3.12.14 runtime and actual `_core.abi3.so` (SHA256
`c97748ba17e043d4caf1b239f22b304111d46e2b922f45e3520c636a0bd5b71b`).
All 560 declared package/SDK inputs are inventoried; application source, SDK,
parent environment and evaluator helpers are checked before and after.
The actual imported package, server, subscription client and compiled SDK
paths/hashes must match those frozen inputs; installed-package fallback fails.
No originals, private fixtures or provider credential/config files are copied
or edited. Only synthetic credentials are written under fresh owned roots.

Stats uses real Uvicorn/create_app startup, a loaded Rust core and an HTTPX
`/stats` request. A stats-only in-memory usage-URL adapter directs the production
subscription client to an owned synthetic loopback server. Compatibility uses
the production default usage URL unchanged and exercises two real TestClient
lifespans under actual pytest. Packaged LiteLLM metadata and documented offline
settings avoid unrelated import-time downloads; they do not admit remote
traffic. The first attempted reference hit that import-time metadata request
and remains a retained ERROR receipt.

Independent reference, original-unbound and deliberately shared-root controls
run in separate fresh directories. The reference has zero compatibility usage
requests. The original and shared-root controls each retain two refused
default HTTPX GETs, although both pytest cases pass. Swallowed-request controls
at collection, setup, call and teardown each retain a refusal and stay ERROR.
The existing catalog's original/broken functional oracles also remain failing
while its reference passes.

Native model qualification uses the public TaskRun interface: the Builder
repairs an owned `phase_policy.json`, then runs the original test against the
real app/SDK evaluator. Planning and review use offline fixtures; this is a
scoped native Builder comparison, not a complete live production build. Each
paid dispatch is preceded by actual original-FAIL/reference-PASS preflight.
Original assertions are protected, independently replayed and preserved.

One initial GLM attempt found an omitted `--python` argument in the fixture,
so that batch stopped before other models ran. The failed attempt is retained.
In the corrected batch GLM and OpenAI completed their scoped runs. Both MiMo
attempts produced the correct repair and passed the real test, but reached
their 180-second and 300-second stage caps before the final report. Those run
receipts remain paused and unaccepted. Independent artifact qualification
passed for all three model-generated policies against the final evaluator; a verified
policy does not establish completion of the MiMo runs. No additional paid
attempts are needed for this harness fix.

This establishes the maintained environment/observer seam against a new frozen
local runtime. It does not repair or relabel the historical V2 ERROR, establish
the private #3913 product candidate's acceptance, or replace the missing
full-overlay/PyO3 qualification tracked in #223. Raw receipts, logs and copied
inputs stay in ignored `.scenario-runs/`; only this note and the evaluator
contract are committed.

Final bound qualification: seven production controls matched their expected
results and all three model-generated policy artifacts passed. The combined
receipt SHA256 is `8fe82d01af5b2f33a428bb6f1501a9e402437a52973c636a7d4594c60db46947`;
its ignored local path is
`.scenario-runs/phase-isolation-proof/final-bound-31a18fba48/qualified-receipt.json`.
Local gates passed: 14 changed/architecture tests, 30 phase/catalog tests,
118 full harness tests, and the fake catalog (53 PASS, one NOT_EXERCISED, one
expected live-only SKIP).
