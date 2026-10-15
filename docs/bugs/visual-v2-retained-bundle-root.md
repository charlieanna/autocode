# Version 2 image binding needs the retained bundle directory

The native companion qualification reached its independent Tester with a complete
version 2 design inventory, but visual preparation returned `NOT_READY`: the
inventory validator was called without the retained bundle directory. No image
comparison or visual acceptance receipt was produced.

`autocode_visual_acceptance.binding` now passes the retained record's `root` to
`autocode_design_manifest.validate`. Version 1 validation still accepts its
existing root-optional record. Source inventory, case coverage, reference hashes,
native delivery and independent acceptance requirements remain enforced.

Focused tests exercise a complete synthetic two-file version 2 bundle, reject a
missing or wrong root, reject incomplete source inventory and omitted case
mapping, and separately reject original source-byte drift at the runtime launch
gate. Synthetic exports test binding only; they do not qualify real Figma access
or native image acceptance.

Retain the failed native attempts. A repaired runtime still requires fresh
current candidate captures and a successful independent native image review.
