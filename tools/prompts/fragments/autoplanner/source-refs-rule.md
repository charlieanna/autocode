
SOURCE REFERENCES. When workspace_inventory.files lists any file, a handoff that
states required_behaviors or acceptance_tests must name at least one existing workspace file
in source_refs: read the file that grounds those requirements and cite it
repository-relative, optionally with :line. Never cite .autocode/, .git/, __pycache__/
or another path the runner or a tool owns or generates; the runner rejects those.
source_refs may be empty only when the inventory lists no file at all, or while the
handoff asks clarifying questions and states no required_behaviors or acceptance_tests.
