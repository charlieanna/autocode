"""Build the docs from their canonical sources; keep repository links revision-bound."""

from __future__ import annotations

import os
import runpy
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def on_config(config):
    # Importing this hook does not require MkDocs (the pure URL helper is independently testable).
    from markdown.extensions import Extension
    from markdown.treeprocessors import Treeprocessor

    class SourceLinks(Treeprocessor):
        def run(self, root):
            for element in root.iter():
                attribute = {"a": "href", "img": "src"}.get(element.tag)
                if attribute and element.get(attribute):
                    element.set(attribute, repository_link(element.get(attribute), self.extension, config))

    class RepositoryLinks(Extension):
        source = None
        site_paths = set()

        def extendMarkdown(self, md):
            processor = SourceLinks(md)
            processor.extension = self
            # Parsed links only: code examples stay byte-for-byte intact. MkDocs relpath runs at 0.
            md.treeprocessors.register(processor, "repository_links", 2)

    extension = RepositoryLinks()
    config["markdown_extensions"].append(extension)
    config.extra["source_links"] = extension
    ref = quote(os.environ.get("DOCS_SOURCE_REF", "master"), safe="")
    config.edit_uri = f"blob/{ref}/docs/"
    return config


def on_files(files, config):
    config.extra["source_links"].site_paths = {file.src_uri for file in files if file.inclusion.is_included()}


def repository_link(url, extension, config):
    """Leave site links to MkDocs; resolve existing source-only paths against this build's revision."""
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith("/"):
        return url
    docs = Path(config.docs_dir).resolve()
    target = (extension.source.parent / unquote(parts.path)).resolve()
    relative = target.relative_to(docs).as_posix() if target.is_relative_to(docs) else None
    if relative in extension.site_paths:
        return url
    repository = docs.parent
    if not target.is_relative_to(repository) or not target.exists():
        raise ValueError(f"{extension.source.relative_to(repository)}: missing repository link target: {url}")
    ref = quote(os.environ.get("DOCS_SOURCE_REF", "master"), safe="")
    path = quote(target.relative_to(repository).as_posix(), safe="/")
    destination = f"{config.repo_url}/blob/{ref}/{path}"
    parsed = urlsplit(destination)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parts.query, parts.fragment))


def on_page_markdown(markdown, page, config, **kwargs):
    extension = config.extra["source_links"]
    extension.source = Path(page.file.abs_src_path)
    if page.file.src_uri == "roles.md":
        roles = runpy.run_path(str(Path(config.docs_dir).parent / "tools" / "autocode_roles.py"))
        rows = ["| Stage in code | Job on screen |", "| --- | --- |"]
        rows += [f"| `{stage}` | {roles['screen_name'](stage)} |" for stage in roles["STAGE_JOB"]]
        markdown = markdown.replace("<!-- canonical-role-table -->", "\n".join(rows))
    return markdown
