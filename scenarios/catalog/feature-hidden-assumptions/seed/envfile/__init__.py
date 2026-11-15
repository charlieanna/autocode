"""envfile: inspect KEY=VALUE env files from the command line."""

from .errors import EnvFileError, fail
from .parse import dump, load

__all__ = ["EnvFileError", "dump", "fail", "load"]
