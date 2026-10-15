# Native visual profile rejects its bundled activity plugin

The built-in OpenCode adapter fingerprints and injects
`providers/opencode_activity.mjs` for every worker. The isolated native visual
profile required every configuration hash to be empty and no original plugins,
so a real production Tester worker became `NOT_READY` before image dispatch.
The previous profile fixtures omitted the bundled hook and missed this conflict.

The profile now recognizes only that exact bundled path and its hash in the
operator-approved transport identity. It requires the original plugin list to
contain precisely the activity hook, then the prepared list to contain activity
followed by image delivery. It copies the approved activity bytes into the owned
stage as a read-only file and pins them in the launch evidence. Arbitrary
plugins, custom configuration, reordered/duplicated hooks and changed source or
stage bytes remain refused. Saved routes, request caps, permissions, containment
and independent visual completion authority are unchanged.

The regression exercises the actual production worker builder, adapter and
kernel containment preparation using an owned fake executable. The executable
supports model-free debug checks and a child-configuration probe; it performs no
model or SDK request. Separate controls cover activity tampering and inherited
plugins. This proves launch compatibility and refusal behavior, not native SDK
image delivery or genuine visual acceptance. Real-model qualification remains
required before a ready pull request.
