"""Download pinned starter projects and ingest verified controls through Arena's CLI.

This preparation tool never invokes a model. Each invocation needs a fresh Arena
directory; partial failed preparations are retained for diagnosis.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path(__file__).with_name("catalog.json")


def command(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, timeout=180, **kwargs)


def prepare(destination, cases, *, catalog_root=CATALOG.parent):
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
        checks = [arg for name in case['checks'] for arg in ("--check", name)]
        command(*cli, "ingest", ident, "--repository", source, "--base", case['base_commit'],
                "--issue", case['issue_ref'], "--issue-file", fixture / "issue.json",
                "--oracle", fixture / "oracle.py", "--reference", reference,
                "--split", case['split'], *checks)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arena", type=Path, default=ROOT / ".autocode/arena")
    parser.add_argument("--case", action="append", help="prepare only this case (repeatable)")
    parser.add_argument("--list", action="store_true", help="list projects without downloading or running code")
    args = parser.parse_args(argv)
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
        destination = prepare(args.arena, cases)
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        parser.exit(1, f"Arena preparation failed: {error}\n")
    print(f"Prepared {len(cases)} verified cases in {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
