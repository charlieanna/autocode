"""Public visual-check behavior, including enforcement in real clean-copy replay."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import unittest
from unittest import mock

import autocode_check_replay as check_replay
import autocode_taskrun as taskrun
import autocode_util as util
import autocode_verify as verify
import autocode_visual_check as visual_check
from tests.visual_check_fixtures import Image, MODULE, VisualProject, offline_provider_environment, sha256


@unittest.skipIf(Image is None, "visual-check tests require the optional Pillow dependency")
class VisualCheckTests(unittest.TestCase):
    def project(self, **kwargs):
        project = VisualProject(**kwargs)
        self.addCleanup(project.close)
        return project

    def report(self, result, status):
        self.assertEqual({"PASS": 0, "FAIL": 1, "UNVERIFIED": 2}[status], result.returncode,
                         result.stdout + "\n" + result.stderr)
        try:
            report = json.loads(result.stdout)
        except ValueError:
            self.fail(f"stdout must be one complete JSON report: {result.stdout!r}; stderr={result.stderr!r}")
        self.assertEqual(status, report["status"], report)
        self.assertEqual("NOT_PERFORMED", report["independent_visual_review"])
        self.assertEqual("project-owned fixture, not authenticated browser provenance", report["capture_provenance"])
        return report

    def test_exact_capture_passes_without_changing_approved_inputs(self):
        project = self.project()
        original = project.inputs()
        before = util.snapshot(project.root)["revision"]
        output = ".autocode/visual-checks/exact"
        report = self.report(project.invoke(output=output), "PASS")
        self.assertEqual(["desktop"], [row["id"] for row in report["cases"]])
        case = report["cases"][0]
        self.assertEqual("PASS", case["status"])
        self.assertEqual(0, case["changed_pixels"])
        self.assertEqual(0, case["changed_ratio"])
        self.assertEqual(before, report["source_revision"])
        self.assertEqual(before, report["source_revision_after"])
        self.assertEqual(original, project.inputs())
        self.assertEqual(before, util.snapshot(project.root)["revision"])
        saved = json.loads((project.root / output / "report.json").read_text())
        self.assertEqual(report, saved)
        self.assertTrue((project.root / output / "captures/desktop.png").is_file())
        for artifact in case["artifacts"].values():
            path = project.root / output / artifact["path"]
            self.assertEqual(sha256(path), artifact["sha256"])
            self.assertTrue(path.resolve().is_relative_to(project.root / output))
        self.assertEqual(sha256(project.root / output / case["candidate"]["path"]), case["candidate"]["sha256"])

    def test_visual_defects_fail_even_though_functional_assertions_pass(self):
        project = self.project()
        original = project.inputs()
        for variant in ("shift", "missing-control"):
            with self.subTest(variant=variant):
                project.configure(variants={"desktop": variant})
                functional = subprocess.run([sys.executable, "-c",
                                             "from app import functional_value; assert functional_value() == 42"],
                                            cwd=project.root, capture_output=True, text=True, timeout=10)
                self.assertEqual(0, functional.returncode, functional.stderr)
                report = self.report(project.invoke(), "FAIL")
                self.assertEqual("FAIL", report["cases"][0]["status"])
                self.assertGreater(report["cases"][0]["changed_pixels"], 0)
                self.assertGreater(report["cases"][0]["changed_ratio"], 0)
                self.assertEqual(original, project.inputs(), "mismatches must never update the baseline or policy")

    def test_every_viewport_and_state_is_mandatory(self):
        cases = [{"id": "desktop", "state": "ready", "route": "/",
                  "viewport": {"width": 100, "height": 80, "device_scale_factor": 1}},
                 {"id": "mobile-empty", "state": "empty", "route": "/inbox",
                  "viewport": {"width": 80, "height": 100, "device_scale_factor": 1}},
                 {"id": "desktop-error", "state": "error", "route": "/inbox",
                  "viewport": {"width": 100, "height": 80, "device_scale_factor": 1}}]
        project = self.project(cases=cases)
        report = self.report(project.invoke(), "PASS")
        self.assertEqual({row["id"] for row in cases}, {row["id"] for row in report["cases"]})
        for case in cases:
            with self.subTest(case=case["id"]):
                project.configure(variants={case["id"]: "missing-control"})
                report = self.report(project.invoke(), "FAIL")
                self.assertEqual({case["id"]}, {row["id"] for row in report["cases"] if row["status"] == "FAIL"})
        project.configure(mode="missing")
        self.report(project.invoke(), "UNVERIFIED")

    def test_a_strict_control_region_overrides_the_global_difference_allowance(self):
        project = self.project()
        project.configure(variants={"desktop": "missing-control"})
        project.policy["cases"][0]["max_changed_ratio"] = 0.02
        project.pin_policy()
        allowed = self.report(project.invoke(), "PASS")
        self.assertGreater(allowed["cases"][0]["changed_pixels"], 0)
        project.policy["cases"][0]["regions"] = [
            {"id": "critical-control", "x": 80, "y": 64, "width": 10, "height": 8, "max_changed_ratio": 0.0}]
        project.pin_policy()
        strict = self.report(project.invoke(), "FAIL")
        self.assertLess(strict["cases"][0]["changed_ratio"], 0.02)
        self.assertEqual("FAIL", strict["cases"][0]["regions"][0]["status"])
        self.assertEqual(1.0, strict["cases"][0]["regions"][0]["changed_ratio"])

    def test_invalid_capture_inventories_and_images_are_unverified(self):
        project = self.project()
        for mode in ("no-inventory", "missing", "duplicate", "unknown-case", "bad-viewport", "bad-state",
                     "bad-route", "unknown-field", "malformed-inventory", "duplicate-key", "missing-image",
                     "corrupt", "truncated", "wrong-size", "escape", "symlink", "nonzero"):
            with self.subTest(mode=mode):
                project.configure(mode=mode)
                self.report(project.invoke(), "UNVERIFIED")

    def test_policy_is_strict_and_rejected_before_capture(self):
        project = self.project()
        original = copy.deepcopy(project.policy)
        bad_values = [(("version",), True), (("version",), 2), (("unknown",), "not allowed"),
                      (("timeout_seconds",), True), (("timeout_seconds",), 0),
                      (("timeout_seconds",), float("nan")), (("timeout_seconds",), float("inf")),
                      (("capture_command",), "python capture.py"), (("capture_command",), []),
                      (("capture_command",), [sys.executable, False]), (("cases",), []),
                      (("manifest", "unknown"), 1), (("manifest", "sha256"), "bad hash"),
                      (("manifest", "sha256"), None),
                      (("cases", 0, "id"), "absent"), (("cases", 0, "unknown"), True)]
        for value in (True, -1, 255, 256, 0.5, "0", float("nan")):
            bad_values.append((("cases", 0, "channel_tolerance"), value))
        for value in (True, -0.1, 1.0, 1.01, "0", float("nan"), float("inf"), -float("inf")):
            bad_values.append((("cases", 0, "max_changed_ratio"), value))
        region = {"id": "control", "x": 80, "y": 64, "width": 10, "height": 8, "max_changed_ratio": 0.0}
        for field, value in (("unknown", 1), ("x", -1), ("x", True), ("width", 0),
                             ("height", 0.5), ("max_changed_ratio", float("nan")),
                             ("max_changed_ratio", True)):
            bad_values.append((("cases", 0, "regions"), [{**region, field: value}]))
        bad_values += [(("cases",), [original["cases"][0], original["cases"][0]]),
                       (("cases", 0, "regions"), [region, region]),
                       (("cases",), [None]), (("cases", 0, "id"), []),
                       (("manifest",), []), (("capture_command",), [None]),
                       (("timeout_seconds",), 10 ** 400), (("cases", 0, "regions"), [None])]
        for keys, value in bad_values:
            with self.subTest(keys=keys, value=value):
                project.policy = copy.deepcopy(original)
                target = project.policy
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                project.pin_policy()
                self.report(project.invoke(), "UNVERIFIED")
                self.assertFalse((project.root / ".autocode/capture-invocations.txt").exists())
        for field in original:
            with self.subTest(missing=field):
                project.policy = copy.deepcopy(original)
                del project.policy[field]
                project.pin_policy()
                self.report(project.invoke(), "UNVERIFIED")

    def test_oversized_or_empty_declared_images_are_refused_before_capture(self):
        for width, height, dpr, scale in ((16384, 16384, 1, 1), (1440, 900, 8, 1), (1, 1, 1, 0.01)):
            with self.subTest(width=width, height=height, dpr=dpr, scale=scale):
                project = self.project()
                case = project.manifest["cases"][0]
                case["viewport"] = {"width": width, "height": height, "device_scale_factor": dpr}
                case["export_scale"] = scale
                project.pin_manifest()
                report = self.report(project.invoke(), "UNVERIFIED")
                self.assertIn("dimensions", " ".join(report["errors"]))
                self.assertFalse((project.root / ".autocode/capture-invocations.txt").exists())

    def test_malformed_json_and_duplicate_policy_keys_are_not_accepted(self):
        project = self.project()
        for text in ("", "[]", "null", "{broken", json.dumps(project.policy).replace(
                '"version": 1', '"version": 0, "version": 1', 1)):
            with self.subTest(text=text[:50]):
                project.policy_path.write_text(text)
                self.report(project.invoke(), "UNVERIFIED")

    def test_all_manifest_cases_must_be_in_the_policy(self):
        project = self.project()
        omitted = copy.deepcopy(project.manifest["cases"][0])
        omitted.update(id="unlisted-state", state="empty")
        project.manifest["cases"].append(omitted)
        project.pin_manifest()
        self.report(project.invoke(), "UNVERIFIED")

    def test_approved_policy_pin_rejects_tolerance_changes_and_rebaselining(self):
        project = self.project()
        approved = sha256(project.policy_path)
        project.policy["cases"][0]["max_changed_ratio"] = 1.0
        project.pin_policy()
        self.report(project.invoke(digest=approved), "UNVERIFIED")
        baseline = project.root / "design/desktop.png"
        Image.new("RGB", (100, 80), "black").save(baseline)
        project.manifest["cases"][0]["artifacts"]["screenshot"]["sha256"] = sha256(baseline)
        project.pin_manifest()
        self.report(project.invoke(digest=approved), "UNVERIFIED")
        self.assertFalse((project.root / ".autocode/capture-invocations.txt").exists())

    def test_manifest_baseline_and_context_hashes_are_pinned(self):
        for relative in ("design/manifest.json", "design/desktop.png", "design/context.json"):
            with self.subTest(input=relative):
                project = self.project()
                path = project.root / relative
                path.write_bytes(path.read_bytes() + b" ")
                self.report(project.invoke(), "UNVERIFIED")

    def test_null_manifest_pin_cannot_authorize_rebaselining(self):
        project = self.project()
        project.policy["manifest"]["sha256"] = None
        approved = project.pin_policy()
        self.report(project.invoke(digest=approved), "UNVERIFIED")
        baseline = project.root / "design/desktop.png"
        Image.new("RGB", (100, 80), "black").save(baseline)
        project.manifest["cases"][0]["artifacts"]["screenshot"]["sha256"] = sha256(baseline)
        project.manifest_path.write_text(json.dumps(project.manifest))
        self.assertEqual(approved, sha256(project.policy_path))
        self.report(project.invoke(digest=approved), "UNVERIFIED")
        self.assertFalse((project.root / ".autocode/capture-invocations.txt").exists())

    def test_rehashed_corrupt_baseline_is_not_visual_evidence(self):
        project = self.project()
        baseline = project.root / "design/desktop.png"
        baseline.write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
        project.manifest["cases"][0]["artifacts"]["screenshot"]["sha256"] = sha256(baseline)
        project.pin_manifest()
        self.report(project.invoke(), "UNVERIFIED")

    def test_input_symlinks_and_reference_path_escape_are_refused(self):
        for relative in ("visual-policy.json", "design/manifest.json", "design/desktop.png", "design/context.json"):
            with self.subTest(symlink=relative):
                project = self.project()
                path = project.root / relative
                target = project.root / (path.name + ".real")
                path.rename(target)
                path.symlink_to(target)
                self.report(project.invoke(), "UNVERIFIED")
        project = self.project()
        outside = project.root.parent / "outside.png"
        outside.write_bytes((project.root / "design/desktop.png").read_bytes())
        project.manifest["cases"][0]["artifacts"]["screenshot"]["path"] = "../../outside.png"
        project.pin_manifest()
        self.report(project.invoke(), "UNVERIFIED")

    def test_declared_implementation_inputs_must_exist_and_be_source_owned(self):
        for mode in ("missing", "ignored", "symlink", "ignored-in-directory"):
            with self.subTest(mode=mode):
                project = self.project()
                implementation = "missing.py"
                if mode == "ignored":
                    project.write("ignored.py", "# not reproducible in a source-only replay\n")
                    project.write(".gitignore", ".autocode/\n__pycache__/\nignored.py\n")
                    implementation = "ignored.py"
                elif mode == "symlink":
                    external = project.root.parent / "outside.py"
                    external.write_text("# outside source\n")
                    (project.root / "linked.py").symlink_to(external)
                    implementation = "linked.py"
                elif mode == "ignored-in-directory":
                    project.write("src/main.py", "# source\n")
                    project.write("src/local-only.py", "# ignored implementation\n")
                    project.write(".gitignore", ".autocode/\n__pycache__/\nsrc/local-only.py\n")
                    implementation = "src"
                project.manifest["cases"][0]["implementation_paths"] = [implementation]
                project.pin_manifest()
                self.report(project.invoke(), "UNVERIFIED")
                self.assertFalse((project.root / ".autocode/capture-invocations.txt").exists())

    def test_manifest_paths_resolve_relative_to_policy_and_artifacts_relative_to_manifest(self):
        project = self.project()
        (project.root / "bundle").mkdir()
        (project.root / "design").rename(project.root / "bundle/design")
        project.policy_path.rename(project.root / "bundle/visual-policy.json")
        project.policy_path = project.root / "bundle/visual-policy.json"
        project.manifest_path = project.root / "bundle/design/manifest.json"
        report = self.report(project.invoke(policy="bundle/visual-policy.json"), "PASS")
        self.assertEqual(0, report["cases"][0]["changed_pixels"])

    def test_policy_cannot_select_an_outside_manifest(self):
        project = self.project()
        outside = project.root.parent / "manifest.json"
        outside.write_bytes(project.manifest_path.read_bytes())
        project.policy["manifest"]["path"] = "../manifest.json"
        project.pin_policy()
        self.report(project.invoke(), "UNVERIFIED")

    def test_source_and_reference_mutations_during_capture_are_unverified(self):
        for mode in ("source-change", "baseline-change", "policy-change", "manifest-change", "context-change"):
            with self.subTest(mode=mode):
                project = self.project()
                project.configure(mode=mode)
                self.report(project.invoke(), "UNVERIFIED")

    def test_temporary_image_substitution_cannot_pass_with_original_pins(self):
        for kind in ("reference", "candidate"):
            with self.subTest(kind=kind):
                project = self.project()
                project.configure(variants={"desktop": "missing-control"})
                self.report(project.invoke(), "FAIL")
                approved = project.inputs()
                before = util.snapshot(project.root)["revision"]
                original_compare = visual_check.visual_diff.compare
                compared = []

                def substitute(reference, candidate, *args, **kwargs):
                    target, replacement = (reference, candidate) if kind == "reference" else (candidate, reference)
                    saved = target.read_bytes()
                    try:
                        target.write_bytes(replacement.read_bytes())
                        result = original_compare(reference, candidate, *args, **kwargs)
                        compared.append(result["status"])
                        return result
                    finally:
                        target.write_bytes(saved)

                with mock.patch.object(visual_check.visual_diff, "compare", side_effect=substitute):
                    report = visual_check.run(project.root, "visual-policy.json", sha256(project.policy_path))
                self.assertEqual(["PASS"], compared, "the real pixel comparison must exercise the substituted bytes")
                self.assertEqual("UNVERIFIED", report["status"])
                self.assertIn(f"Compared {kind} bytes", " ".join(report["errors"]))
                self.assertEqual(approved, project.inputs())
                self.assertEqual(before, util.snapshot(project.root)["revision"])

    def test_reaped_capture_group_is_not_signaled_again(self):
        project = self.project()
        output = project.root / ".autocode" / "worker-cleanup"
        output.mkdir(parents=True)
        util.atomic_json(output / "capture-result.json", {"exit_code": 0, "timed_out": False, "error": ""})
        process = mock.Mock()
        process.poll.return_value = -9
        with mock.patch.object(visual_check.subprocess, "Popen", return_value=process), \
                mock.patch.object(visual_check.os, "killpg") as kill:
            result = visual_check._capture([sys.executable, "capture.py"], project.root, output,
                                           project.manifest_path, 1)
        self.assertEqual(0, result["exit_code"])
        kill.assert_not_called()

    def test_capture_cannot_introduce_ignored_source_under_a_declared_directory(self):
        project = self.project()
        project.write("web/index.html", "<!doctype html><p>Source-owned page</p>\n")
        project.write(".gitignore", ".autocode/\n__pycache__/\nweb/generated.css\n")
        project.manifest["cases"][0]["implementation_paths"] = ["app.py", "web"]
        project.pin_manifest()
        self.report(project.invoke(), "PASS")
        project.configure(mode="ignored-source-change")
        before = util.snapshot(project.root)["revision"]
        self.report(project.invoke(), "UNVERIFIED")
        self.assertTrue((project.root / "web/generated.css").exists())
        self.assertEqual(before, util.snapshot(project.root)["revision"],
                         "the source-only Git snapshot alone cannot detect this ignored input")

    def test_existing_output_is_refused_without_overwriting_old_evidence(self):
        project = self.project()
        output = ".autocode/visual-checks/retained"
        self.report(project.invoke(output=output), "PASS")
        path = project.root / output
        before = {str(file.relative_to(path)): file.read_bytes() for file in path.rglob("*") if file.is_file()}
        project.configure(variants={"desktop": "shift"})
        self.report(project.invoke(output=output), "UNVERIFIED")
        self.assertEqual(before, {str(file.relative_to(path)): file.read_bytes()
                                  for file in path.rglob("*") if file.is_file()})

    def test_each_default_invocation_recaptures_into_a_fresh_directory(self):
        project = self.project()
        self.report(project.invoke(), "PASS")
        first = set((project.root / ".autocode/visual-checks").iterdir())
        project.configure(variants={"desktop": "shift"})
        self.report(project.invoke(), "FAIL")
        after = set((project.root / ".autocode/visual-checks").iterdir())
        self.assertEqual(1, len(first))
        self.assertEqual(2, len(after))
        invocations = (project.root / ".autocode/capture-invocations.txt").read_text().splitlines()
        self.assertEqual(2, len(set(invocations)))
        self.assertEqual("PASS", json.loads((next(iter(first)) / "report.json").read_text())["status"])

    def test_output_must_stay_in_autocode_without_symlink_parents(self):
        project = self.project()
        for output in ("evidence", "../outside", ".autocode/../outside", str(project.root.parent / "outside")):
            with self.subTest(output=output):
                self.report(project.invoke(output=output), "UNVERIFIED")
        outside = project.root.parent / "outside-output"
        outside.mkdir()
        (project.root / ".autocode").mkdir(exist_ok=True)
        (project.root / ".autocode/link").symlink_to(outside, target_is_directory=True)
        self.report(project.invoke(output=".autocode/link/attempt"), "UNVERIFIED")
        self.assertEqual([], list(outside.iterdir()))

    def test_capture_spawn_failure_is_unverified(self):
        project = self.project()
        project.policy["capture_command"] = [str(project.root / "no-such-executable")]
        project.pin_policy()
        self.report(project.invoke(), "UNVERIFIED")

    def test_capture_timeout_is_unverified_without_waiting_for_wall_clock(self):
        project = self.project()
        project.write("capture.py", "while True:\n    pass\n")
        project.policy["timeout_seconds"] = 0.01
        project.pin_policy()
        report = self.report(project.invoke(), "UNVERIFIED")
        self.assertIn("timed out", json.dumps(report).lower())

    def test_killing_the_runner_also_terminates_capture_and_its_descendant(self):
        import psutil

        project = self.project()
        with socket.create_server(("127.0.0.1", 0)) as server:
            server.settimeout(10)
            address = server.getsockname()
            handshake = ("import os, socket\n"
                         f"channel = socket.create_connection({address!r}, timeout=10)\n"
                         "channel.settimeout(None)\n"
                         "channel.sendall((str(os.getpid()) + '\\n').encode())\n"
                         "channel.recv(1)\n")
            project.write("capture.py", "import subprocess, sys\n"
                          f"subprocess.Popen([sys.executable, '-c', {handshake!r}], start_new_session=True)\n" + handshake)
            command = [sys.executable, str(MODULE), "--workspace", str(project.root),
                       "--policy", "visual-policy.json", "--policy-sha256", sha256(project.policy_path)]
            process = subprocess.Popen(command, cwd=project.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            descendants, connections, peer_pids = [], [], set()
            try:
                for _ in range(2):
                    channel, _ = server.accept()
                    channel.settimeout(10)
                    connections.append(channel)
                    line = b""
                    while not line.endswith(b"\n"):
                        chunk = channel.recv(1)
                        self.assertTrue(chunk, "fixture closed before reporting its PID")
                        line += chunk
                    peer_pids.add(int(line))
                self.assertEqual(2, len(peer_pids), "capture and child must independently reach the blocking handshake")
                descendants = psutil.Process(process.pid).children(recursive=True)
                process.kill()
                process.wait(timeout=10)
                for channel in connections:
                    self.assertEqual(b"", channel.recv(1), "runner death must close every capture descendant")
            finally:
                # Also clean up a broken implementation, so a red regression cannot leak a server/browser.
                if process.poll() is None:
                    descendants = psutil.Process(process.pid).children(recursive=True)
                    process.kill()
                for child in descendants:
                    try:
                        child.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                for channel in connections:
                    channel.close()
                process.communicate(timeout=10)

    def test_missing_pillow_has_an_explicit_unverified_report(self):
        project = self.project()
        bootstrap = ("import builtins, runpy, sys\n"
                     "original = builtins.__import__\n"
                     "def without_pillow(name, *args, **kwargs):\n"
                     "    if name == 'PIL' or name.startswith('PIL.'):\n"
                     "        raise ImportError('Pillow intentionally unavailable')\n"
                     "    return original(name, *args, **kwargs)\n"
                     "builtins.__import__ = without_pillow\n"
                     "sys.argv = sys.argv[1:]\n"
                     "sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[0]).parent))\n"
                     "runpy.run_path(sys.argv[0], run_name='__main__')\n")
        report = self.report(project.invoke(bootstrap=bootstrap), "UNVERIFIED")
        self.assertIn("pillow", json.dumps(report).lower())

    def test_standalone_module_file_has_the_same_public_contract(self):
        project = self.project()
        self.report(project.invoke(entry="file"), "PASS")

    @unittest.skipUnless(importlib.util.find_spec("autocode_cli"), "autocode_cli is not installed")
    def test_installed_module_has_the_same_public_contract(self):
        project = self.project()
        self.report(project.invoke(entry="module"), "PASS")

    def test_prescribed_visual_check_survives_validator_omission_and_scratch_cleanup(self):
        project = self.project()
        command = shlex.join([sys.executable, str(MODULE), "--workspace", ".", "--policy", "visual-policy.json",
                              "--policy-sha256", sha256(project.policy_path)])
        approved = {"goal_contract": {"body": {"acceptance_criteria": [
            {"id": "AC-visual", "human_review": False, "verification_method": command}]}},
                    "current_task": {"acceptance_criteria": ["AC-visual"], "validation_plan": []}}
        unrelated = shlex.join([sys.executable, "-c", "print('unrelated passing check')"])
        checks = [{"command": unrelated, "exit_code": 0, "evidence_ref": "event:validator"}]
        for variant, status in (("match", "PASS"), ("missing-control", "FAIL")):
            with self.subTest(variant=variant):
                project.configure(variants={"desktop": variant})
                run_dir = project.root / ".autocode" / ("replay-" + variant)
                record = {"output": "validator-01.json", "source_revision": util.snapshot(project.root)["revision"]}
                if status == "PASS":
                    result = check_replay.replay(checks, project.root, run_dir, record, verify.scratch_run,
                                                 approved_state=approved, timeout=30)
                    self.assertEqual("PASS", result["verdict"])
                else:
                    with self.assertRaisesRegex(ValueError, "autocode_visual_check.*exited 1"):
                        check_replay.replay(checks, project.root, run_dir, record, verify.scratch_run,
                                            approved_state=approved, timeout=30)
                [output] = (run_dir / "check-replay").glob("validator-01-*")
                receipt = json.loads((output / "replay.json").read_text())
                self.assertEqual(status, receipt["verdict"])
                self.assertEqual([unrelated, command], [row["command"] for row in receipt["checks"]])
                visual = receipt["checks"][1]
                self.assertEqual("approved-plan", visual["evidence_ref"])
                self.assertEqual(0 if status == "PASS" else 1, visual["exit_code"])
                log = Path(visual["output"]).read_bytes()
                self.assertEqual(hashlib.sha256(log).hexdigest(), visual["output_sha256"])
                report = json.loads(log)
                self.assertEqual(status, report["status"])
                self.assertEqual("desktop", report["cases"][0]["id"])
                self.assertEqual(status, report["cases"][0]["status"])
                self.assertIn("changed_pixels", report["cases"][0])
                if status == "FAIL":
                    self.assertGreater(report["cases"][0]["changed_pixels"], 0)
                self.assertEqual("NOT_PERFORMED", report["independent_visual_review"])
                self.assertGreater(len(log), check_replay.TAIL_CHARS, "the retained JSON must not be a tail excerpt")
                self.assertFalse((output / "check-02/scratch/tree").exists())
        self.assertFalse((project.root / ".autocode/visual-checks").exists(), "replay must not capture in the source tree")

    def test_task_run_cannot_complete_with_an_omitted_failing_visual_criterion(self):
        for variant in ("match", "missing-control"):
            with self.subTest(variant=variant):
                project = self.project()
                project.configure(variants={"desktop": variant})
                project.write("greet.py", "# Greeting CLI to be implemented by the offline fixture.\n")
                command = shlex.join([sys.executable, str(MODULE), "--workspace", ".", "--policy",
                                      "visual-policy.json", "--policy-sha256", sha256(project.policy_path)])
                env = offline_provider_environment(project, command)
                run = taskrun.TaskRun.start(project.root, "Build the offline greeting CLI and preserve the approved UI",
                                           options=("--engine", "codex", "--joint-planning",
                                                    "--astra-model", "gpt-6-astra", "--terra-model", "gpt-5.6-terra",
                                                    "--sol-model", "gpt-5.6-sol", "--completion-model", "gpt-6-astra",
                                                    "--glm-model", "gpt-5.6-sol", "--plan-reviewer-model", "gpt-6-astra"),
                                           env=env, timeout=90)
                view = run.advance_until_input()
                self.assertEqual("approve_plan", view["needs"]["kind"], view)
                run.approve_plan(view["needs"]["token"])
                view = run.advance_until_input()
                if variant == "match":
                    self.assertTrue(view["done"], view)
                    self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
                    checks = view["evidence"]["check_replay"]["checks"]
                    self.assertTrue(any(row["command"] == command and row["exit_code"] == 0
                                        for row in checks), checks)
                else:
                    self.assertFalse(view["done"], view)
                    self.assertIn("autocode_visual_check", view["stop_reason"])
                    self.assertIn("exited 1", view["stop_reason"])
                    reports = list(run.run_dir.glob("check-replay/**/replay.json"))
                    self.assertTrue(reports, view)
                    failed = [row for path in reports for row in json.loads(path.read_text())["checks"]
                              if row["command"] == command and row["exit_code"] == 1]
                    self.assertTrue(failed, reports)
                    report = json.loads(Path(failed[-1]["output"]).read_text())
                    self.assertEqual("FAIL", report["status"])
                    self.assertGreater(report["cases"][0]["changed_pixels"], 0)


if __name__ == "__main__":
    unittest.main()
