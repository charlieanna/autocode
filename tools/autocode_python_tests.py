"""Parse literal Python test commands without interpreting shell programs."""
from dataclasses import dataclass
from pathlib import Path
import re
import shlex


@dataclass(frozen=True)
class Invocation:
    prefix: tuple[str, ...]
    python: str
    kind: str
    arguments: tuple[str, ...]

    def targeted(self, files):
        """Keep environment and collection options when replacing suite paths.

        Unknown option arity cannot safely be guessed. In that case execute the
        original suite, which still supplies named base/candidate results.
        """
        if self.kind != 'pytest':
            return shlex.join([*self.prefix, self.python, '-m', 'unittest', '-v', *files])
        values = {'-p', '-o', '-c', '-k', '-m', '--override-ini', '--rootdir',
                  '--confcutdir', '--import-mode', '--tb', '--assert', '--maxfail',
                  '--ignore', '--ignore-glob', '--deselect', '--color', '--capture'}
        flags = {'-q', '-v', '-vv', '-s', '-x', '--verbose', '--quiet', '--disable-warnings',
                 '--strict-markers', '--strict-config', '--pyargs', '--continue-on-collection-errors'}
        kept, index = [], 0
        while index < len(self.arguments):
            word = self.arguments[index]
            if word in values:
                if index + 1 == len(self.arguments):
                    return None
                kept.extend(self.arguments[index:index + 2])
                index += 2
                continue
            if word in flags or ('=' in word and word.split('=', 1)[0] in values):
                kept.append(word)
            elif word.startswith('-'):
                return shlex.join([*self.prefix, self.python, '-m', self.kind, *self.arguments])
            index += 1
        return shlex.join([*self.prefix, self.python, '-m', 'pytest', *kept, *files])


def parse(command):
    """Accept Python directly or behind env with literal NAME=value assignments."""
    if not isinstance(command, str) or re.search(r'[;&|<>`$\n]', command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    offset = 0
    if words and words[0] in ('env', '/usr/bin/env', '/bin/env'):
        offset = 1
        while offset < len(words) and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', words[offset]):
            offset += 1
    tail = words[offset:]
    if (len(tail) < 3 or not re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', Path(tail[0]).name)
            or tail[1] != '-m' or tail[2] not in ('pytest', 'unittest')):
        return None
    if tail[2] == 'unittest' and not {'-v', '--verbose'} & set(tail[3:]):
        return None
    return Invocation(tuple(words[:offset]), tail[0], tail[2], tuple(tail[3:]))
