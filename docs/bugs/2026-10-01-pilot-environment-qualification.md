# Provider environment preparation

Issue #226 came from a pilot launcher that cleared `os.environ` before building
the provider child's environment. It lost `PATH`, and provider admission failed
before a model request could start. This was a launcher preparation failure;
it was not evidence that a model had attempted or failed the engineering task.

The maintained OpenCode and command-provider APIs accept an optional `env`
mapping for admission, model listing, authentication checks and launch. Omit it
to retain the existing ambient-environment behavior. For an isolated launcher,
take a snapshot first, compute the child environment from that snapshot and
pass the same mapping throughout admission and launch:

```python
from providers import env_prep, opencode

prepared = env_prep.child_environment(env_prep.snapshot_environment())
# Add task-specific variables to this copy; keep the provider OAuth HOME.
settings = opencode.local_settings(workspace, env=prepared)
models = opencode.available_models(workspace, env=prepared)
command, child_env, metadata = opencode.launch(
    "terra", workspace, run_dir, None, selected_model, "medium", True,
    env=prepared,
)
# The existing runner starts command with env=child_env.
```

The explicit mapping supplies executable lookup and subprocess variables. It
admits an explicit absolute executable without PATH. Bare preflight commands
require the mapping's PATH, so POSIX default-path lookup cannot start a roster,
version or authentication subprocess outside the declared environment.
Relative PATH entries and relative command paths use the preflight child's
working directory. An explicitly empty PATH searches that working directory,
matching provider launch; omitting PATH from an explicit mapping still rejects
bare commands. When `env` is omitted, an unset ambient PATH retains the
prior POSIX default search behavior.

The mapping
also supplies configuration roots and the hashed inline `OPENCODE_*` inputs.
`~/` in `OPENCODE_CONFIG` and `OPENCODE_CONFIG_DIR` uses the mapping's HOME.
Admission returns the existing error prefixes with no-launch wording when an
executable or roster subprocess cannot start. A response that ends after
provider execution still retains the existing uncertainty and no-retry rule.

Billing guards examine forbidden variables in both the explicit mapping and
the ambient environment. Providing a clean mapping does not bypass an ambient
API-key or endpoint override. Authentication files and OAuth values are not
read into provenance; only configuration-file and inline-configuration hashes
are retained. This change adds no model route, paid fallback or live retry.

If an older integration requires temporary process-global changes, compute the
complete environment before mutation and restore the original in `finally`:

```python
original = dict(os.environ)
prepared = dict(original)
# Compute task-specific overrides here, before changing os.environ.
try:
    os.environ.clear()
    os.environ.update(prepared)
    # Call the older integration using the already prepared environment.
finally:
    os.environ.clear()
    os.environ.update(original)
```

That fallback is limited to a single-process integration without concurrent
threads observing the environment. No process-global mutation API is added to
AutoCode. Parallel workers should each pass their own explicit mapping.

The offline regression uses fake provider executables. It reproduces the
clear-before-build order inside an isolated child, confirms missing-PATH
admission stops with no-launch wording, then admits the snapshot-first mapping
while the child's ambient environment is empty and verifies restoration.
The provider tests also cover independent concurrent mappings, parent
environment preservation, configuration identity and both billing-guard
inputs. These tests qualify the maintained provider seam; a real vendor/model
call and the original external pilot remain separate qualification work.

```sh
.venv/bin/python -m unittest tests.test_provider_env_prep tests.test_env_prep_regression
```
