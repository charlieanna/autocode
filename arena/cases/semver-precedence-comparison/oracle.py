"""Independent behavior checks of semver's precedence comparison; tests only a disposable copy.

Usage: python3 oracle.py WORKSPACE. Copies the candidate, adds the evaluator's own integration test and
runs it with the ``cargo`` on PATH. The test names the new method, so on code without it every check fails.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

workspace = Path(sys.argv[1]).resolve()
CHECKS = ["ignores_build_metadata", "orders_major_minor_patch", "orders_prerelease", "sorts_stably",
          "total_order_unchanged"]  # fmt: skip

TEST = r'''
use semver::Version;
use std::cmp::Ordering;

fn v(text: &str) -> Version {
    Version::parse(text).unwrap()
}

#[test]
fn ignores_build_metadata() {
    assert_eq!(v("1.2.3+a").cmp_precedence(&v("1.2.3+b")), Ordering::Equal);
    assert_eq!(v("1.2.3").cmp_precedence(&v("1.2.3+build.7")), Ordering::Equal);
    assert_eq!(v("1.2.3-rc.1+x").cmp_precedence(&v("1.2.3-rc.1")), Ordering::Equal);
}

#[test]
fn orders_major_minor_patch() {
    assert_eq!(v("1.2.3").cmp_precedence(&v("1.2.4")), Ordering::Less);
    assert_eq!(v("1.10.0").cmp_precedence(&v("1.9.9")), Ordering::Greater);
    assert_eq!(v("2.0.0+z").cmp_precedence(&v("10.0.0+a")), Ordering::Less);
}

#[test]
fn orders_prerelease() {
    assert_eq!(v("1.0.0-alpha").cmp_precedence(&v("1.0.0")), Ordering::Less);
    assert_eq!(v("1.0.0-alpha.1").cmp_precedence(&v("1.0.0-alpha.beta")), Ordering::Less);
    assert_eq!(v("1.0.0-rc.11+b").cmp_precedence(&v("1.0.0-rc.2+a")), Ordering::Greater);
}

#[test]
fn sorts_stably() {
    let mut versions = vec![v("1.20.0+c144a98"), v("1.20.0"), v("1.0.0"), v("1.0.0-alpha"), v("1.20.0+bc17664")];
    versions.sort_by(Version::cmp_precedence);
    let shown: Vec<String> = versions.iter().map(|x| x.to_string()).collect();
    assert_eq!(shown, ["1.0.0-alpha", "1.0.0", "1.20.0+c144a98", "1.20.0", "1.20.0+bc17664"]);
}

#[test]
fn total_order_unchanged() {
    assert_eq!(v("1.2.3+a").cmp(&v("1.2.3+b")), Ordering::Less);
    assert!(v("1.2.3+a") != v("1.2.3+b"));
    let mut versions = vec![v("1.20.0+c144a98"), v("1.20.0"), v("1.20.0+bc17664")];
    versions.sort();
    let shown: Vec<String> = versions.iter().map(|x| x.to_string()).collect();
    assert_eq!(shown, ["1.20.0", "1.20.0+bc17664", "1.20.0+c144a98"]);
    let _: fn(&Version, &Version) -> Ordering = Version::cmp_precedence;
}
'''


with tempfile.TemporaryDirectory(prefix="arena-semver-") as folder:
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
        seen = {name: ("FAILED" if name in failed else "ok") for name in CHECKS}
detail = (done.stderr or done.stdout)[-400:]
checks = [
    {"name": name, "ok": seen.get(name) == "ok", **({} if seen.get(name) == "ok" else {"detail": seen.get(name) or detail})}
    for name in CHECKS
]
print(json.dumps({"checks": checks}))
