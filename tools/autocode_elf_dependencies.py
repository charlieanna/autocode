"""Resolve the non-system files a selected Linux executable loads, without running it.

The Linux counterpart of autocode_macho_dependencies (#709). The bubblewrap tool
boundary already mounts the system runtime (/usr and /lib*) read-only, so only a
toolchain installed elsewhere (a hosted Python, Node or Go, a Nix store path)
needs its exact executable and shared libraries named as read roots.

The ELF dynamic section is parsed directly: no ldd, no loader and no part of the
selected program runs, so inspecting a tool cannot execute its code outside
containment. A library named by an RPATH or RUNPATH outside the system roots is
followed; any other DT_NEEDED name is left to the system loader search, whose
directories the boundary mounts. A library the loader would find somewhere else
(an ld.so.conf entry under /opt, LD_LIBRARY_PATH, which the contained shell
clears) is not granted, so the toolchain's contained startup probe fails before
any model launch rather than the tool failing later.
"""

from __future__ import annotations

import struct
from pathlib import Path

# Mounted read-only by the bubblewrap boundary, so never granted again.
SYSTEM_ROOTS = ("/usr/", "/lib/", "/lib32/", "/lib64/", "/libx32/", "/bin/", "/sbin/")
PT_DYNAMIC, PT_INTERP, PT_LOAD = 2, 3, 1
DT_NULL, DT_NEEDED, DT_STRTAB, DT_RPATH, DT_RUNPATH = 0, 1, 5, 15, 29


def _system(path):
    return str(path).startswith(SYSTEM_ROOTS)


def _string(data, offset):
    end = data.find(b"\0", offset)
    if offset < 0 or end < 0:
        raise ValueError("Malformed ELF string table")
    return data[offset:end].decode("utf-8", "surrogateescape")


def _dynamic(path):
    """(interpreter, needed, rpath, runpath) of one ELF file; rpath/runpath are raw strings."""
    data = Path(path).read_bytes()
    if data[:4] != b"\x7fELF" or len(data) < 52:
        raise ValueError(f"Selected tool is not a native ELF executable: {path}")
    width = {1: 32, 2: 64}.get(data[4])
    order = {1: "<", 2: ">"}.get(data[5])
    if width is None or order is None:
        raise ValueError(f"Unsupported ELF class or byte order: {path}")
    # e_phoff, then e_phentsize and e_phnum; a program header row; a dynamic entry; and
    # where a row keeps p_type, p_offset, p_vaddr and p_filesz.
    if width == 64:
        (phoff,), (phentsize, phnum) = (
            struct.unpack_from(order + "Q", data, 32),
            struct.unpack_from(order + "HH", data, 54),
        )
        header, entry, columns = order + "IIQQQQQQ", order + "qQ", (0, 2, 3, 5)
    else:
        (phoff,), (phentsize, phnum) = (
            struct.unpack_from(order + "I", data, 28),
            struct.unpack_from(order + "HH", data, 42),
        )
        header, entry, columns = order + "IIIIIIII", order + "iI", (0, 1, 2, 4)
    fields = lambda row: tuple(row[column] for column in columns)  # type, offset, vaddr, filesz
    if phentsize < struct.calcsize(header) or phoff + phnum * phentsize > len(data):
        raise ValueError(f"Malformed ELF program headers: {path}")
    segments = [fields(struct.unpack_from(header, data, phoff + index * phentsize)) for index in range(phnum)]
    interpreter = None
    for kind, offset, _, size in segments:
        if kind == PT_INTERP:
            interpreter = _string(data[offset : offset + size] + b"\0", 0)
    dynamic = [(offset, size) for kind, offset, _, size in segments if kind == PT_DYNAMIC]
    if not dynamic:
        return interpreter, [], None, None  # Statically linked.
    offset, size = dynamic[0]
    step = struct.calcsize(entry)
    tags = []
    for position in range(offset, min(offset + size, len(data) - step + 1), step):
        tag, value = struct.unpack_from(entry, data, position)
        if tag == DT_NULL:
            break
        tags.append((tag, value))
    table = next((value for tag, value in tags if tag == DT_STRTAB), None)
    if table is None:
        raise ValueError(f"ELF dynamic section has no string table: {path}")
    # DT_STRTAB is a virtual address; the loadable segment holding it gives its file offset.
    load = next(
        (
            (offset, vaddr)
            for kind, offset, vaddr, size in segments
            if kind == PT_LOAD and vaddr <= table < vaddr + size
        ),
        None,
    )
    if load is None:
        raise ValueError(f"ELF string table is outside every loadable segment: {path}")
    base = table - load[1] + load[0]
    text = lambda wanted: [_string(data, base + value) for tag, value in tags if tag == wanted]
    rpath, runpath = text(DT_RPATH), text(DT_RUNPATH)
    return interpreter, text(DT_NEEDED), (rpath or [None])[0], (runpath or [None])[0]


def _search_path(value, origin):
    """Expand one RPATH/RUNPATH string; $ORIGIN is the only token AutoCode qualifies."""
    directories = []
    for item in (value or "").split(":"):
        if not item:
            continue
        expanded = item.replace("${ORIGIN}", str(origin)).replace("$ORIGIN", str(origin))
        if "$" in expanded:
            raise ValueError(f"Unsupported dynamic string token in tool library search path: {item}")
        if not expanded.startswith("/"):
            raise ValueError(f"Relative tool library search path: {item}")
        directories.append(Path(expanded))
    return tuple(directories)


def dependencies(executable):
    """Return the executable and the libraries it loads outside the system roots.

    RPATH follows the loading chain unless an object has its own RUNPATH, which
    covers only that object's direct dependencies, as the glibc loader searches.
    A dependency inside the system roots is mounted already and not followed.
    """
    executable = Path(executable).resolve(strict=True)
    pending, found = [(executable, ())], set()
    while pending:
        selected, inherited = pending.pop()
        path = selected.resolve(strict=True)
        if path in found:
            continue
        if not path.is_file():
            raise ValueError(f"Tool dependency is not a file: {path}")
        # Parse even a system executable, so a script or non-ELF tool is refused like on macOS.
        interpreter, needed, rpath, runpath = _dynamic(path)
        if _system(path):
            continue
        found.add(path)
        if interpreter and not _system(interpreter):
            pending.append((Path(interpreter), ()))
        # glibc drops an object's RPATH when it has a RUNPATH, and searches the loading
        # chain's RPATHs only for an object without one.
        chain = (() if runpath is not None else _search_path(rpath, path.parent)) + inherited
        search = _search_path(runpath, path.parent) if runpath is not None else chain
        for name in needed:
            if "/" in name:
                if not name.startswith("/"):
                    raise ValueError(f"Relative tool library dependency: {name}")
                candidates = [Path(name)]
            else:
                candidates = [directory / name for directory in search]
            dependency = next((candidate for candidate in candidates if candidate.is_file()), None)
            if dependency is not None and not _system(dependency.resolve()):
                pending.append((dependency, chain))
    return found
