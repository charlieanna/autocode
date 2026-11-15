"""Read and write env files: one ``KEY=VALUE`` entry per line."""

from pathlib import Path

from .errors import EnvFileError


def load(path):
    """The entries of the env file at ``path``, in file order.

    Blank lines and lines starting with ``#`` are skipped. A key repeated in the
    same file keeps its first value; later lines for that key are ignored.
    Raises EnvFileError for a missing file or a line that is not KEY=VALUE.
    """
    path = Path(path)
    if not path.is_file():
        raise EnvFileError(f"{path}: no such file")
    entries = {}
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            raise EnvFileError(f"{path}:{number}: expected KEY=VALUE")
        entries.setdefault(key, value.strip())
    return entries


def dump(entries, *, sort=False):
    """The text of an env file holding ``entries``, one ``KEY=VALUE`` line each."""
    keys = sorted(entries) if sort else list(entries)
    return "".join(f"{key}={entries[key]}\n" for key in keys)
