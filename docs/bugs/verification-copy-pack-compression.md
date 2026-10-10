# Large verification-copy packs

An original TypeScript Arena attempt paused before independent testing because
`git fast-import` exceeded its unchanged 30-second preparation bound. Its frozen
source has 61,364 files totaling 244 MB. The attempt closed STOPPED; its ten
passing diagnostic oracle checks out of fifteen do not qualify the task.

A separate native probe imported that frozen baseline with the ordinary command
in 22.91 seconds and with `pack.compression=0` in 11.44 seconds. Both retained the
exact original Git tree; the probe did not read the model's working-tree changes.
The baseline therefore need not time out on every invocation. Compression adds
avoidable work and leaves less margin under load.

Verification preparation now disables compression for its disposable source
pack. The same verified copied bytes are imported, with no parent object-store
link. The 30-second bound remains unchanged. Ordinary staging still applies Git
attributes and records modes before creating the baseline commit. Tests retain
binary, symlink, executable and attribute coverage and authenticate the actual
import command, bounded timeout and readable imported source. Git may unpack a
small fixture's objects; the imported representation is not part of the contract.

A fresh real qualification is required. Failed Arena candidates remain final;
this change does not authorize resuming, extending or regrading one.
