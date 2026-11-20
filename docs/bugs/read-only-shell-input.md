# Read-only stage shells: feed report-check through a pipe (#3875)

Models in read-only stages called the advertised `autocode report-check ... -`
command with a shell here-document, and Bash failed with "cannot create temp
file for here document" (11 failed tool invocations across two native Kilo runs
on macOS with GLM and MiMo; later attempts that piped the JSON with `printf`
succeeded). The handoff note that advertises the command
(`with_report_check_command` in `tools/autocode_tool_handoff.py`, the only place
any stage learns of it; no `tools/prompts` file mentions it) said only "validate
candidate JSON via stdin" and named no input form, so each model picked one.

Each claim below is marked **observed** (run on this host: Linux, GNU Bash
5.2.21; bubblewrap 0.9.0 was installed later for the containment test) or
**source** (read from the cited file).

## The shared cause

A here-document or here-string can need a temporary file, and whether the
stage's shell can create one depends on a temporary area AutoCode does not
control in every backend. Any file-backed input form a model picks fails the
same way; report-check was only the command the handoff told models to run.

### Which input forms need a temporary file

**Observed.** Each form fed N total bytes to `wc -c` under `ulimit -f 0`
(`RLIMIT_FSIZE` 0, so no process can write file data anywhere) with `TMPDIR`
set to a scratch directory. Every count below is the total `wc -c` read,
including the trailing newline the shell adds:

| Form | 16, 32768, 65535, 65536 bytes | 65537, 70000 bytes |
| --- | --- | --- |
| here-document `<<` | runs | killed by SIGXFSZ, exit 153 |
| here-string `<<<` | runs | killed by SIGXFSZ, exit 153 |
| pipe `printf '%s' ... \|` | runs | runs |
| process substitution `< <(...)` | runs | runs |

(So input that fits the 64 KiB Linux pipe buffer, 65536 bytes, runs, and one
byte more does not.) The failed forms left empty `sh-thd.*` files in
`TMPDIR`, which shows where Bash put them. So on Bash 5.1 or later a here-document
or here-string that fits the pipe buffer is pipe-backed and a larger one is
file-backed (**source** for the version: Bash 5.1's release notes introduced
pipes for here-documents and here-strings smaller than the pipe buffer). On
macOS system Bash 3.2 every here-document is file-backed (**source**: the
Apple `redir.c` and `tmpfile.c` code cited in
[the contained-shell note](2026-11-19-contained-shell-input.md)). A pipe and
process substitution need no file in any of these.

## Each backend

1. **OpenCode and Kilo, under AutoCode's tool containment.** The reported
   Kilo-on-macOS failure is already recorded and fixed on master by
   [the contained-shell note](2026-11-19-contained-shell-input.md) (#3931): the
   scratch `TMPDIR` was writable, but macOS system Bash 3.2 ignores `TMPDIR` for
   here-documents and fell back to the read-only current directory. **Source**:
   `tools/autocode_tool_shell.py` now selects the Homebrew Bash on macOS
   (`HOMEBREW_PREFIXES`), its `environment()` sets `TMPDIR`, `TMP` and `TEMP` to
   the launch's own scratch, and `prepare()` in
   `tools/autocode_tool_containment.py` refuses a shell whose start check (small,
   32768-byte and nested here-string input) fails. **Observed** on Linux with
   bubblewrap 0.9.0: the advertised pipe form runs inside the read-only tool
   containment (`OK (builder)`, exit 0, workspace unchanged).
2. **Claude Code.** **Source**: `tools/providers/claude.py` gives read-only
   stages Bash with Edit and Write disallowed, adds no kernel boundary and sets no
   `TMPDIR`; the shell's temporary area is whatever the inherited environment and
   Claude Code provide, which AutoCode does not own.
3. **Codex read-only.** **Source**: `tools/providers/codex_sandbox.py`
   (`arguments`) runs Codex's own sandbox profile extending `:read-only`, with
   write roots only for `.autocode/evidence` and `.autocode/output`. Whether a
   here-document's temporary file can be created there is decided by that
   provider-owned profile, not by AutoCode.

What remains exposed is therefore the shells whose temporary area AutoCode does
not own (Claude Code, Codex read-only) and any file-backed form a model picks
anywhere, for example a large report sent as a here-document.

## Why no private temporary directory

Under AutoCode's own containment the shell already has a private `TMPDIR`
(above). Giving one to every other read-only stage would mean changing the Codex
sandbox profile, a provider-owned policy, or adding a writable area to Claude
Code stages, which have no AutoCode boundary to scope it; either could weaken
the read-only guarantee for the workspace, which this fix must keep as strict as
it is. A pipe needs no file at all, so it works in every one of them.

## The fix

The shared handoff note now names one file-free form, once:
`printf '%s' '<candidate JSON>' | <report_check_command>`, writing each `'`
inside the JSON as `'\''`, and says not to use a here-document (`<<`) or
here-string (`<<<`) because the shell backs them with a temporary file. Every
stage on every backend reads that note. Report-check itself is unchanged
(#3865).

`tests/test_read_only_shell_input.py` checks the note (once, also after a second
application), runs the filled pipe form under `RLIMIT_FSIZE` 0 with a candidate
containing `it's` (**observed**: `OK (builder)`, exit 0, workspace unchanged),
shows that a 70000-byte here-document cannot run under the same limit, and runs
the pipe inside the read-only tool containment where the host has a kernel
sandbox.

## Still open

Not observed on this host: macOS (Bash 3.2 or Homebrew Bash, and its pipe
buffer size), Codex's read-only sandbox, Kilo and OpenCode runs, and Claude
Code's own shell environment. The containment test skips on a host without bubblewrap
or sandbox-exec. The note guides the model; it cannot stop a
model that still chooses a here-document.
