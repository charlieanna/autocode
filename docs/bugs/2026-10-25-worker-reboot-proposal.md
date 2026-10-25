# Proposed literal Linux guest reboot qualification (#1881 C4, #1886 S05)

Status: reviewed for qualification-only publication; literal guest execution
remains unverified. The pure oracle/admission tests do not qualify a reboot. Keep #1881 open until a real supported-guest result and
its exact runtime/archive identity are recorded. The existing changed-boot unit
tests remain simulated evidence.

## Exact environment and proposed action

The manual-only `worker-reboot-c4` workflow creates one new QEMU guest inside a
GitHub-hosted `ubuntu-24.04` x86_64 runner. It refuses self-hosted runners and
missing readable/writable `/dev/kvm`, with no emulation or container fallback.
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
gh workflow run worker-reboot.yml --ref REVIEWED_MERGED_COMMIT
```

The workflow's exact advancing command is:

```sh
timeout --signal=TERM --kill-after=15s 22m \
  .venv/bin/python -m tests.worker_reboot_vm \
  --image "$RUNNER_TEMP/autocode-c4-noble-amd64.img"
```

The workflow must exist on the default branch for GitHub's manual-dispatch
registration. The coordinating session reviewed the exact environment, commands
and bounds for qualification-only publication. Dispatch remains deferred until
it confirms the merged reviewed revision. Preparation did not install packages,
download a disk image, boot a VM or reboot any system.

## Resource and time bounds

One QEMU process receives 2 virtual CPUs, 2 GiB RAM and a 12 GiB copy-on-write
guest disk. The 593 MB published image is read-only backing storage. The job
has a 30 minute limit; the coordinator has a 22 minute external timeout.
Individual bounds are 180 seconds for initial SSH, 600 for cloud-init, 180 for
fixture preparation, 180 for reboot/reconnect and 45 for each of three public
post-reboot samples. Source transfer and installation have their own bounded
subprocess timeouts. A failed admission, provisioning or timeout is unverified,
never a passing skip, and is not retried automatically.

Only the disposable CI runner installs QEMU/cloud-image-utils and its test
venv. Cloud-init prepares only the new guest: ordinary user `autocode`, its
existing user manager with linger, Python/venv, Git, bubblewrap, libseccomp and
SSH. The narrowly scoped Ubuntu user-namespace sysctl adjustment is inside the
guest, following the existing disposable worker-host qualification. Guest
runtime dependencies use the existing pinned psutil 7.1.0 test convention.
No model account, provider credential or repository-write token is copied.

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
existing offline greeting fixture, with a counted dispatch journal and a held
Tester after Builder edits. The exact fake plan is approved only inside this
explicit fixture; no saved user run or plan is adopted.

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

Evidence is limited to `.scenario-runs/worker-reboot`: before/after public
observations, success/failure metadata, exact Git checkout SHA and tools-tree
pin, public runtime identity, source-archive/image hashes, the exact
outer command list and QEMU/serial output. Private SSH material, the seed,
overlay and guest disk remain under private `RUNNER_TEMP` and are never uploaded.
Cleanup terminates only the coordinator's exact QEMU child. GitHub disposes the
new runner and its guest storage at the end of the bounded job.

## Review and remaining proof

The recorded #1879 lifecycle direction already authorizes this qualification
semantics; no repeated product decision is required. AGENTS.md explicitly keeps
host administration separate, so this proposal confines every setup/reboot to
a new disposable CI environment. The coordinating session reviewed publication;
execution still awaits its confirmation of the merged reviewed revision. It does not authorize administering
the user's Mac or an existing Linux host.

Actual KVM availability on the selected runner, image boot/cloud-init behavior,
guest containment admission, real shutdown/reconnect timing and the C4 oracles
remain unverified. If any prerequisite fails, retain the failed artifact and
report that limit instead of broadening the host or bypassing admission.
