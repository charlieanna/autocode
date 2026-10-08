# build-924

Workflow: `build`
Upstream: pytest-dev/pytest-asyncio#924

## Acceptance brief

Finish the deprecation of unset asyncio_default_fixture_loop_scope. Unset must warn then fail; tests adjusted; existing suite green.

## Oracle

`python -m pytest -q; # oracle: unset option warns/fails, suite green`
