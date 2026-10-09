"""Download pinned projects and ingest verified controls through Arena's CLI.

This preparation tool never invokes a model. Each invocation needs a fresh Arena
directory; partial failed preparations are retained for diagnosis.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path(__file__).with_name("catalog.json")


def command(*args, timeout=180, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, timeout=timeout, **kwargs)


def add_test_overlay(overlay, source, reference, base):
    """Add the same new evaluator tests to both controls, preserving runtime source."""
    files = []
    for path in sorted(overlay.rglob('*')):
        if path.is_symlink():
            raise ValueError('Test overlays cannot contain symlinks')
        if not path.is_file():
            continue
        relative = path.relative_to(overlay)
        if relative.parts[0] not in ('tests', 'test') or path.suffix not in (
                '.py', '.js', '.cjs', '.mjs', '.ts', '.go'):
            raise ValueError('Test overlays may only add test source under tests/ or test/')
        files.append((path, relative))
    if not files:
        raise ValueError('Test overlay must contain new test files')
    command('git', '-C', source, 'checkout', '--quiet', '--detach', base)
    # Check every destination before copying any file. Existing tests and runtime
    # files are never replaced by an evaluator adapter.
    for _path, relative in files:
        for tree in (source, reference):
            target = tree / relative
            for parent in target.parents:
                if parent == tree:
                    break
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    raise ValueError('Test overlay parents must be real directories: ' + str(relative))
            if not target.resolve().is_relative_to(tree.resolve()) or target.exists() or target.is_symlink():
                raise ValueError('Test overlay must not overwrite or escape either source tree: ' + str(relative))
    hashes = {}
    for path, relative in files:
        for tree in (source, reference):
            target = tree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        hashes[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    command('git', '-C', source, 'add', '--', *(relative for _path, relative in files))
    command('git', '-C', source, 'commit', '--quiet', '-m', 'Add evaluator-owned preservation tests',
            env={**os.environ, 'GIT_AUTHOR_NAME': 'AutoCode Arena',
                 'GIT_AUTHOR_EMAIL': 'arena@example.invalid',
                 'GIT_COMMITTER_NAME': 'AutoCode Arena', 'GIT_COMMITTER_EMAIL': 'arena@example.invalid',
                 'GIT_AUTHOR_DATE': '2000-01-01T00:00:00Z', 'GIT_COMMITTER_DATE': '2000-01-01T00:00:00Z'})
    adapted = command('git', '-C', source, 'rev-parse', 'HEAD', capture_output=True, text=True).stdout.strip()
    return adapted, hashes


def prepare(destination, cases, *, catalog_root=CATALOG.parent, oracle_timeout=60):
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Choose a fresh Arena directory; existing evaluator data is never overwritten")
    cli = [sys.executable, ROOT / "tools/autocode_arena.py", "--arena", destination]
    command(*cli, "init")
    for case in cases:
        ident = case['id']
        source = destination / "sources" / ident
        reference = destination / "references" / ident
        source.mkdir(parents=True)
        command("git", "init", "-q", source)
        command("git", "-C", source, "remote", "add", "origin", case['repository'])
        command("git", "-C", source, "fetch", "--quiet", "--depth=1", "origin",
                case['base_commit'], case['reference_commit'])
        for revision in (case['base_commit'], case['reference_commit']):
            resolved = command("git", "-C", source, "rev-parse", "--verify", revision + "^{commit}",
                               capture_output=True, text=True).stdout.strip()
            if resolved != revision:
                raise ValueError(f"Pinned commit did not resolve: {revision}")
        tree = command("git", "-C", source, "archive", case['reference_commit'],
                       capture_output=True).stdout
        reference.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(tree)) as archive:
            archive.extractall(reference, filter="data")
        fixture = catalog_root / "cases" / ident
        base = case['base_commit']
        note = None
        preparation = None
        if case.get('test_overlay'):
            if case['test_overlay'] is not True:
                raise ValueError('test_overlay must be true when a case needs a native test adapter')
            overlay = fixture / 'test_overlay'
            if overlay.is_symlink():
                raise ValueError('Test overlay root cannot be a symlink')
            base, hashes = add_test_overlay(overlay, source, reference, base)
            preparation = {'upstream_base_commit': case['base_commit'], 'adapted_base_commit': base,
                           'upstream_reference_commit': case['reference_commit'], 'test_overlay_sha256': hashes}
            note = ('Evaluator setup adds identical native preservation tests to the pinned baseline and reference. '
                    'Runtime source remains the upstream baseline ' + case['base_commit'] + '. '
                    'Extend the existing adapter tests with regression cases; retain its preservation guards.')
        checks = [arg for name in case['checks'] for arg in ("--check", name)]
        command(*cli, "ingest", ident, "--repository", source, "--base", base,
                "--issue", case['issue_ref'], "--issue-file", fixture / "issue.json",
                "--oracle", fixture / "oracle.py", "--reference", reference,
                "--split", case['split'], "--oracle-timeout", oracle_timeout, *checks,
                *(['--note', note] if note else []),
                # Ingestion archives, stages and commits a frozen tree before
                # running its two separately bounded oracles. Allow their three
                # bulk Git budgets plus metadata/setup time (#619).
                timeout=2 * oracle_timeout + 3 * 600 + 180)
        if preparation:
            (destination / 'cases' / ident / 'preparation.json').write_text(json.dumps(preparation, indent=2) + '\n')
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arena", type=Path, default=ROOT / ".autocode/arena")
    parser.add_argument("--case", action="append", help="prepare only this case (repeatable)")
    parser.add_argument("--oracle-timeout", type=int, default=60,
                        help="deadline in seconds for each original/reference control")
    parser.add_argument("--list", action="store_true", help="list projects without downloading or running code")
    args = parser.parse_args(argv)
    if args.oracle_timeout <= 0:
        parser.error("--oracle-timeout must be positive")
    catalog = json.loads(CATALOG.read_text())
    cases = catalog['cases']
    if args.case:
        unknown = set(args.case) - {case['id'] for case in cases}
        if unknown:
            parser.error("Unknown cases: " + ", ".join(sorted(unknown)))
        cases = [case for case in cases if case['id'] in args.case]
    if args.list:
        print(json.dumps(cases, indent=2))
        return 0
    try:
        destination = prepare(args.arena, cases, oracle_timeout=args.oracle_timeout)
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        parser.exit(1, f"Arena preparation failed: {error}\n")
    print(f"Prepared {len(cases)} verified cases in {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
