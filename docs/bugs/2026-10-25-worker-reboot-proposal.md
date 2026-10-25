# Proposed literal Linux guest reboot qualification (#1881 C4, #1886 S05)

Status: reviewed for qualification-only publication; literal guest execution
remains unverified. The pure oracle/admission tests do not qualify a reboot. Keep #1881 open until a real supported-guest result and
its exact runtime/archive identity are recorded. The existing changed-boot unit
tests remain simulated evidence.

## Exact environment and proposed action

The manual-only `worker-reboot-c4` workflow creates one new QEMU guest inside a
GitHub-hosted `ubuntu-24.04` x86_64 runner. It refuses self-hosted, non-Linux,
non-x86_64 or root callers. The guest accelerator is explicitly TCG, using
`-machine q35 -accel tcg,thread=multi -cpu max`; there is no automatic accelerator
fallback. KVM availability is recorded as diagnostic metadata, not an admission
requirement. KVM was a qualification speed preference, absent from the product
lifecycle contract. [QEMU system emulation](https://www.qemu.org/docs/master/system/introduction.html)
boots a real guest OS with TCG; this does not substitute user-mode emulation or
a container for the guest. The unchanged runtime still admits only its supported
Linux ABI, process namespace and seccomp-continuation boundary.
It does not change the existing `worker_host_setup.sh` or its CI-only guard.
No PR, push or schedule event runs this workflow; PR checks exercise only the
pure fake observations in `tests/test_worker_reboot.py`.

The image is Canonical's Ubuntu 24.04 amd64 cloud image from the immutable
[20261017 release directory](https://cloud-images.ubuntu.com/releases/noble/release-20261017/):

```text
https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img
SHA256 04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94
```

The pin was read from Canonical's published
[SHA256SUMS](https://cloud-images.ubuntu.com/releases/noble/release-20261017/SHA256SUMS)
on 2026-10-25. The published
[package manifest](https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-amd64.manifest)
lists systemd 255.4 and Linux 6.8.0-147. No disk image was downloaded during
proposal preparation, and no image signature or guest admission has been
qualified. The workflow checks the downloaded bytes against that fixed pin;
the coordinator independently checks it again before starting QEMU.

After the reviewed revision is merged and the coordinating session confirms
execution, the proposed manual dispatch is:

```sh
gh workflow run worker-reboot.yml --ref REVIEWED_PINNED_BRANCH
```

The workflow's exact advancing command is:

```sh
timeout --signal=TERM --kill-after=15s 22m \
  .venv/bin/python -m tests.worker_reboot_vm \
  --image "$RUNNER_TEMP/autocode-c4-noble-amd64.img"
```

The workflow must exist on the default branch for GitHub's manual-dispatch
registration. The named branch must resolve to the merged reviewed commit;
result metadata records the actual checkout SHA. The coordinating session
reviewed the exact environment adjustment, commands and bounds for
qualification-only publication. Dispatch remains deferred until
it confirms the merged reviewed revision. Preparation did not install packages,
download a disk image, boot a VM or reboot any system.

## Resource and time bounds

One QEMU process receives 2 virtual CPUs, 2 GiB RAM and a 12 GiB copy-on-write
guest disk. The 593 MB published image is read-only backing storage. The job
has a 30 minute limit; the coordinator has a 22 minute external timeout.
Individual bounds are 300 seconds for initial SSH, 600 for cloud-init, a
preparation allowance derived from the actual remaining global time, 45 seconds
for the guarded reboot request plus 300 for reconnect, and 45 for each of three
public post-reboot samples. Source transfer and installation have their own bounded
subprocess timeouts. A failed admission, provisioning or timeout is unverified,
never a passing skip, and is not retried automatically.

Only the disposable CI runner installs QEMU/cloud-image-utils and its test
venv. Cloud-init prepares only the new guest: ordinary user `autocode`, its
existing user manager with linger, Python/venv, Git, bubblewrap, libseccomp and
SSH. The narrowly scoped Ubuntu user-namespace sysctl adjustment is inside the
guest, following the existing disposable worker-host qualification. Guest
runtime dependencies use the existing pinned psutil 7.1.0 test convention.
No model account, provider credential or repository-write token is copied.

## Preparation deadline and failure observations

The 22-minute coordinator cap and 30-minute job cap remain fixed. Before starting
preparation, the coordinator reserves these later obligations:

| Obligation | Reserved seconds |
| --- | ---: |
| Reboot request, including two public baseline reads | 45 |
| Reconnect and changed guest boot observation | 300 |
| Three read-only post-reboot samples | 135 |
| Preparation-failure public diagnostics | 45 |
| Exact-child cleanup | 15 |
| Total | 540 |

The preparation SSH allowance is the actual remaining coordinator time minus
540 seconds. Its guest deadline subtracts another 15 seconds for SSH/startup
transport. For example, 485 seconds already spent leaves 295 seconds for the
preparation SSH operation and one 280-second guest budget. This is derived from
measured elapsed time, not a new fixed preparation timeout. No positive guest
budget means no preparation call. `preparation-budget.json` records the assigned
budgets, reservations and elapsed/remaining coordinator time.

The guest creates its one deadline before heavy fixture imports. Setup commands,
public reads, launch receipt inspection, planning decisions, gate observation
and final baseline reads all share it; every subprocess gets at most 15 seconds
or the remaining guest time. The preparation driver uses the same core-named
worker envelope, actions and receipt checks through public CLI calls, with no
independent 30/120/180-second helper allowances. A lost advancing reply ends the
attempt; inspection never becomes permission to redispatch it.

At most 64 allowlisted `C4_PROGRESS` JSON records go to stderr, with one slot
reserved for a terminal failure. Records show fixture phase, elapsed/remaining
budget and changed public status/stage/attempt summaries. They contain no prompt,
raw provider prose, command, environment or authority token. Stdout remains the
final baseline JSON. On preparation failure, including `TimeoutExpired`, the
coordinator retains only these bounded trace records in `prepare-progress.json`.
A failed operation is identified by safe phase/result/exception class, not its
potentially sensitive error body.

Before exact-child cleanup, a preparation failure gets at most 45 seconds of
public diagnostics, only while sufficient global time remains after reserving
cleanup. That window includes a 15-second SSH transport margin; its guest reads
share the remaining budget, with a 15-second per-read maximum. It reads status,
then the current launch's inspection when available, otherwise public launch
discovery. It does not require the late baseline handle or built files, read
private run state, submit an action, release a gate, approve, recover or retry.
`prepare-diagnostics.json` retains whitelisted nested summaries and explicit
unavailable/timeout outcomes. Missing time or failed diagnostics still lead to
cleanup, never another launch. An external global timeout does not start a new
diagnostic window.

The reboot request's 45-second allowance is separate from its full 300-second
reconnect allowance. Both and the inspection window are bounded by the remaining
global deadline; SSH probes cannot overrun their own remaining phase time.
UUID, active-baseline, boot-identity and no-replay checks stay mandatory.

## Admission diagnostics before setup

The initial step runs only the standard-library command:

```sh
python -I tests/worker_reboot_vm.py --admission-only
```

It prints the accelerator, named GitHub runner/host identity facts, UID, required
path-presence flags and `/dev/kvm` stat/access metadata. It evaluates every
mandatory predicate and reports each failed name instead of one generic shell
exit. It never dumps the environment, installs anything, creates a VM, reads a
model account or administers the host. A failed predicate exits nonzero before
coordinator installation or image download; Bash pipefail preserves that refusal
while `tee` retains its JSON report.

The workflow creates `.scenario-runs/worker-reboot-admission` with a fresh-only
`mkdir`. This separate early directory cannot collide with the coordinator's
fresh `.scenario-runs/worker-reboot` directory. The coordinator still refuses
an existing attempt directory rather than overwriting earlier evidence.

## Identity, fixture and reboot boundary

The coordinator owns its exact QEMU child, private temporary seed/overlay and
per-run SSH host/client keys. QEMU exposes SSH only on a generated loopback
port. There is no arbitrary SSH target, host filesystem share, inherited guest
terminal or fallback host-reboot command. SSH requires its generated host key.
Cloud-init writes a random instance marker and a root-owned readable copy of
QEMU's actual SMBIOS UUID; both must match the coordinator's UUID. The guest
kernel boot ID must also differ from the runner's real boot ID.

Only Git-tracked source is copied into `/opt/autocode`; no untracked files or
host filesystem mounts are included.
The editable task workspace, launch receipts, fake provider and baseline live
under `/home/autocode/reboot-fixture` on the persistent guest disk. The runtime
and receipts stay outside the editable task workspace. The provider is the
existing offline greeting fixture, with unchanged brief/options, a counted
dispatch journal and a held Tester after Builder edits. The exact fake plan is
approved only inside this explicit fixture; no saved user run or plan is adopted.

Preparation saves public `--status` and `worker inspect` results, attempt and
controller identities, routes, approvals, source/test hashes and the fixture's
provider-dispatch journal hash. The baseline is copied outside the guest before
any reboot. The guest-only reboot entry checks its UUID/marker, distinct boot,
retained baseline and active offline worker before executing:

```text
sudo /usr/bin/systemctl reboot
```

That command occurs only inside the verified guest SSH session. A lost SSH
receipt causes boot inspection of that same guest, never another reboot call.
The same QEMU process and disk must survive, the guest boot ID must change,
and the outer runner's boot ID must remain unchanged. SSH loss alone, service
restart, container restart, fake boot identity or restarting QEMU cannot pass.

## Public post-reboot oracles

The three post-reboot samples execute only `--status` and `worker inspect`.
They never release the fixture gate, continue, launch, approve or recover.
The external coordinator checks each saved sample against the external baseline:

- the old launch is not ready, explicitly reports reboot, retains one dispatch,
  and status reports `boot_matches=false`;
- ownership is checked dead, the worker is not advertised as running, and the
  task claims neither completion nor delivery;
- launch/controller/runtime identity, accepted attempt identities, routes,
  approvals, edited files and provider-dispatch journal are retained. Known
  recorded tokens, requests, costs and duration of completed baseline attempts
  remain identical; the held active attempt may acquire final accounting during
  graceful shutdown, and unknown amounts are not treated as zero;
- the view exposes its ordinary named human recovery boundary, or an explicit
  stale retained-attempt handoff with no action. An unavailable inspection or a
  paused label with no recovery offer does not pass.

A graceful reboot may collect an interrupted result. The oracle does not
invent an uncertain outcome where cleanup provided a known result, or infer
permission to replay an uncertain provider call. Applying recovery is left to
the named human boundary and is not needed to establish this C4 no-replay slice.

Evidence is limited to `.scenario-runs/worker-reboot-admission` and
`.scenario-runs/worker-reboot`: allowlisted admission facts, before/after public
observations, success/failure metadata, the selected accelerator and phase/total
wall durations, exact Git checkout SHA and tools-tree pin, public runtime
identity, source-archive/image hashes, the exact outer command list and
QEMU/serial output. `result.json` is published only after the exact QEMU child
has stopped successfully. Cleanup failure records failure evidence and never
leaves a PASS result. `phase-durations.json` retains setup, boot, provisioning,
source installation, fixture preparation, reboot, read-only inspection, cleanup
and any preparation-diagnostic scopes; unsuccessful phases remain unsuccessful.
The assigned budget is retained separately in `preparation-budget.json`. Private
SSH material, the seed,
overlay and guest disk remain under private `RUNNER_TEMP` and are never uploaded.
Cleanup terminates only the coordinator's exact QEMU child. GitHub disposes the
new runner and its guest storage at the end of the bounded job.

## Retained first attempt

The sole first actual qualification attempt,
[run 40240501353](https://github.com/charlieanna/autocode/actions/runs/40240501353),
used merged revision `4698d44ddd1c0def83dbd22693e4f27214a864f5`. It failed the
initial shell admission step with exit 1. The log did not print predicate values
or execution tracing, so the exact failed GitHub-hosted/x86_64/KVM predicate is
unknown. Coordinator setup, image download and guest boot/reboot were skipped.
This failure remains evidence; it established neither reboot nor recovery.

The TCG follow-up is an explicitly reviewed qualification-environment change,
not a retry of that unchanged revision or an automatic fallback. A TCG guest
attempt is recorded below. The new diagnostics make any next refusal attributable
without weakening hosted-runner, guest identity or native runtime admission.

The sole TCG attempt,
[run 40242703671](https://github.com/charlieanna/autocode/actions/runs/40242703671),
used merged `47ed4b877a9df291b8ea4032afb6728587e74927` and tools tree
`18451e671c080b975a2476f55877261ca0295c6a`. Admission, image verification,
guest boot (124.416s), provisioning (223.414s) and source installation (136.951s)
passed. The complete preparation SSH command then exceeded its former fixed
180-second deadline. Cleanup completed in 0.032s; total coordinator duration was
667.020s. Artifact `12618091075` has verified SHA256
`10bd5e3d3a3ad956bcdcc25167a317e908a10cfa625b958680038665d1edb97e`.
There was no exported baseline, paired boot identity, reboot receipt, oracle
result or PASS. Provider admission and the active preparation substep are unknown.

Source audit found no concrete environment, Git, runtime identity or gate
mismatch. It did find that the former 180-second outer deadline wrapped multiple
independent planning/binding waits and another 180-second gate wait. That could
expire during valid progress, but this artifact cannot prove progress rather
than a stall. The composed-budget/trace/diagnostic follow-up preserves both
failed attempts and does not relabel either as success. No further real guest
execution is authorized before root's final-diff and merged-head review.

## Review and remaining proof

The recorded #1879 lifecycle direction already authorizes this qualification
semantics; no repeated product decision is required. AGENTS.md explicitly keeps
host administration separate, so this proposal confines every setup/reboot to
a new disposable CI environment. The coordinating session reviewed publication;
execution still awaits its confirmation of the merged reviewed revision. It does
not authorize administering the user's Mac or an existing Linux host.

The earlier TCG attempt verified initial guest boot, cloud-init and installation.
Preparation under the composed deadline, native runtime admission, real guest
shutdown/reconnect and the C4 no-replay oracles remain unverified. If any prerequisite fails, retain the failed artifact and
report that limit instead of broadening the host or bypassing admission.
