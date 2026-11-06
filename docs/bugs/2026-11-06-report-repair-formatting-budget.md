# Report repair formatting budget

A completed Tester report with many per-line evidence decisions could fit the repair
content budget but exceed it after the runner saved indented JSON. The repair reader
refused that saved artifact before the normal report correction could run.

The reader now allows at most 512 KiB of artifact bytes. For an artifact above the
existing 128 KiB threshold, valid JSON must fit that same threshold when serialized
with the repair handoff's compact encoding. Oversized malformed text and oversized
JSON content still pause. The final 256 KiB handoff bound remains unchanged.

No saved artifact is rewritten or truncated. Source paths, hashes and byte counts
still describe the original bytes, and every report admission check remains in force.
Unit tests cover formatting, Unicode, exact content preservation and both size bounds.
A public CLI regression repairs an indented Tester report, preserves its original
checks and pinned source, and reaches current completion.
