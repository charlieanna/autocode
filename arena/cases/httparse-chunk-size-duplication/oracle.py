"""Independent checks of a behavior-preserving deduplication in httparse; tests only a disposable copy.

Usage: python3 oracle.py WORKSPACE. Copies the candidate, adds the evaluator's own integration test
(expected results recorded from the pinned baseline) and runs it with the ``cargo`` on PATH; one more
check reads the candidate's parse_chunk_size.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

workspace = Path(sys.argv[1]).resolve()
CHECKS = ["lowercase_hex_sizes", "uppercase_and_mixed_hex_sizes", "overflow_and_invalid_refused",
          "extensions_and_partial_input", "hex_digit_branches_merged"]  # fmt: skip

TEST = r'''
#[test]
fn lowercase_hex_sizes() {
    let cases: &[(&[u8], &str)] = &[
        (b"a\r\n", "Ok(Complete((3, 10)))"),
        (b"1234abcd\r\n", "Ok(Complete((10, 305441741)))"),
        (b"abcdef\r\n", "Ok(Complete((8, 11259375)))"),
        (b"ffffffffffffffff\r\n", "Ok(Complete((18, 18446744073709551615)))"),
        (b"7fffffffffffffff\r\n", "Ok(Complete((18, 9223372036854775807)))"),
    ];
    for (input, expected) in cases {
        let got = format!("{:?}", httparse::parse_chunk_size(input));
        assert_eq!(&got, expected, "parse_chunk_size({:?})", String::from_utf8_lossy(input));
    }
}

#[test]
fn uppercase_and_mixed_hex_sizes() {
    let cases: &[(&[u8], &str)] = &[
        (b"A\r\n", "Ok(Complete((3, 10)))"),
        (b"fF\r\n", "Ok(Complete((4, 255)))"),
        (b"ABCDEF\r\n", "Ok(Complete((8, 11259375)))"),
        (b"8000000000000000\r\n", "Ok(Complete((18, 9223372036854775808)))"),
    ];
    for (input, expected) in cases {
        let got = format!("{:?}", httparse::parse_chunk_size(input));
        assert_eq!(&got, expected, "parse_chunk_size({:?})", String::from_utf8_lossy(input));
    }
}

#[test]
fn overflow_and_invalid_refused() {
    let cases: &[(&[u8], &str)] = &[
        (b"ffffffffffffffff0\r\n", "Err(InvalidChunkSize)"),
        (b"FFFFFFFFFFFFFFFF0\r\n", "Err(InvalidChunkSize)"),
        (b"g\r\n", "Err(InvalidChunkSize)"),
        (b"G\r\n", "Err(InvalidChunkSize)"),
        (b"5\rx", "Err(InvalidChunkSize)"),
    ];
    for (input, expected) in cases {
        let got = format!("{:?}", httparse::parse_chunk_size(input));
        assert_eq!(&got, expected, "parse_chunk_size({:?})", String::from_utf8_lossy(input));
    }
}

#[test]
fn extensions_and_partial_input() {
    let cases: &[(&[u8], &str)] = &[
        (b"0\r\n", "Ok(Complete((3, 0)))"),
        (b"5;ext=1\r\n", "Ok(Complete((9, 5)))"),
        (b"5 ; a=b\r\n", "Ok(Complete((9, 5)))"),
        (b"1", "Ok(Partial)"),
        (b"Ab", "Ok(Partial)"),
        (b"\r\n", "Ok(Complete((2, 0)))"),
        (b"5\r", "Ok(Partial)"),
        (b"0;\r\n", "Ok(Complete((4, 0)))"),
    ];
    for (input, expected) in cases {
        let got = format!("{:?}", httparse::parse_chunk_size(input));
        assert_eq!(&got, expected, "parse_chunk_size({:?})", String::from_utf8_lossy(input));
    }
}
'''


def merged():
    source = (workspace / "src/lib.rs").read_text()
    start = source.find("pub fn parse_chunk_size(")
    if start < 0:
        return False, "parse_chunk_size not found in src/lib.rs"
    end = source.find("\npub fn ", start + 1)
    body = source[start : end if end > 0 else len(source)]
    # One arm for decimal digits and one for hexadecimal letters of either case, or a single arm for all.
    guards = len(re.findall(r"count\s*>\s*15", body))
    return 1 <= guards <= 2, f"the sixteen-digit guard is written {guards} times in parse_chunk_size"


with tempfile.TemporaryDirectory(prefix="arena-httparse-") as folder:
    copy = Path(folder) / "crate"
    shutil.copytree(workspace, copy, ignore=shutil.ignore_patterns(".git", ".autocode", "target"))
    (copy / "tests").mkdir(exist_ok=True)
    (copy / "tests" / "arena_oracle.rs").write_text(TEST)
    done = subprocess.run(
        ["cargo", "test", "--quiet", "--test", "arena_oracle", "--", "--test-threads=1"],
        cwd=copy,
        capture_output=True,
        text=True,
        timeout=900,
        stdin=subprocess.DEVNULL,
        env={**__import__("os").environ, "CARGO_TARGET_DIR": str(Path(folder) / "target")},
    )
seen = dict(re.findall(r"^test (\w+) \.\.\. (ok|FAILED|ignored)$", done.stdout, re.M))
if not seen:
    # --quiet prints dots; run results are then read from the summary of failures.
    failed = set(re.findall(r"^---- (\w+) stdout ----$", done.stdout, re.M))
    finished = re.search(r"test result: (ok|FAILED)\. (\d+) passed", done.stdout)
    if finished:
        seen = {name: ("FAILED" if name in failed else "ok") for name in CHECKS[:-1]}
detail = (done.stderr or done.stdout)[-400:]
checks = [
    {"name": name, "ok": seen.get(name) == "ok", **({} if seen.get(name) == "ok" else {"detail": seen.get(name) or detail})}
    for name in CHECKS[:-1]
]
ok, why = merged()
checks.append({"name": "hex_digit_branches_merged", "ok": ok, **({} if ok else {"detail": why})})
print(json.dumps({"checks": checks}))
