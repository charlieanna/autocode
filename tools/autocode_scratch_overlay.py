"""Install retained files and original link topology inside a disposable tree."""

import shutil
from pathlib import Path, PurePosixPath

try:
    from .autocode_protected_paths import identity, relative_name, verify_links
except ImportError:
    from autocode_protected_paths import identity, relative_name, verify_links


def apply(tree, files, links, *, bound=False):
    tree = Path(tree).resolve()
    files, links = files or {}, links or {}
    if files.keys() & links.keys():
        raise ValueError("Scratch overlay cannot be both a file and a link")
    names = sorted(files.keys() | links.keys())
    expected = {name: identity(Path(source)) for name, source in files.items()} if bound else {}
    if any("symlink" in value for value in expected.values()):
        raise ValueError("A retained regular fixture is no longer a regular file")
    for name in names:
        relative_name(name)
    # Prepare every destination before creating links: candidate parents may
    # point to the live workspace, or alias another path inside this tree.
    for name in names:
        target = tree / name
        current = tree
        for part in Path(name).parts[:-1]:
            current /= part
            if current.is_symlink() or current.is_file():
                current.unlink()
            current.mkdir(exist_ok=True)
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
    for name, source in files.items():
        shutil.copy2(source, tree / name)
    for name, target in links.items():
        (tree / name).symlink_to(target)
    if bound:
        expected.update({name: {"symlink": target} for name, target in links.items()})
        installed = {name: identity(tree / name) for name in names}
        if installed != expected:
            raise ValueError("Retained fixture bytes changed during copy")
        return {
            name: (
                "symlink:" + value["symlink"]
                if "symlink" in value
                else ("executable:" if value["mode"] & 0o111 else "") + value["sha256"]
            )
            for name, value in expected.items()
        }
    if links:
        # This also prevents a caller accidentally supplying an escaping link
        # or forgetting the original target in its overlay.
        verify_links(tree, {name: identity(tree / name) for name in names})
    return {}


def combined_inventory(files, overlays):
    """Overlay identities supersede the entries used to make the exact source copy."""
    parents = {parent.as_posix() for name in overlays for parent in PurePosixPath(name).parents}
    visible = {
        name: value
        for name, value in files.items()
        if name not in parents and all(parent.as_posix() not in overlays for parent in PurePosixPath(name).parents)
    }
    return visible | overlays
