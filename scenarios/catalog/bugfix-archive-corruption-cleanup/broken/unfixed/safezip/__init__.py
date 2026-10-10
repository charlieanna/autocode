"""Safely extract validated ZIP archives using only the standard library."""

import os
import re
import shutil
import stat
import tempfile
import zipfile

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def _validate_destination(destination):
    destination = os.path.abspath(os.fspath(destination))
    current = os.path.sep
    for component in os.path.relpath(destination, os.path.sep).split(os.path.sep):
        current = os.path.join(current, component)
        if os.path.lexists(current) and os.path.islink(current):
            raise ValueError("destination path contains a symlink")

    parent = os.path.dirname(destination)
    if not os.path.isdir(parent):
        raise ValueError("destination parent must exist")
    if os.path.lexists(destination):
        if not os.path.isdir(destination):
            raise ValueError("destination must be a directory")
        if os.listdir(destination):
            raise ValueError("destination must be empty")
    return destination


def _member_path(info):
    name = info.filename
    if not name or name.startswith("/") or "\\" in name or _DRIVE_PREFIX.match(name):
        raise ValueError("unsafe ZIP member name")
    directory = name.endswith("/")
    stripped = name[:-1] if directory else name
    if not stripped:
        raise ValueError("unsafe ZIP member name")
    components = stripped.split("/")
    if any(component in ("", ".", "..") for component in components):
        raise ValueError("unsafe ZIP member name")
    return components, directory


def _validate_members(archive, max_bytes):
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a nonnegative integer")

    members = []
    names = {}
    total_size = 0
    try:
        infos = archive.infolist()
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise ValueError("invalid ZIP archive") from error

    for info in infos:
        components, directory = _member_path(info)
        mode_type = stat.S_IFMT(info.external_attr >> 16)
        if mode_type and mode_type != (stat.S_IFDIR if directory else stat.S_IFREG):
            raise ValueError("ZIP member has an unsafe type")
        path = tuple(components)
        if path in names:
            raise ValueError("ZIP archive contains duplicate members")
        names[path] = directory
        if not directory:
            total_size += info.file_size
            if total_size > max_bytes:
                raise ValueError("ZIP archive exceeds max_bytes")
        members.append((info, path, directory))

    for path in names:
        if any(prefix in names and not names[prefix] for prefix in (path[:length] for length in range(1, len(path)))):
            raise ValueError("ZIP archive contains prefix collisions")
    return members


def _stage_members(archive, members, parent):
    staging = tempfile.mkdtemp(prefix=".safezip-", dir=parent)
    try:
        for info, path, directory in members:
            target = os.path.join(staging, *path)
            if directory:
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as source, open(target, "xb") as output:
                shutil.copyfileobj(source, output)
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError("ZIP archive could not be extracted safely") from error
    return staging


def extract(zip_path, destination, max_bytes=1048576):
    """Extract a validated ZIP archive and return sorted regular-file paths.

    Archive contents are fully staged beneath the existing destination parent before
    an empty or absent destination is replaced.
    """
    destination = _validate_destination(destination)
    parent = os.path.dirname(destination)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if any(info.flag_bits & 1 for info in archive.infolist()):
                raise ValueError("encrypted ZIP members are unsupported")
            members = _validate_members(archive, max_bytes)
            staging = _stage_members(archive, members, parent)
    except ValueError:
        raise
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as error:
        raise ValueError("invalid ZIP archive") from error

    try:
        if os.path.lexists(destination):
            os.rmdir(destination)
        os.replace(staging, destination)
    except OSError as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError("destination could not be published") from error
    return sorted("/".join(path) for _, path, directory in members if not directory)
