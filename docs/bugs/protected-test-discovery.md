# Retained tests entered Vitest discovery (#393)

A live word-counter repair completed with native regression proof and a passing
independent source oracle, but its ordinary `npm test` failed afterward. Vitest
4.1.6 discovered the original tests retained under
`.autocode/runs/<run>/protected-tests/<binding>/src/counter.test.js`. That copy
could not import its neighboring `counter.js`, which was not part of the retained
test inventory. Copying only the delivered project files into a clean directory
made the same command pass. Excluding `.autocode/` from AutoCode's own instrumented
command alone did not fix the user's ordinary test command.

Retained originals now live in an opaque ZIP archive. The logical inventory,
binding hash, modes, link identities and original suite command stay unchanged.
The runner verifies each member before replay and materializes it temporarily
outside the project. A changed candidate test still has to pass the original
assertions against the current implementation.

Idle saved runs compact their own historical and current directory bundles into
verified archive companions without rewriting their approval identity. Archive
publication precedes removal of recorded copies, so interrupted compaction can
resume. Unrecorded files, active runs and other runs remain untouched. Redirected
storage directories are refused before writing or removing originals.

Real Vitest controls reproduce the legacy collection failure, then show ordinary
`npm test` passing after compaction and with a newly retained archive. A deliberately
weakened candidate passes its own tests but fails the retained original suite.
Additional controls cover interrupted compaction, tampering, duplicate archive
members, internal file links and redirected storage directories.

Native Vitest 4 reporting is also available so a plan can name its actual tests
without wrapping another runner in `node:test`. Qualification covers Vitest 4.1.6;
missing or skipped cases do not earn proof, and hook failures do not demonstrate
an application regression. The existing static wrapper guard from #380 remains
in force and does not certify arbitrary dynamically constructed wrappers.

A fresh final-runtime live run through OpenCode used GPT-6 Luna, GPT-6 Sol and
GPT-6.1 Sol. It completed within its original 1,200-second active-time limit,
passed all 256 independent source checks and ordinary workspace `npm test`, and
preserved the original tests and dependency files. Native proof recorded five
fail-to-pass cases and three preservation cases. This qualifies one word-counter
repair and the stated Vitest version, not arbitrary projects or model combinations.

See [protected tests](../protected-tests.md) and
[named test proof](../named-test-proof.md) for usage and compatibility limits.
