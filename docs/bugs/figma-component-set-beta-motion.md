# Component-set beta motion capability

The native Figma connector exposes ordinary scene motion getters on frames and
variants, but rejects `animationStyles`, `animations`, `manualKeyframeTracks`
and `timelines` on `COMPONENT_SET`. Its standalone types inherit the beta motion
mixin for that container, so blindly requiring every getter made complete file
collection fail with four errors. A production companion file reproduced all
four exact `node.FIELD: no such property 'FIELD' on COMPONENT_SET node` errors.

The collector attempts all four reads. Only that complete, exact container
refusal records `motion_capability` with the original getter errors and emits
property profile `autocode-design-properties-v2`. It does not invent empty
motion values or omit readable data, and does not assert that unavailable beta
capabilities contain no motion. Profiles without the refusal keep v1.
The validator accepts only the matching evidence on a component-set container;
variant motion, prototype reactions and other node motion remain mandatory.
Partial refusals, undefined values, different errors and contradictory readable
values remain collection or validation failures. Existing v1 receipts remain
valid. Offline frozen-getter fixtures cover both versions and the refusal
controls; native collection must still qualify actual files separately.

## Section container capabilities

Whole-file collection also reproduced exact native `SECTION` getter refusals
for `minWidth`, `maxWidth`, `minHeight`, `maxHeight` and `strokeGeometry`, despite
the standalone opaque-node/minimal-stroke mixins declaring them. The same v2
profile records the complete five-error group as `section_capability`. Other
section dimensions, transforms, paints, corners and all child nodes remain
original required source facts. Supported groups retain their original values;
partial refusals, missing real dimensions and missing child geometry still fail.
