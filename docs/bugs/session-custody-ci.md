# Session custody and keeper cleanup CI failures

The suite failures shared by PRs #1191–#1194 exposed two runtime defects and
test doubles that had not followed the session admission interface.

Verification preparation recorded the owner without its native session ID,
although command supervision authenticated the session-aware launch identity.
Preparation now records that ID. Replay compares the authenticated launch
owner and terminal inventory through the same compact birth-identity projection;
the other custody, receipt and publication checks remain required.

Whole-host PID churn could exhaust three scans even when a command's admitted
session was empty. A scan can ignore a changed PID only after native inspection
proves it foreign. Unclassified departures, vanished additions and denied
inspection remain unknown. A typed transient scan failure gets at most half a
second to produce the same exact proof; keeper deadline and signal threads keep
running. Each scan rechecks leader births and replaces stale observations before
expanding group ownership, so a reused leader cannot restore revoked authority.

The cleanup receipt is written before the keeper exits and releases its checkout
lease. The kill-window regression waits for both within its existing time bound.
Test doubles now implement session inventory and the workspace-aware lifeline
interface without inventing admission evidence.

Focused verification covers preparation/publication success and refusal, fake
clocks and ownership races, real process cleanup with fake providers, and the
original unrelated-process churn reproduction. No live models are needed to
exercise these deterministic process and custody failures.

The next CI run exposed two fixture ordering errors. Pipe EOF can precede the
recorded worker's actual exit, so the uncertain-checkout test observes every
recorded birth disappear within its original shared 15-second bound before
asserting new admission. Its still-live worker refusal remains required.

Harness settings faults deliberately ignore SIGTERM but previously waited only
12 seconds after declaring a 33-second force window intended for nested worker
cleanup. Settings-only fixtures now declare a 0.25-second grace before launch;
provider-stage faults retain the production grace. The saved declaration is
checked and the survivor wait follows its window plus the publication margin.
Foreign-sentinel, negative-exit and ungraded-evidence checks remain intact.

The cancelled-kernel-request fixture also must distinguish signal delivery from
exit. It used to send SIGKILL and immediately acknowledge cancellation, although
the seccomp request could still be live. The keeper correctly refuses that
acknowledgement. The fixture now observes the exact caller birth disappear within
the existing handoff deadline, without reaping the provider's child. Native
request validation, containment refusal and the provider's collected exit code
remain unchanged.

After source tracing became part of the model generation schemas, older fake
providers omitted criterion `sources` and repaired Completion Reviewer
`source_verdict` fields. This prevented planning from issuing an approval request,
or sent a completed run back through report repair. The scripted providers now
follow the requested schema, including report-only repairs whose packets omit
the live contract. They retain supplied citations, failed verdicts and executed
check evidence; schema compatibility does not confer new execution authority.

The remaining source-fidelity failures came from generic scripted plans that
cited whole requests without carrying their supplied input paths or issue/design
identities. The fixtures now restate those actual inputs as constraints and
preserve them during repair. The untracked-only preparation case uses a brief
that matches its files, and the follow-up amendment retains the original exit
code. Prompt fingerprints changed only where source-trace guidance and inventory
were deliberately added; byte comparisons against the prior runtime verified
the unaffected cases. Runtime source-survival checks remain enforced.

A later source-trace merge caught an unsupported boundary and silently disabled
provider containment. Restore the original refusal before any provider or
bootstrap launch. Unsupported hosts or capabilities cannot authorize an
uncontained provider; portable runner-command supervision remains separate.
The one exception is macOS, which has no seccomp: by the owner's decision (#1195) providers
there run under portable supervision with a notice (`autocode_session_boundary.PortableHost`).
