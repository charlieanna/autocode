# Stage names

This table is generated at site build time from
[`tools/autocode_roles.py`](../tools/autocode_roles.py). It is the same source
used by the CLI and dashboard; there is no separate documentation mapping.

<!-- canonical-role-table -->

The table shows the default mapping. Under reviewer routing (`glm_first_v1`),
`astra_checkpoint` performs the **Tester** job. A `_report_repair` suffix retains
the stage's job name. See [model routes](models.md#roles) for responsibilities
and configuration.
