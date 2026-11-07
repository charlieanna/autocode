# Releasing

[← Back to Install](install.md)

AutoCode is published to PyPI as `autocode-supervisor` by
`.github/workflows/publish.yml`. It uses PyPI
[trusted publishing](https://docs.pypi.org/trusted-publishers/): GitHub proves to PyPI
which repository, workflow and environment is uploading, so there is no API token to
store or rotate.

## Published baseline

PyPI and `charlieanna/homebrew-tap` already publish **0.8.2**, checked on
2026-10-27. The tag points to `86fdb7920f46a935405840f78c8c3bff0ed3422b`
(2026-10-21), not current master. See the [distribution verification
record](bugs/2026-10-27-release-installation.md). Existing versions/tags must
not be republished or moved to ship later master fixes: choose a new version
and follow the release process below.

The one-time publisher/environment/tap instructions below describe setup for
an installation that does not already have them. Do not create a second PyPI
project or overwrite the existing tap as part of a release.

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

## Homebrew

Homebrew installs AutoCode from a tap, `charlieanna/homebrew-tap`, whose
`Formula/autocode.rb` pins the PyPI release's source archive and each runtime
dependency by sha256. People then install with:

```sh
brew install charlieanna/tap/autocode
```

### One-time setup

Create the public GitHub repository `charlieanna/homebrew-tap` with an empty
`Formula/` directory. Homebrew finds `charlieanna/tap` by that name.

### Each release

After the PyPI release above has succeeded, from a checkout of the release tag:

```sh
python3 scripts/homebrew_formula.py 0.8.0 > ../homebrew-tap/Formula/autocode.rb
cd ../homebrew-tap
brew install --build-from-source ./Formula/autocode.rb && brew test autocode
git commit -am "autocode 0.8.0" && git push
```

The script refuses a version other than `pyproject.toml`'s and a release with no
source archive on PyPI. The formula builds a virtualenv on Homebrew's Python and
links only the public `autocode` command; `brew test` checks that it reports the
release's version.

## Cadence and qualification decision (#2048)

The proposed two-week release cadence remains an owner decision, not an enabled
schedule. No automation or calendar promise is created by the installation
correction. Until a cadence is agreed, releases use the explicit version/tag
process above.

Before choosing the next release pin, review that revision's required CI,
packaging/install checks and applicable supported-host/provider reliability
record, preserving failed, skipped, unknown and unrun results. A distribution
smoke pass or a prior tag's live sample does not qualify all newer master
behavior. Record the exact candidate, missing evidence and agreed release gate;
#2048 remains open for this policy decision. Do not publish the current master
under the already-published 0.8.2 identity to avoid that decision.
