# Qualify shell input before model dispatch

A real Kilo follow-up could not run its local report check: `/bin/bash` said it
could not create a heredoc temporary file. The same failure reproduced without
models for small and large input, short and deeply nested workspaces, with and
without the private-run read denial. Direct writes to the owned scratch worked;
writer shells and a read-only shell whose current directory was scratch worked.

The macOS system Bash 3.2 heredoc implementation requests a system temporary
directory without the flag that reads `TMPDIR`. When the system temporary
directories are not writable, it falls back to the current directory. See
[Apple's heredoc code](https://github.com/apple-oss-distributions/bash/blob/main/bash-3.2/redir.c#L356)
and [temporary-directory selection](https://github.com/apple-oss-distributions/bash/blob/main/bash-3.2/lib/sh/tmpfile.c#L54).
The model's report text was unrelated to this shell limitation.

Native tool preparation now selects the fixed Homebrew Bash installation on
macOS, with its observed loader dependencies granted read-only. The recorded
runtime digest is checked again before launch. A protected directory exposing
only a pinned Bash alias leads the tool PATH for nested Bash calls; every other
tool keeps its original resolution order. Arbitrary project/PATH shell wrappers remain
unqualified. Linux keeps its native Bash. The tool environment and its owned
temporary directory stay scrubbed; no source-write or system-temp authority is
added. The former shell environment construction moves into one focused module.

The previous `exit 0` startup check could not observe this failure. The check
now exercises literal punctuation and Unicode, large stdin and a nested shell
inside the actual boundary, with the existing setup-check deadline and bounded
timeout recovery. It refuses a broken runtime before paid dispatch. The
Homebrew formula and native macOS CI install Bash as a dependency; pip users
have the prerequisite documented.

The new kernel regression fails on the original runtime and passes using the
corrected runtime, preserving the working directory, exact input bytes and
denial of source writes. Ten final shell/startup/integrity checks pass against
the installed Bash 5.3.20; an earlier 59-test policy/installer/architecture group
passes, and Mypy passes all 962 sources. The first broader consumer run exposed
a shared editable-environment mismatch and two pre-existing runtime
qualification failures, both reproduced on current master and fixed separately
in #3929 (with its fixture correction in #3928). On that verified base, the
affected selection ran 1,122 tests in 50 modules: 49 modules passed; the sole
legacy record-shape/probe assertion was updated for the new runtime fingerprint
and input check. All 22 tests of that module then pass. Its mocked native client
now recognizes the shell action independently of the probe's text. Source-write
and opt-out footprint assertions remain strict. CI results are tracked in the PR.

For the live check, the official Homebrew Bash dependency was installed. An
earlier isolated bottle probe was verified against Homebrew's published SHA-256
and relocated only within a disposable directory. No model route or run limit
was changed for either probe.

The actual VS Code follow-up completed with independent Tester and Completion
Reviewer models and fresh evidence for all four criteria. Its read-only Planner
ran a 20,470-byte command containing a quoted heredoc and report check; the
captured tool result was exit 0, `OK (draft_plan)`. The recorded runtime is
Homebrew Bash 5.3.20. This verifies real model-issued input, alongside the native
32 KiB/literal/nested-input regression; it is not a sweep of every model or of
user scripts that explicitly select another interpreter. The run's redacted
bundle manifest is `97f13c2e300a31d23e1ce0e884cbed93599a07fc10ead006bf91cfcb462fa4f5`.

The first Ubuntu CI attempt caught a shared PATH defect in the candidate:
prepending the interpreter's parent directory also moved system Python and Git
ahead of the selected virtualenv and version manager on merged-/usr hosts.
The correction exposes only Bash through the protected alias directory; it
does not branch on a platform or command name. A regression fails before the
correction for Python, Git, Node and an arbitrary custom tool. Before dispatch,
the alias must still name the pinned binary, remain under this control area,
and contain no additional PATH commands. Kernel checks also deny adding a
command to that directory. The failed CI attempt remains retained on the PR.
