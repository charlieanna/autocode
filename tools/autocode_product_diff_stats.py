"""Measure a changed tree using its already established product/test roles."""

from pathlib import Path


def summarize(workspace, base, changes, roles, *, staged, git, is_code, binary_lines, allow_ambiguous=False):
    lines, binary = {}, []
    with staged(workspace, base) as (env, _):
        numstat = git(workspace, "diff-index", "--cached", "--numstat", "-z", "--no-renames", base, "--", env=env)
    for record in numstat.split("\0"):
        parts = record.split("\t", 2)
        if len(parts) != 3 or parts[2] not in changes:
            continue
        if parts[0] == "-":
            binary.append(parts[2])
            lines[parts[2]] = (binary_lines, 0)
        else:
            lines[parts[2]] = (int(parts[0]), int(parts[1]))
    for path, status in changes.items():
        file = Path(workspace) / path
        if path not in lines and status == "added" and file.is_file():
            data = file.read_bytes()
            if b"\0" in data[:8000]:
                binary.append(path)
                lines[path] = (binary_lines, 0)
            else:
                lines[path] = (len(data.splitlines()), 0)
    tests, source, ambiguous = roles.partition(changes)
    if ambiguous and not allow_ambiguous:
        roles.is_test(ambiguous[0])
    return {
        "files": len(changes),
        "source_files": len(source),
        "test_files": len(tests),
        "lines_added": sum(a for a, _ in lines.values()),
        "lines_removed": sum(r for _, r in lines.values()),
        "source_lines_changed": sum(sum(lines.get(p, (0, 0))) for p in source),
        "binary_files": sorted(binary),
        "non_code_files": sorted(p for p in changes if not is_code(p)),
    }
