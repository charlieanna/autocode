
SOURCE REFERENCES. When workspace_inventory.files lists any file, a handoff that
states required_behaviors or acceptance_tests must name at least one existing workspace file
in source_refs: read the file that grounds those requirements and cite it
repository-relative, optionally with :line. Never cite .autocode/, .git/, __pycache__/
or another path the runner or a tool owns or generates; the runner rejects those.
A runner-owned path the task tells you to read (a joint task's candidate copy under
.autocode/) is a task input, not a citation: cite the lasting workspace source the
work changes instead (a joint task's repos/ tree).
Cite only files whose contents you read in this request (a file-read tool, or a command
that printed them): the runner checks the stage's own tool calls and refuses a citation of a
file it never read. A report-only repair has no tools; it may cite only what the attempt it
repairs read.
source_refs may be empty only when the inventory lists no file at all, or while the
handoff asks clarifying questions and states no required_behaviors or acceptance_tests.
