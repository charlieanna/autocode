2. Use the runner-prepared investigation_workspace in CURRENT HANDOFF DATA. It already contains a
   complete copy of eligible application source. Git copies include tracked and ordinary untracked inputs,
   excluding ignored credentials, dependencies and outputs. Do not rebuild the copy, copy individual
   source files into it, or substitute an incomplete directory. Reproduce the reported behavior there.
   Keep scratch tests, output and caches in that directory. Do not edit application source in the
   original workspace, create scratch outside the workspace, or modify existing runner state or
   evidence. Do not use /tmp or mktemp's default location. Run bounded checks in the foreground without
   nohup or detached processes, preserve the real command exit status, and allow enough time for cold
   compilation. If setup fails, report that failure; it is not evidence that the bug was reproduced.
   Python virtualenvs are reused rather than copied. When investigation_python is provided, use it for
   Python commands from investigation_workspace, with PYTHONDONTWRITEBYTECODE=1. Do not install packages into or
   modify that environment; keep application imports and scratch writes in the investigation_workspace.
   The submitted probe runs again from the root of a clean source copy. Temporary files and installed
   packages under .autocode/investigation are not copied, and absolute workspace paths are redirected
   into that source copy. Reuse investigation_python for dependencies and keep application imports
   relative to the replay root; do not make the probe depend on temporary scratch packages or tests.
   Make the submitted probe self-contained: if reproduction needs a temporary test or fixture, the
   probe must create it with a relative path in its replay tree before running it. During investigation,
   create those files only under investigation_workspace; never add them to the original workspace.
   Record exactly what you ran and what happened (reproduction, tests_run).
