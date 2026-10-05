"""Resolve the files needed to load a selected macOS executable, without running it.

The caller chooses executables. This module never grants an installation prefix,
executes package hooks, or searches the user's home for dependencies.
"""
from pathlib import Path
import re
import subprocess

# Shared-cache libraries are available through the OS policy, and may not exist
# as ordinary files on current macOS releases.
SYSTEM_LIBRARIES = ('/usr/lib/', '/System/Library/')


def _expand(name, loader, executable):
    for marker, base in (('@loader_path', loader.parent),
                         ('@executable_path', executable.parent)):
        if name == marker or name.startswith(marker + '/'):
            return base / name[len(marker):].lstrip('/')
    return Path(name) if name.startswith('/') else None


def dependencies(executable):
    """Return exact executable/library paths; reject unresolved dynamic loads.

    LC_RPATH entries follow the loading image and its loader chain. Resolution
    uses otool, not the selected program, so inspecting an executable cannot run
    its startup code outside containment. This function accepts Mach-O inputs;
    script interpreter and package selection belong to its caller.
    """
    executable = Path(executable).resolve(strict=True)
    with executable.open('rb') as stream:
        magic = stream.read(4)
    if magic not in (b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
                     b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                     b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'):
        raise ValueError(f'Selected tool is not a native Mach-O executable: {executable}')
    pending, found = [(executable, ())], set()
    while pending:
        selected, inherited = pending.pop()
        if str(selected).startswith(SYSTEM_LIBRARIES):
            continue
        path = selected.resolve(strict=True)
        if path in found:
            continue
        if not path.is_file():
            raise ValueError(f'Tool dependency is not a file: {path}')
        found.add(path)
        output = subprocess.run(['/usr/bin/otool', '-l', str(path)], check=True,
                                capture_output=True, text=True, timeout=10).stdout
        local = []
        for match in re.finditer(r'cmd LC_RPATH\n\s+cmdsize \d+\n\s+path (.+) \(offset \d+\)', output):
            root = _expand(match[1], path, executable)
            if root is None:
                raise ValueError(f'Unresolved tool library search path: {match[1]}')
            local.append(root.resolve())
        search = tuple(local) + inherited
        # otool -L includes LC_ID_DYLIB, the library's own install name. It
        # need not resolve in this loader chain (Apple Python is one example).
        # Only load/reexport commands introduce a dependency.
        loads = re.finditer(
            r'cmd LC_(?:LOAD|LOAD_WEAK|REEXPORT|LOAD_UPWARD|LAZY_LOAD)_DYLIB\n'
            r'\s+cmdsize \d+\n\s+name (.+) \(offset \d+\)', output)
        for match in loads:
            name = match[1]
            if name.startswith(SYSTEM_LIBRARIES):
                continue
            if name.startswith('@rpath/'):
                suffix = name[len('@rpath/'):]
                dependency = next((root / suffix for root in search
                                   if (root / suffix).is_file()), None)
            else:
                dependency = _expand(name, path, executable)
            if dependency is None:
                raise ValueError(f'Unresolved tool library dependency: {name}')
            pending.append((dependency, search))
    return found
