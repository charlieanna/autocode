# A narrowed package test script could still pass the proof (#528)

`npm test` (and yarn/pnpm) runs each scratch tree's own `package.json` script.
The command text stays `npm test --silent`, and that command reports no
per-test results, so a green exit was treated as preservation. A candidate
could point `scripts.test` at only its new test and hide a broken old one.

An exit code now proves preservation only when that script is unchanged. A
redefined script is UNVERIFIED and the proof says so. A document-only base
still has no old behavior to preserve, and a `package.json` edit that leaves
`scripts` alone still compares the exit codes.

Superseded: #625 replaced the script-string compare by running the base
suite definition over the candidate's code, and #652 closed the gaps that run
still had (2026-10-07-base-definition-gaps.md).
