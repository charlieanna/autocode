# Build and publish the documentation

The site renders the existing `docs/` Markdown files with Material for MkDocs.
The [front page](index.md) and README name the same five first-day guides.
Historical bug notes and design plans stay in the repository and remain linked
from the guides. They are not copied into a second documentation tree.

Use a separate environment for documentation tooling:

```sh
python3 -m venv .docs-venv
source .docs-venv/bin/activate
pip install -r docs/requirements.txt
mkdocs build --strict
mkdocs serve
```

The strict build is the CI link check: missing local targets and internal anchors
fail it. Source links that leave the published guide set are checked against the
checkout and rendered as GitHub links at the exact built revision. Code examples
are not rewritten. This checks repository cross-references; it does not promise
that every third-party URL is available.

`docs/site_hooks.py` generates the [stage table](roles.md) from
`tools/autocode_roles.py` and rewrites source links. To change a role name, edit
the canonical module rather than the generated table. No generated HTML belongs
in master.

## Publication

The `documentation` workflow builds every pull request. Only a push to master
or a `v*` release tag can publish. The publication job uses the GitHub Pages
artifact deployment with explicit `contents`, `pages` and OIDC permissions.
GitHub Pages must use **GitHub Actions** as its publishing source. In the
`github-pages` environment, allow deployments from the `master` branch and
`v*` tags; Pages initially permits only the default branch.

Mike stores generated versions in `gh-pages`. Master updates only `latest` and
the site's root redirect. A release tag adds its own version and preserves
`latest` and all earlier versions. Older master workflow reruns skip publication. Publication is serialized so these updates
do not overwrite each other. The release-tag hook is ready for the first
[versioned release](releasing.md); creating a documentation site does not create
or publish a package release.

For a local versioning rehearsal, use a disposable clone and local branch:

```sh
mike deploy --branch docs-preview --ignore-remote-status latest
mike set-default --branch docs-preview --ignore-remote-status latest
mike deploy --branch docs-preview --ignore-remote-status v0.0.0-test
mike list --branch docs-preview
```

This writes only the disposable clone. It does not push or deploy to Pages.
