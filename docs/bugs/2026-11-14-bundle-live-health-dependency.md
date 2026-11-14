# Evidence export depended on live backend health

A feature-recovery collector on `fb5fdc3a29f114f72c618b056ef5fbb16bddfd34`
timed out its public bundle export after the existing 65-second allowance. Both
output streams were empty and no bundle was published. The retained observer
receipt has SHA-256 `746ac87de89e2ea1270f7f315d6f81ba30ce0559929db577a1390dbdd72da208`;
the failed-retained handoff has SHA-256
`1e67dd8a9be077ab240e97b283486beb22d0befb53b6d218f7e29bbaf11ccf86`.
There is no successful Core bundle manifest for that invocation. Its exact
interrupted stack remains unknown, and the expired run was not retried.

Source inspection found a reproducible dependency: bundle collection always ran
`doctor.all_checks` before publishing the archive. Its Kilo roster check can use
30- and 60-second attempts, exceeding that collector allowance. This explains a
possible failure path, not the proven location of the retained timeout. The
native evidence copy had already succeeded and the public stopped checkpoint
matched before and after that copy.

The bundle now retains `doctor.json` with `status: "not_run"`, `ok: null`, an
empty `checks` list and a reason. Export does not make live diagnostic calls;
the explicit `autocode doctor` command continues to check the host and backend.
The archive keeps its existing retained files, redaction, replay handling,
optional diff and hashed manifest. This change adds no timeout, retry, thread,
completion authority or automatic cleanup.

Deterministic collection and public bundle-CLI controls fail on the original
code because it calls the live diagnostic sentinel. With the change, they
produce the not-run record and a redacted archive whose manifest hashes verify,
while leaving the saved run unchanged. They do not establish the cause of the
original timeout or guarantee that every export finishes within 65 seconds.
