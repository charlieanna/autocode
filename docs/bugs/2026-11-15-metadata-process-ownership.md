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

A further fault injection killed that subreaper after the backend and its
detached worker were ready. The first public connection check reported failure,
but a fresh CLI still offered Check and started another backend while both old
processes survived. Its observation receipt is
`5d38a132ff860e1944f95284c30aa7a4ea235a62fa06aca533586a58f2c3b947`.
An exception in one Core invocation is therefore insufficient admission policy.

Linux metadata admission now retains a private pending record before launch.
The reaper writes its nonce-bound terminal result to a durable private file
after collecting the family. Declared parallel batches retain a separate result
for every command and release only after all of them finish; healthy toolchain
and policy checks keep their concurrency. Route discovery shares the same batch
authority across its three workers, with independent context copies and receipts. Every metadata call and cached-answer
lookup checks that same record under a bounded lock. Missing or invalid completion
evidence remains blocked across CLI exits; elapsed time, helper death and
sampled process absence cannot release it. Connections displays that block
without offering another check or sign-in action. A changed kernel boot may
retire an old record while retaining it as failure evidence.

A further native control on source `32a30e9c` found the corresponding recovery
gap. Killing a metadata helper inside the terminal client's real History reader
left the next public check blocked, even after its enclosing capture had
collected every process. Both one and two enclosing captures failed at that
same assertion; the original check still reported failure, an unrelated sentinel
survived, and collection needed no fallback signals. The baseline result receipt
is `29e67a5ce0ec40d1a0a0faf3595495e79be23434cdad198591de7d3c60982e14`.

Captures now enroll a separate family-only backup with each enclosing reaper,
using private Unix endpoints and authenticated kernel ancestry. A surviving
ancestor writes that backup only after collecting its root status and reaching
`ECHILD`. A fresh Core may use it to recover admission; the failed command still
requires its own result and cannot become a success. Private capture identifiers
do not change preflight cache keys and are withheld from model subprocesses.

Startup has a separate ownership boundary: preparation creates no pending
target. The helper establishes adoption and its endpoint, acknowledges readiness,
and waits. Core then commits pending custody before allowing the target to start.
Target cwd and environment setup happen inside that owner. Missing directories,
capture-file failures and helper startup refusals therefore cannot create a
pending target without a cleanup owner.

Without a surviving owner's valid receipt, an unknown family remains blocked;
elapsed time and sampled process absence grant no recovery authority. The macOS
runner retains its documented sampled cleanup limitation. Native qualification
of the new enclosing-owner and startup behavior is recorded separately from the
earlier checks below.

The original VS Code status-read deadline fix has separate actual-editor
evidence. Forty subsequent reads did not reproduce the rare 65–72 second
delay, so its underlying latency cause remains open. Metadata process
ownership and current Kilo readiness require their own qualification. The
Connections deadline, duplicate refusal, late-result discard and fresh-result
recovery were observed in the actual extension on source `4576a3ce`; all its
readers and native processes were collected. Both real Kilo checks returned
`sign_in_required`, so that combined readiness attempt remains failed. It does
not establish that saved credentials are missing: auth-list startup errors must
be distinguished from a successful empty list.

A subsequent model-free comparison on that same source identified the failed
setup: Docker had created Kilo's data directory as root, and both version and
auth-list commands exited 1 with `EACCES`. With private writable runtime
directories and identical read-only account mounts, version returned 7.8.3 and
auth-list returned six saved entries. Both namespaces were collected without
fallback signals. Its terminal observation receipt is
`de74b9c56728efd95993637ebf3443bfccd76ad23ca88cb6ea2196243423dd7d`.
This qualifies local account discovery, not key validity or model access. Core
now reports every nonzero Kilo/OpenCode auth-list exit as an operational failure;
a successful empty list still means no saved login was confirmed.

The final saved-credential check on source `0ca0c609` passed through the actual
VS Code extension: its named Check returned `credentials_present`, and the
panel showed “Saved credentials found”. All thirteen public readers and their
transports finished naturally before collection; the private namespace was
quiet with no fallback signals and the exact container exited 0. Its final
receipt is `73c1ce602cfae60c61592ef01793e034c61ae75e103ceafb01a7d99b02f5af75`.
This verifies local credential discovery and editor rendering; no key validity,
model access or paid task was tested. The earlier failed attempts remain failed.

The separate actual-editor helper-loss check on that source also passed. One
authenticated capture helper was killed after its offline root and detached
worker were ready. Check returned a sanitized blocked result without Check or
Sign in actions; a fresh Core list kept that state while both workers and an
unrelated sentinel remained alive. After seventeen readers finished naturally
and the editor host closed, the fixture collected its known workers. Native
fallback signals remained zero and the exact container exited 0. Its final
receipt is `b150881fec1c46fa274e1a60eec81503bb107e5249fb5aff0d544b263cd42c57`.
