# Prepare a complete investigation workspace before launching the model

The private TEST Jira pilot reached `investigate_bug-05` after its task compiler
was corrected. Its scratch copy lacked `accessors/datainterface` and `rule`, so
the Go test stopped with setup errors rather than reproducing the reported bug.
The model tried to repair the copy using a compound `find | while` shell command.
GoCode then returned HTTP 400 `blocked_by_guardrail`, citing that command's risk
score of 85 against a threshold of 80. The original application checkout stayed
clean, and the task paused without an implementation plan or a completed fix.

The runner now creates a fresh workspace-contained copy before preparing the
Investigator's request. It includes source in every package, non-code resources
and offline dependencies. Repository metadata and runner state are excluded.
The handoff names this existing copy and instructs the model to use it rather
than reconstructing it. Existing scratch/evidence is retained, and modifying a
copied file does not change its original.

Symlinked runner directories, external or state links, and directory symlinks
are rejected. Internal file links are copied as independent file contents.
Root `.venv` and `venv` environments identified by a regular `pyvenv.cfg` are
excluded from the source copy. Their normal external interpreter links and Linux
`lib64` directory links therefore do not block investigation preparation. The
handoff provides `investigation_python` through the existing project-environment
lookup; the Investigator uses it with bytecode writes disabled against the scratch
source, without installing into or modifying the shared environment. Other source
symlinks retain the same checks, and ordinary directories named `venv` are copied.
Other directory-link dependency layouts remain unsupported by this preparation path;
they fail before a model launches. No GoCode guard, model permission, recovery
limit or human approval requirement is weakened.

New workspace, prompt and request tests failed before implementation. The
focused bug-job/workspace tests plus architecture checks pass (59 tests).
Broader local fake scenarios were attempted but fail at the existing signed-in
Codex authentication boundary before creating runs; live AWS completion is
still unproven. Retained pilot state was inspected without editing it.
