"""Independent behavior checks of cast's float-string conversion; builds and tests only a disposable copy.

Usage: python3 oracle.py WORKSPACE. Copies the candidate, adds the evaluator's own external test file
and runs it with the ``go`` on PATH; each check is one subtest of TestArena.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

workspace = Path(sys.argv[1]).resolve()
CHECKS = ["string_floats_truncate", "json_number_floats_truncate", "whole_and_invalid_strings_unchanged",
          "time_from_json_number_unchanged", "float_and_duration_unchanged"]  # fmt: skip

TEST = r'''
package cast_test

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/spf13/cast"
)

func want[T comparable](t *testing.T, label string, got T, err error, expected T) {
	t.Helper()
	if err != nil || got != expected {
		t.Errorf("%s = %v, %v; want %v", label, got, err, expected)
	}
}

func refused[T any](t *testing.T, label string, got T, err error) {
	t.Helper()
	if err == nil {
		t.Errorf("%s = %v; want an error", label, got)
	}
}

func TestArena(t *testing.T) {
	t.Run("string_floats_truncate", func(t *testing.T) {
		v, err := cast.ToIntE("10.9")
		want(t, `ToIntE("10.9")`, v, err, 10)
		v64, err := cast.ToInt64E("-2.5")
		want(t, `ToInt64E("-2.5")`, v64, err, int64(-2))
		v32, err := cast.ToInt32E("3.")
		want(t, `ToInt32E("3.")`, v32, err, int32(3))
		u8, err := cast.ToUint8E("10.9")
		want(t, `ToUint8E("10.9")`, u8, err, uint8(10))
		u, err := cast.ToUintE("7.25")
		want(t, `ToUintE("7.25")`, u, err, uint(7))
		if got := cast.ToInt16("12.75"); got != 12 {
			t.Errorf(`ToInt16("12.75") = %v; want 12`, got)
		}
	})
	t.Run("json_number_floats_truncate", func(t *testing.T) {
		v, err := cast.ToIntE(json.Number("7.9"))
		want(t, `ToIntE(json.Number("7.9"))`, v, err, 7)
		v64, err := cast.ToInt64E(json.Number("-4.5"))
		want(t, `ToInt64E(json.Number("-4.5"))`, v64, err, int64(-4))
	})
	t.Run("whole_and_invalid_strings_unchanged", func(t *testing.T) {
		v, err := cast.ToIntE("10")
		want(t, `ToIntE("10")`, v, err, 10)
		v, err = cast.ToIntE("10.0")
		want(t, `ToIntE("10.0")`, v, err, 10)
		v, err = cast.ToIntE("0x1F")
		want(t, `ToIntE("0x1F")`, v, err, 31)
		v, err = cast.ToIntE("abc")
		refused(t, `ToIntE("abc")`, v, err)
		v, err = cast.ToIntE(" 7.5")
		refused(t, `ToIntE(" 7.5")`, v, err)
		v, err = cast.ToIntE("")
		want(t, `ToIntE("")`, v, err, 0)
	})
	t.Run("time_from_json_number_unchanged", func(t *testing.T) {
		tm, err := cast.ToTimeE(json.Number("1700000000.0"))
		want(t, `ToTimeE(json.Number("1700000000.0"))`, tm.Unix(), err, int64(1700000000))
		tm, err = cast.ToTimeE(json.Number("7"))
		want(t, `ToTimeE(json.Number("7"))`, tm.Unix(), err, int64(7))
		tm, err = cast.ToTimeE(json.Number("7.9"))
		refused(t, `ToTimeE(json.Number("7.9"))`, tm, err)
		tm, err = cast.ToTimeE(json.Number("1700000000.5"))
		refused(t, `ToTimeE(json.Number("1700000000.5"))`, tm, err)
	})
	t.Run("float_and_duration_unchanged", func(t *testing.T) {
		f, err := cast.ToFloat64E("10.9")
		want(t, `ToFloat64E("10.9")`, f, err, 10.9)
		d, err := cast.ToDurationE("1.5")
		want(t, `ToDurationE("1.5")`, d, err, time.Duration(1))
		d, err = cast.ToDurationE("2.5s")
		want(t, `ToDurationE("2.5s")`, d, err, 2500*time.Millisecond)
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


with tempfile.TemporaryDirectory(prefix="arena-cast-") as folder:
    copy = Path(folder) / "module"
    shutil.copytree(workspace, copy, ignore=shutil.ignore_patterns(".git", ".autocode"))
    (copy / "zz_arena_oracle_test.go").write_text(TEST)
    done = subprocess.run(
        ["go", "test", "-json", "-count=1", "-run", "^TestArena$", "."],
        cwd=copy,
        capture_output=True,
        text=True,
        timeout=600,
        stdin=subprocess.DEVNULL,
    )
    seen = outcome(done.stdout)
detail = (done.stderr or done.stdout)[-400:]
print(json.dumps({"checks": [
    {"name": name, "ok": seen.get(name) == "pass", **({} if seen.get(name) == "pass" else {"detail": seen.get(name) or detail})}
    for name in CHECKS
]}))  # fmt: skip
