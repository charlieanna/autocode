"""Inspect or corrupt retained fixtures without depending on an on-disk directory."""

import zipfile

import autocode_protected_store as store


def retained_text(record, workspace, name):
    with store.opened(record, workspace) as root:
        return (root / name).read_text()


def rewrite_archive(record, updates):
    path = store.archive_path(record)
    with zipfile.ZipFile(path) as bundle:
        members = [(info, bundle.read(info.filename)) for info in bundle.infolist()]
    with zipfile.ZipFile(path, "w") as bundle:
        for info, data in members:
            bundle.writestr(info, updates.get(info.filename, data))
