# Investigator test interpreter and clean replay

An Arena Django run stopped because its configured tests used an external prepared
virtualenv, but the Investigator handoff supplied the PATH Python. The model
installed missing `asgiref`/`sqlparse` into its investigation scratch directory.
Its direct probe passed there. Clean replay excluded `.autocode` and redirected
the original workspace prefix in that dependency path into the replay tree, so
the same probe failed before reaching the Django assertion. Later reports also
violated the no-reproduction schema and were correctly rejected. See #778.

A Go Arena investigation also referenced a temporary reproduction test under
`.autocode/investigation`; clean replay could not find it. A retry copied that
test into the original repository and was correctly rejected and restored.

The Investigator now reuses an executable explicit Python path from a literal
configured `python -m pytest` or `python -m unittest` command, including supported
`env NAME=value` prefixes. Relative interpreter paths are based on the project;
virtualenv symlinks retain their environment path. The existing command parser
leaves shell programs, computed paths, extra interpreter flags and bare Python
names to normal project discovery. The declared test command is included in the
handoff, together with the clean replay boundary.

Probes must import application code from the clean replay root and use the
prepared interpreter for dependencies. Temporary scratch packages/tests are not
copied into replay. Source isolation and the no-reproduction schema are unchanged.
The offline TaskRun regression imports an external prepared dependency in the
actual Investigator copy and then requires the runner's clean replay to succeed.
The probe creates its own relative fixture in both copies; the original workspace
does not receive it. The handoff explains this requirement for temporary tests.
