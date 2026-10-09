"""Recognize literal Go test invocations without interpreting shell programs."""
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


def _words(command):
    if not isinstance(command, str) or any(c in command for c in '\x00\n\r'):
        return None
    quote, escaped = None, False
    for character in command:
        if escaped:
            if quote == '"' and character in '$`':
                return None  # shlex and the shell interpret these escapes differently.
            escaped = False
        elif quote == "'":
            if character == "'":
                quote = None
        elif character == '\\':
            escaped = True
        elif quote == '"':
            if character == '"':
                quote = None
            elif character in '$`':
                return None
        elif character in "'\"":
            quote = character
        elif character in ';&|<>`$(){}*?[]#~':
            return None
    try:
        return shlex.split(command)
    except ValueError:
        return None


@dataclass(frozen=True)
class Invocation:
    prefix: tuple[str, ...]
    executable: str
    arguments: tuple[str, ...]
    command: str

    @property
    def json_flags(self):
        flags, index = [], 0
        while index < len(self.arguments):
            word = self.arguments[index]
            if word in ('-args', '--'):
                break  # The remaining flags belong to the test program.
            if word in ('-run', '-bench', '-fuzz'):
                index += 2  # A literal selector may itself start with -json.
                continue
            if word == '-json' or word.startswith('-json='):
                flags.append(word)
            index += 1
        return flags

    @property
    def json_disabled(self):
        # An explicit disabled/malformed reporter cannot supply native test
        # identities, even if the test program happens to print JSON itself.
        return any(word not in ('-json', '-json=1', '-json=t', '-json=T',
                                '-json=true', '-json=TRUE', '-json=True')
                   for word in self.json_flags)

    def instrument(self):
        # The leading flag is unambiguous. A later -json token could instead
        # be another flag's value; it must not prevent native JSON collection.
        # Repeating a later enabled reporter preserves its native semantics.
        if self.json_disabled or (self.arguments and
                (self.arguments[0] == '-json' or self.arguments[0].startswith('-json='))):
            return self.command
        return shlex.join([*self.prefix, self.executable, 'test', '-json', *self.arguments])


def parse(command):
    """Accept Go directly or behind env with literal NAME=value assignments.

    Quotes may protect literal regex characters; expansions, shell operators,
    globbing and shell wrappers never become a test collection claim.
    """
    words = _words(command)
    if not words:
        return None
    offset = 0
    if words[0] in ('env', '/usr/bin/env', '/bin/env'):
        offset = 1
        while offset < len(words) and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', words[offset]):
            offset += 1
    tail = words[offset:]
    if len(tail) < 2 or Path(tail[0]).name not in ('go', 'go.exe') or tail[1] != 'test':
        return None
    return Invocation(tuple(words[:offset]), tail[0], tuple(tail[2:]), command)
