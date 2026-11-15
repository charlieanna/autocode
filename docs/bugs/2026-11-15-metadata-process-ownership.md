# Metadata commands must finish their process families

A model-free Linux Kilo connection check returned `check_failed` while a
descendant remained alive. The preserved readiness attempt used source
`2750d04ce9311d377e2a4300569918aca804635f`, an executable private cache and
read-only existing account mounts. It made no model call. Its generic error
does not identify the backend exception, and neither model availability nor
the cached search helper was qualified by that attempt.

The cause reaches beyond the connection screen. `subprocess.run` collects the
direct command; a helper can survive a successful exit or a timeout. A retry,
cached success or backend fallback can then overlap the previous command's
work. Authentication summaries, model rosters and native policy reads share
this ownership requirement, independent of the backend or command spelling.

A second model-free control tested the proposed sampled process-tree helper
on source `25c7517ab31a878077b010c04446eca8808fabb1`. Before its first sample,
the command had exited and remained unreaped, while a double-forked helper
had entered a different session and been reparented to the private PID 1.
The real capture helper returned success with that worker alive. An unrelated
sentinel survived; the fixture then collected its own worker and sentinel,
and the exact container exited normally. The observation's SHA-256 is
`72c36f8595d8d7bac8e4d2296fe8c10cfd310548cc3c394884648914fe28f314`;
its terminal collection receipt is
`f88e7b201e0364d4d1e5716173af6345c1a81c4c80644cc17e840e8c7bf2be05`.
This exposes a sampling gap, rather than a Kilo-specific timeout value.

The shared runner must own orphan adoption before spawning the command,
retain its exact exit status, and confirm that no children remain before
publishing success or a safely collected timeout. On Linux, an invocation's
private subreaper provides adoption across session changes; the kernel's
`ECHILD` result supplies the terminal family check. These contracts are
documented in [PR_SET_CHILD_SUBREAPER](https://www.man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)
and [waitpid](https://www.man7.org/linux/man-pages/man2/waitpid.2.html).
An uncertain result cannot authorize another attempt, a cached answer, a
different backend or a tool-containment waiver.

The original VS Code status-read deadline fix has separate actual-editor
evidence. Forty subsequent reads did not reproduce the rare 65–72 second
delay, so its underlying latency cause remains open. Metadata process
ownership, current Kilo readiness and the new Connections editor check require
their own qualification; this note does not claim those checks have passed.
