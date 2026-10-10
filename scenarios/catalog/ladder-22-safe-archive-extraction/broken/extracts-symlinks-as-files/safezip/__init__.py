import lzma
import os
import re
import shutil
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path


def extract(zip_path, destination, max_bytes=1048576):
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("invalid size limit")
    destination = Path(os.path.abspath(destination))
    if any(item.is_symlink() for item in (destination, *destination.parents)):
        raise ValueError("symlink destination")
    if not destination.parent.is_dir():
        raise ValueError("parent missing")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("destination not empty")
    staging = None
    try:
        with zipfile.ZipFile(zip_path) as archive:
            entries = []
            names = {}
            total = 0
            for info in archive.infolist():
                raw = info.filename
                name = raw[:-1] if raw.endswith("/") else raw
                if (
                    not name
                    or raw.startswith("/")
                    or "\\" in raw
                    or re.match(r"^[A-Za-z]:", raw)
                    or any(part in ("", ".", "..") for part in name.split("/"))
                ):
                    raise ValueError("unsafe path")
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                    raise ValueError("special file")
                is_dir = raw.endswith("/")
                if (kind == stat.S_IFDIR and not is_dir) or (kind == stat.S_IFREG and is_dir):
                    raise ValueError("inconsistent type")
                if name in names:
                    raise ValueError("duplicate name")
                names[name] = is_dir
                entries.append((info, name, is_dir))
                if not is_dir:
                    total += info.file_size
                if total > max_bytes:
                    raise ValueError("archive too large")
            for name in names:
                parts = name.split("/")
                if any("/".join(parts[:i]) in names and not names["/".join(parts[:i])] for i in range(1, len(parts))):
                    raise ValueError("file/directory collision")
            staging = Path(tempfile.mkdtemp(dir=destination.parent, prefix=".extract-"))
            actual = 0
            for info, name, is_dir in entries:
                target = staging / name
                if is_dir:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    while True:
                        block = source.read(65536)
                        if not block:
                            break
                        actual += len(block)
                        if actual > max_bytes:
                            raise ValueError("archive too large")
                        output.write(block)
            files = sorted(name for _, name, is_dir in entries if not is_dir)
        os.replace(staging, destination)
        staging = None
        return files
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, lzma.LZMAError) as error:
        raise ValueError("invalid archive or destination") from error
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
