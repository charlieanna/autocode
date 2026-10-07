"""Supply source archive build metadata without replacing candidate code."""
from pathlib import Path
import sys
import types

package = Path.cwd() / "src" / "humanize"
if (package / "__init__.py").is_file():
    sys.path.insert(0, str(package.parent))
    if not (package / "_version.py").is_file():
        version = types.ModuleType("humanize._version")
        version.__version__ = "0+arena"
        sys.modules.setdefault(version.__name__, version)
