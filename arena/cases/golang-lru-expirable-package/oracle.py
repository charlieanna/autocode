"""Independent checks of moving golang-lru's expirable cache into its own package; tests only a copy.

Usage: python3 oracle.py WORKSPACE. Copies the candidate, adds two evaluator-owned test packages (one
importing simplelru, one importing the new expirable package, so a missing package fails only its own
checks) and runs them with the ``go`` on PATH. Each behavior check is one subtest; two more read the
candidate's sources.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

workspace = Path(sys.argv[1]).resolve()
MODULE = "github.com/hashicorp/golang-lru/v2"
CHECKS = ["simplelru_unchanged", "expirable_package_caches", "expirable_entries_expire",
          "expirable_evict_callback", "simplelru_has_no_expirable_cache", "list_written_once"]  # fmt: skip

SIMPLE = r'''
package arenaoraclesimple

import (
	"reflect"
	"testing"

	"github.com/hashicorp/golang-lru/v2/simplelru"
)

func TestArena(t *testing.T) {
	t.Run("simplelru_unchanged", func(t *testing.T) {
		var evicted []int
		cache, err := simplelru.NewLRU[int, string](2, func(k int, _ string) { evicted = append(evicted, k) })
		if err != nil {
			t.Fatal(err)
		}
		cache.Add(1, "a")
		cache.Add(2, "b")
		if v, ok := cache.Get(1); !ok || v != "a" {
			t.Fatalf("Get(1) = %q, %v", v, ok)
		}
		cache.Add(3, "c")
		if !reflect.DeepEqual(cache.Keys(), []int{1, 3}) || !reflect.DeepEqual(evicted, []int{2}) {
			t.Fatalf("keys %v, evicted %v", cache.Keys(), evicted)
		}
		if n := cache.Resize(1); n != 1 || cache.Len() != 1 {
			t.Fatalf("Resize(1) evicted %d, len %d", n, cache.Len())
		}
		var _ simplelru.LRUCache[int, string] = cache
	})
}
'''

EXPIRING = r'''
package arenaoracleexpirable

import (
	"reflect"
	"testing"
	"time"

	"github.com/hashicorp/golang-lru/v2/expirable"
)

func TestArena(t *testing.T) {
	t.Run("expirable_package_caches", func(t *testing.T) {
		var cache *expirable.LRU[string, int] = expirable.NewExpirableLRU[string, int](2, nil, time.Hour)
		defer cache.Close()
		cache.Add("a", 1)
		cache.Add("b", 2)
		cache.Get("a")
		cache.Add("c", 3)
		if !reflect.DeepEqual(cache.Keys(), []string{"a", "c"}) {
			t.Fatalf("keys %v", cache.Keys())
		}
		if v, ok := cache.Peek("c"); !ok || v != 3 || cache.Contains("b") {
			t.Fatalf("Peek(c) = %d, %v; Contains(b) = %v", v, ok, cache.Contains("b"))
		}
	})
	t.Run("expirable_entries_expire", func(t *testing.T) {
		cache := expirable.NewExpirableLRU[string, int](10, nil, 40*time.Millisecond)
		defer cache.Close()
		cache.Add("x", 1)
		if _, ok := cache.Get("x"); !ok {
			t.Fatal("fresh entry missing")
		}
		time.Sleep(200 * time.Millisecond)
		if _, ok := cache.Get("x"); ok || cache.Len() != 0 {
			t.Fatalf("entry outlived its ttl (len %d)", cache.Len())
		}
	})
	t.Run("expirable_evict_callback", func(t *testing.T) {
		var evicted []string
		var onEvict expirable.EvictCallback[string, int] = func(k string, _ int) { evicted = append(evicted, k) }
		cache := expirable.NewExpirableLRU[string, int](1, onEvict, time.Hour)
		defer cache.Close()
		cache.Add("a", 1)
		cache.Add("b", 2)
		if !reflect.DeepEqual(evicted, []string{"a"}) {
			t.Fatalf("evicted %v", evicted)
		}
	})
}
'''


def outcome(stream: str) -> dict:
    seen = {}
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        name = event.get("Test") or ""
        if name.startswith("TestArena/") and event.get("Action") in ("pass", "fail", "skip"):
            seen[name.split("/", 1)[1]] = event["Action"]
    return seen


def sources():
    return {
        path.relative_to(workspace).as_posix(): path.read_text(errors="replace")
        for path in workspace.rglob("*.go")
        if not path.name.endswith("_test.go") and ".git" not in path.parts
    }


def source_checks():
    files = sources()
    leftover = [name for name, text in files.items() if name.startswith("simplelru/") and "Expirable" in text]
    lists = sorted(
        name for name, text in files.items() if re.search(r"^func \(\w+ \*\w+(\[[^\]]*\])?\) (?i:moveToFront|pushFront)\(", text, re.M)
    )
    outside = [name for name in lists if not name.startswith("internal/")]
    return {
        "simplelru_has_no_expirable_cache": (not leftover, "still in " + ", ".join(leftover)),
        "list_written_once": (len(lists) == 1 and not outside, "list implementations: " + ", ".join(lists or ["none"])),
    }


with tempfile.TemporaryDirectory(prefix="arena-lru-") as folder:
    copy = Path(folder) / "module"
    shutil.copytree(workspace, copy, ignore=shutil.ignore_patterns(".git", ".autocode"))
    for directory, text in (("arena_oracle_simple", SIMPLE), ("arena_oracle_expirable", EXPIRING)):
        (copy / directory).mkdir()
        (copy / directory / "arena_test.go").write_text(text)
    done = subprocess.run(
        ["go", "test", "-json", "-count=1", "-run", "^TestArena$", "./arena_oracle_simple", "./arena_oracle_expirable"],
        cwd=copy,
        capture_output=True,
        text=True,
        timeout=600,
        stdin=subprocess.DEVNULL,
    )
    seen = outcome(done.stdout)
detail = (done.stderr or done.stdout)[-400:]
static = source_checks()
checks = []
for name in CHECKS:
    if name in static:
        ok, why = static[name]
        checks.append({"name": name, "ok": ok, **({} if ok else {"detail": why})})
    else:
        ok = seen.get(name) == "pass"
        checks.append({"name": name, "ok": ok, **({} if ok else {"detail": seen.get(name) or detail})})
print(json.dumps({"checks": checks}))
