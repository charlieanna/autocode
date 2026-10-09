"""Cumulative review diffs including selected ignored outputs, without staging them."""
import os
import subprocess
import tempfile
from pathlib import Path

try:
    from . import autocode_source_snapshot as snapshots
except ImportError:
    import autocode_source_snapshot as snapshots


def write(workspace, destination, source, *, prefix=""):
    """Capture HEAD-to-source diff; the user's index is never opened for writes."""
    destination = Path(destination)
    if not source.get('source_paths'):
        with destination.open('wb') as output:
            subprocess.run(['git', 'diff', '--no-ext-diff', '--binary', '--src-prefix=a/' + prefix,
                             '--dst-prefix=b/' + prefix, 'HEAD'],
                           cwd=workspace, stdout=output, check=True)
        return
    with tempfile.TemporaryDirectory(prefix='source-diff-', dir=destination.parent) as directory:
        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(directory) / 'index')}
        def git(*args, data=None):
            return subprocess.run(['git', *args], cwd=workspace, env=env, input=data,
                                  capture_output=True, check=True).stdout
        git('read-tree', '--empty')
        names = [name for name, value in source['files'].items() if value != 'deleted']
        if names:
            git('--literal-pathspecs', 'add', '-f', '--pathspec-from-file=-', '--pathspec-file-nul',
                data=b'\0'.join(os.fsencode(name) for name in names) + b'\0')
        content = git('diff', '--cached', '--no-ext-diff', '--binary',
                      '--src-prefix=a/' + prefix, '--dst-prefix=b/' + prefix, 'HEAD')
        for index, (name, identity) in enumerate(source['files'].items()):
            if identity.startswith('submodule:'):
                root = Path(workspace) / name
                paths = snapshots.nested_paths(workspace, source['source_paths'], name)
                nested = snapshots.snapshot(root, paths=paths)
                artifact = Path(directory) / ('nested-' + str(index) + '.diff')
                write(root, artifact, nested, prefix=prefix + name + '/')
                content += artifact.read_bytes()
        if snapshots.snapshot(workspace, paths=source['source_paths']) != source:
            raise ValueError('Source changed while capturing the review diff')
        destination.write_bytes(content)
