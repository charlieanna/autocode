# Verification's pure suite scanner (#1125)

The literal JS and shell scanner lives in `autocode_suite_definition_scan`.
It takes text or repository-relative paths and uses only standard-library
helpers. It does not read files, execute commands or import the verification
controller. Manifest/config classification, comments and template literals,
import sites, shell identification, literal file reads and child-command
literals moved together without changing their algorithms.

`autocode_verify` retains explicit compatibility names for the moved helpers
and constants. Existing callers still access those names there, and patches
to the controller's lookups remain effective. Helpers' own globals now belong
to the scanner; patching one scanner helper's nested dependency belongs there.
No existing fixture patches that nested dependency through verification.
Git scratch inputs, suite traversal, command execution and verdicts stay with
their current owners. Saved evidence and run state do not change.

This first extraction reduces verification from 3,680 to 3,440 lines and adds
it to the architecture size ratchet at that measured size. Further separation
of the remaining responsibilities is outside this change.
