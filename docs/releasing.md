# Releasing

[← Back to Install](install.md)

AutoCode is published to PyPI as `autocode-supervisor` by
`.github/workflows/publish.yml`. It uses PyPI
[trusted publishing](https://docs.pypi.org/trusted-publishers/): GitHub proves to PyPI
which repository, workflow and environment is uploading, so there is no API token to
store or rotate.

## One-time setup

1. On PyPI, signed in as the account that will own the project, open
   **Your account → Publishing** and add a pending GitHub publisher:
   project name `autocode-supervisor`, owner `charlieanna`, repository `autocode`,
   workflow `publish.yml`, environment `pypi`. The first upload creates the project;
   after that the publisher is listed under the project's **Settings → Publishing**.
2. On GitHub, **Settings → Environments → New environment** named `pypi`. Add
   yourself as a required reviewer if each release should wait for a click, and limit
   its deployment tags to `v*`.

## Each release

1. Set `version` in `pyproject.toml` (for example `0.8.0`) and merge that to master.
2. Tag the merged commit with `v` and the same version, and push the tag:

   ```sh
   git fetch origin
   git tag v0.8.0 origin/master
   git push origin v0.8.0
   ```

3. The `publish` workflow refuses a tag that differs from `v` plus pyproject's version,
   builds the sdist and wheel, checks the wheel installs and reports that version, then
   uploads both (after the `pypi` environment's approval, if you set one).
4. Check it: `pipx install autocode-supervisor` (or `pipx upgrade autocode-supervisor`),
   then `autocode --version` prints the new version and `commit unknown`.

PyPI never accepts the same file twice. Once an upload has succeeded, fix a bad release
with a new version, not by moving the tag.
