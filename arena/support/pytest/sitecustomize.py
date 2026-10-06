"""Supply source-archive version metadata for the pinned pytest case only."""
from pathlib import Path
import os
import sys
import types

paths = [Path.cwd() / "src", *(Path(p) for p in sys.path if p)]
for source in paths:
    if (source.name == "src" and (source / "pytest" / "__init__.py").is_file()
            and (source / "_pytest" / "__init__.py").is_file()):
        # Source wins even after a builder generates real version metadata.
        # Installed packages are not source checkouts and remain untouched.
        sys.path.insert(0, str(source))
        inherited = os.environ.get("PYTHONPATH", "")
        entries = list(dict.fromkeys((str(source), *filter(None, inherited.split(os.pathsep)))))
        os.environ["PYTHONPATH"] = os.pathsep.join(entries)
        if not (source / "_pytest" / "_version.py").exists():
            metadata = types.ModuleType("_pytest._version")
            metadata.version = metadata.__version__ = "8.0.0.dev0"
            metadata.version_tuple = metadata.__version_tuple__ = (8, 0, 0, "dev0")
            sys.modules.setdefault(metadata.__name__, metadata)
            import _pytest
            _pytest._version = metadata
        break
