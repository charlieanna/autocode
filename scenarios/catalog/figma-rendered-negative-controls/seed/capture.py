"""Project adapter: public source-bound acquisition into deterministic comparison.

This fixture is synthetic. Its visual_acceptance must remain None. No tools import.
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, reason):
    """Adapter refusal must remain active under -O/PYTHONOPTIMIZE."""
    if not condition:
        raise ValueError(reason)


def main():
    root = Path.cwd().resolve()
    output = Path(os.environ["AUTOCODE_VISUAL_OUTPUT"]).resolve()
    manifest_path = Path(os.environ["AUTOCODE_VISUAL_MANIFEST"]).resolve()
    require(
        output.is_relative_to(root / ".autocode") and output.is_dir() and not list(output.iterdir()),
        "Capture output must be a fresh empty directory below workspace .autocode",
    )
    require(manifest_path.is_relative_to(root), "Comparison manifest must be inside the workspace")
    identity = json.loads((root / "fixture-identity.json").read_text())
    require(identity["bound"] is True, "Unbound proposal is not executable")
    argv = identity["cli_argv"]
    require(
        isinstance(argv, list) and argv and all(isinstance(word, str) and word for word in argv),
        "Capture needs a nonempty pinned public CLI argv",
    )
    require(digest(identity["cli_entry_path"]) == identity["cli_entry_sha256"], "Public CLI entry changed")
    manifest = json.loads(manifest_path.read_text())
    require(len(manifest["cases"]) == 1, "Synthetic fixture requires exactly one complete case")
    case = manifest["cases"][0]
    expected = {key: case[key] for key in ("id", "state", "route", "viewport")}
    require(
        expected == identity["case"] and case["export_scale"] == 1,
        "Comparison case differs from bound fixture identity or native export scale",
    )
    proc = subprocess.run(
        [*argv, "visual-capture", "--config", "capture.json", "--timeout", "120"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=130,
    )
    require(proc.returncode == 0, "Public visual-capture failed: " + proc.stdout + proc.stderr)
    capture = json.loads(proc.stdout)
    require(capture["visual_acceptance"] is None, "Synthetic capture cannot grant visual acceptance")
    capture_path = Path(capture["capture_ref"]).resolve()
    candidate = Path(capture["candidate_ref"]).resolve()
    require(
        capture_path.is_relative_to(root / ".autocode" / "captures"),
        "Capture manifest must be inside workspace .autocode/captures",
    )
    require(candidate.is_relative_to(capture_path.parent), "Candidate must be inside its capture bundle")
    require(digest(capture_path) == capture["capture_sha256"], "Capture manifest pin changed")
    body = json.loads(capture_path.read_text())
    require(
        body["case"] == expected and body["reference_hash"] == digest(manifest_path),
        "Capture case or comparison manifest reference changed",
    )
    require(
        candidate == (root / body["artifacts"]["candidate"]["path"]).resolve(),
        "Returned candidate differs from retained capture artifact",
    )
    require(digest(candidate) == body["artifacts"]["candidate"]["sha256"], "Captured candidate pin changed")
    browser_path = (root / body["artifacts"]["browser"]["path"]).resolve()
    require(browser_path.is_relative_to(capture_path.parent), "Browser receipt must be inside capture bundle")
    require(digest(browser_path) == body["artifacts"]["browser"]["sha256"], "Browser receipt pin changed")
    browser = json.loads(browser_path.read_text())
    require(browser["status"] == "CAPTURED" and not browser["errors"], "Browser capture did not finish cleanly")
    require(browser["viewport"] == case["viewport"], "Browser viewport differs from comparison case")
    require(browser["screenshot_sha256"] == digest(candidate), "Browser screenshot pin differs from candidate")
    require(browser["setup"] is True and browser["teardown"] is True, "Browser setup or teardown did not finish")
    target = output / (case["id"] + ".png")
    with target.open("xb") as stream:
        stream.write(candidate.read_bytes())
    require(digest(target) == digest(candidate), "Copied comparison image differs from original capture")
    row = {
        "id": case["id"],
        "path": target.name,
        "state": case["state"],
        "route": case["route"],
        "viewport": case["viewport"],
    }
    with (output / "captures.json").open("x") as stream:
        json.dump({"version": 1, "cases": [row]}, stream)
    with (output / "source-bound-capture.json").open("x") as stream:
        json.dump({"capture": capture, "case": expected, "visual_acceptance": None}, stream)
    print("Protected Run mouse, Tab and Enter assertions passed; independent image review not performed")


if __name__ == "__main__":
    main()
