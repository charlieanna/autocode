"""Install retained files and original link topology inside a disposable tree."""
from pathlib import Path
import shutil
try:
    from .autocode_protected_paths import identity, relative_name, verify_links
except ImportError:
    from autocode_protected_paths import identity, relative_name, verify_links


def apply(tree, files, links, *, removed=()):
    """Copy ``files`` (name: source), create ``links`` (name: target) and leave ``removed`` absent."""
    tree = Path(tree).resolve()
    files, links = files or {}, links or {}
    installed = files.keys() | links.keys()
    if files.keys() & links.keys():
        raise ValueError('Scratch overlay cannot be both a file and a link')
    if installed & set(removed):
        raise ValueError('Scratch overlay cannot both install and remove a path')
    names = sorted(installed | set(removed))
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
    if links:
        # This also prevents a caller accidentally supplying an escaping link
        # or forgetting the original target in its overlay.
        verify_links(tree, {name: identity(tree / name) for name in sorted(installed)})
