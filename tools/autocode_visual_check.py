"""Render and compare pinned references as one replayable verification command.

The project owns capture setup and browser assertions. This command establishes
fresh invocation, unchanged source/inputs and pixel results, NOT browser provenance
or an independent visual-review verdict. The complete report is emitted to stdout
because the ordinary verification runner removes its scratch worktree afterward.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

import psutil

try:
    from . import autocode_util as util, autocode_visual_policy as policy
    from . import autocode_visual_diff as visual_diff
except ImportError:
    import autocode_util as util, autocode_visual_policy as policy
    import autocode_visual_diff as visual_diff


def _capture_worker():
    """Own a capture group until its result or until the invoking checker disappears."""
    if os.getpgrp() != os.getpid():
        raise ValueError("Capture worker requires its own process group")
    config = json.loads(sys.stdin.readline())

    def stop_group():
        # Browser drivers may put their own children in additional process groups.
        try:
            for child in psutil.Process().children(recursive=True):
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        finally:
            os.killpg(os.getpgrp(), signal.SIGKILL)

    def parent_closed():
        sys.stdin.read()
        stop_group()

    # The pipe belongs only to the checker. Outer replay can SIGKILL it, so a
    # checker-side finally or signal handler alone cannot clean up this group.
    threading.Thread(target=parent_closed, daemon=True).start()
    result = {"exit_code": None, "timed_out": False, "error": ""}
    try:
        process = subprocess.Popen(config["command"], stdin=subprocess.DEVNULL)
        try:
            result["exit_code"] = process.wait(timeout=config["timeout"])
        except subprocess.TimeoutExpired:
            result["timed_out"] = True
    except (OSError, ValueError) as error:
        result["error"] = str(error)
    finally:
        try:
            util.atomic_json(config["result"], result)
        finally:
            stop_group()


def _capture(command, workspace, output, manifest, timeout):
    env = {**os.environ, "AUTOCODE_VISUAL_OUTPUT": str(output / "captures"),
           "AUTOCODE_VISUAL_MANIFEST": str(manifest), "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.monotonic()
    with (output / "capture.log").open("xb") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--capture-worker"],
                                   cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True, text=True)
        try:
            process.stdin.write(json.dumps({"command": command, "timeout": timeout,
                                            "result": str(output / "capture-result.json")}) + "\n")
            process.stdin.flush()
            process.wait(timeout=timeout + 10)
        except subprocess.TimeoutExpired as error:
            raise ValueError(f"Capture timed out after {timeout} seconds") from error
        finally:
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
    receipt, result_hash = policy.read_json(policy.contained(output, "capture-result.json"))
    policy.object_fields(receipt, "exit_code timed_out error", "capture result")
    if receipt["timed_out"]:
        raise ValueError(f"Capture timed out after {timeout} seconds")
    if receipt["error"]:
        raise ValueError(f"Capture could not run: {receipt['error']}")
    code = receipt["exit_code"]
    if type(code) is not int:
        raise ValueError("Capture has no process exit code")
    if code:
        raise ValueError(f"Capture command exited {code}; inspect capture.log")
    return {"command": command, "exit_code": code, "duration_seconds": time.monotonic() - started,
            "result": {"path": "capture-result.json", "sha256": result_hash}}


def _captures(root, cases):
    path = policy.contained(root, "captures.json")
    body, _ = policy.read_json(path)
    policy.object_fields(body, "version cases", "capture inventory")
    if type(body["version"]) is not int or body["version"] != 1 or not isinstance(body["cases"], list):
        raise ValueError("Invalid capture inventory version/cases")
    found, paths = {}, set()
    for item in body["cases"]:
        policy.object_fields(item, "id path state route viewport", "capture")
        key = item["id"]
        if not isinstance(key, str) or key not in cases or key in found:
            raise ValueError("Duplicate or unknown capture case")
        reference = cases[key]
        if (item["state"] != reference["state"] or item["route"] != reference["route"]
                or policy.viewport(item["viewport"]) != reference["viewport"]):
            raise ValueError(f"Capture state/route/viewport differs from reference: {key}")
        candidate = policy.contained(root, item["path"])
        if candidate in paths:
            raise ValueError("Each case requires a separate fresh capture path")
        paths.add(candidate)
        found[key] = candidate
    if found.keys() != cases.keys():
        raise ValueError("Capture inventory must cover every reference case exactly once")
    return found


def run(workspace, policy_path, policy_sha256, *, output=None):
    workspace = Path(workspace).resolve()
    report = {"version": 1, "kind": "deterministic_visual_comparison", "status": "UNVERIFIED", "cases": [],
              "independent_visual_review": "NOT_PERFORMED",
              "capture_provenance": "project-owned fixture, not authenticated browser provenance",
              "errors": []}
    destination = None
    try:
        loaded = policy.load(workspace, policy_path, policy_sha256)
        report.update(policy_sha256=loaded["policy_sha256"], manifest_sha256=loaded["manifest_sha256"],
                      required_cases=list(loaded["cases"]), input_hashes=loaded["pins"],
                      engine={Path(__file__).name: util.file_hash(__file__),
                              Path(policy.__file__).name: util.file_hash(policy.__file__),
                              Path(visual_diff.__file__).name: util.file_hash(visual_diff.__file__)})
        for case in loaded["cases"].values():
            for scale in (case["export_scale"], case["viewport"]["device_scale_factor"]):
                width, height = (round(case["viewport"][axis] * scale) for axis in ("width", "height"))
                if min(width, height) < 1 or width * height > visual_diff.MAX_PIXELS:
                    raise ValueError(f"Declared image dimensions for {case['id']} exceed comparison limits")
        before = util.snapshot(workspace)
        if any(name not in before["files"] for name in loaded["pins"]):
            raise ValueError("Visual policy and reference inputs must be source-owned, not ignored/run artifacts")
        report["source_revision"] = before["revision"]
        report["implementation_inputs"] = policy.implementation_inputs(workspace, loaded["cases"], before)
        relative = output or f".autocode/visual-checks/{uuid.uuid4().hex}"
        parts = policy.relative(relative).parts
        if len(parts) < 3 or parts[:2] != (".autocode", "visual-checks"):
            raise ValueError("Output must be a new directory below .autocode/visual-checks/")
        target = policy.contained(workspace, relative, is_file=False)
        target.mkdir(parents=True, exist_ok=False)
        destination = target
        (destination / "captures").mkdir()
        (destination / "comparisons").mkdir()
        report["output"] = str(destination.relative_to(workspace))
        report["capture"] = _capture(loaded["policy"]["capture_command"], workspace, destination,
                                     loaded["manifest_path"], loaded["policy"]["timeout_seconds"])
        captures = _captures(destination / "captures", loaded["cases"])
        capture_hashes = {str(path.relative_to(destination)): util.file_hash(path) for path in captures.values()}
        capture_hashes["captures/captures.json"] = util.file_hash(destination / "captures" / "captures.json")
        for key, case in loaded["cases"].items():
            reference = policy.contained(loaded["manifest_path"].parent, case["artifacts"]["screenshot"]["path"])
            check = loaded["checks"][key]
            result = visual_diff.compare(reference, captures[key], destination / "comparisons" / key,
                                         viewport=case["viewport"], export_scale=case["export_scale"],
                                         channel_tolerance=check["channel_tolerance"],
                                         max_changed_ratio=check["max_changed_ratio"], regions=check["regions"])
            candidate_hash = capture_hashes[str(captures[key].relative_to(destination))]
            for kind, expected in (("reference", case["artifacts"]["screenshot"]["sha256"]),
                                   ("candidate", candidate_hash)):
                if result[f"{kind}_sha256"] != expected:
                    raise ValueError(f"Compared {kind} bytes differ from pinned evidence: {key}")
            for artifact in result["artifacts"].values():
                artifact["path"] = str(Path(artifact["path"]).relative_to(destination))
            report["cases"].append({**result, "id": key, "file_key": case["file_key"], "node_id": case["node_id"],
                                    "state": case["state"], "route": case["route"], "viewport": case["viewport"],
                                    "export_scale": case["export_scale"], "implementation_paths": case["implementation_paths"],
                                    "policy": check, "reference_sha256": case["artifacts"]["screenshot"]["sha256"],
                                    "candidate": {"path": str(captures[key].relative_to(destination)),
                                                  "sha256": capture_hashes[str(captures[key].relative_to(destination))]}})
        # Re-read pinned files even if their content is excluded from an unusual Git snapshot.
        policy.load(workspace, policy_path, policy_sha256)
        if any(util.file_hash(policy.contained(destination, name)) != digest for name, digest in capture_hashes.items()):
            raise ValueError("Capture artifacts changed during comparison")
        after = util.snapshot(workspace)
        report["source_revision_after"] = after["revision"]
        policy.implementation_inputs(workspace, loaded["cases"], after)
        if after["revision"] != before["revision"]:
            raise ValueError("Source changed during visual capture/comparison; render the current source again")
        report["status"] = "PASS" if all(case["status"] == "PASS" for case in report["cases"]) else "FAIL"
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        report["errors"].append(str(error))
        report["status"] = "UNVERIFIED"
    if report["errors"]:
        report["summary"] = report["errors"][0][:500]
    else:
        failed = [case for case in report["cases"] if case["status"] == "FAIL"]
        report["summary"] = (f"{len(failed)} case(s) differ: " + "; ".join(
            f"{case['id']}: {case['changed_pixels']}/{case['total_pixels']} pixels, bounds {case['bbox']}"
            for case in failed[:4]))[:500] if failed else "All declared pixel comparisons passed; independent visual review not performed"
    if destination:
        try:
            log = destination / "capture.log"
            if log.is_file():
                report["capture_log"] = {"path": "capture.log", "sha256": util.file_hash(log)}
            # Only this invocation's newly-created directory is written; older attempts are never replaced.
            util.atomic_json(destination / "report.json", report)
        except OSError as error:
            report["status"] = "UNVERIFIED"
            report["summary"] = f"Cannot retain comparison report: {error}"
            report["errors"].append(report["summary"])
    return report


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--policy", required=True, help="Source-owned policy path relative to workspace")
    parser.add_argument("--policy-sha256", required=True, help="Policy digest pinned in the approved verification command")
    parser.add_argument("--output", help="New relative directory under .autocode/visual-checks/")
    args = parser.parse_args(argv)
    report = run(args.workspace, args.policy, args.policy_sha256, output=args.output)
    print(json.dumps(report, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "UNVERIFIED": 2}[report["status"]]


if __name__ == "__main__":
    if sys.argv[1:] == ["--capture-worker"]:
        _capture_worker()
    else:
        raise SystemExit(cli())
