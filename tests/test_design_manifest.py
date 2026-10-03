"""Offline design inventory and public CLI coverage; no models or Figma writes."""
import copy
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

import autocode_completion as completion
import autocode_design_coverage as coverage
import autocode_design_manifest as manifest
import autocode_report_schema as reports
import autocode_run_view as run_view
import autocode_taskrun as taskrun
import autocode_util as util

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ("Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
         "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
         "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
         "Python standard library only.")
OPTIONS = ("--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
           "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--completion-model",
           "gpt-6-astra", "--glm-model", "gpt-5.6-sol", "--plan-reviewer-model", "gpt-6-astra")


def png(path, width=2, height=1):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    pixels = zlib.compress((b"\x00" + b"\x11\x22\x33" * width) * height)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + header + chunk(b"IDAT", pixels) + chunk(b"IEND", b""))


def bundle(root):
    root.mkdir(parents=True, exist_ok=True)
    png(root / "screen.png")
    (root / "context.txt").write_text("Fixture tokens: spacing 8; foreground #112233; route /greet")
    artifacts = {kind: {"path": path, "sha256": util.file_hash(root / path)} for kind, path in
                 (("screenshot", "screen.png"), ("design_context", "context.txt"))}
    body = {"version": 1, "files": [{"key": "FILEA", "nodes": ["1:2"]}, {"key": "FILEB", "nodes": ["3:4"]}],
            "cases": [{"id": cid, "file_key": key, "node_id": node, "state": state, "route": "/greet",
                       "implementation_paths": ["greet.py"], "viewport": {"width": 2, "height": 1,
                       "device_scale_factor": 1}, "export_scale": 1, "artifacts": copy.deepcopy(artifacts)}
                      for cid, key, node, state in (("greet.empty", "FILEA", "1:2", "empty"),
                                                   ("greet.filled", "FILEB", "3:4", "filled"))]}
    path = root / "manifest.json"
    path.write_text(json.dumps(body))
    return path, body


class DesignManifestTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path, self.body = bundle(self.root / "exports")
        self.workspace = self.root / "project"
        self.workspace.mkdir()

    def test_exported_inventory_preserves_opencode_builder_pin_and_disabled_caps(self):
        import autocode_args
        import autocode_configure
        import autocode_milestones as milestones
        import autocode_opencode as opencode
        import autocode_planning as planning
        import autocode_support as support
        import autopilot
        parser = autocode_args.build_parser("autopilot", opencode.DEFAULT_MODELS)
        args = parser.parse_args(["fixture", "--engine", "opencode", "--provider", "opencode",
            "--figma-manifest", str(self.path), "--terra-model", "zai/glm-5.3", "--terra-reasoning-effort", "high",
            "--pin-model-role", "terra", "--unlimited-iterations", "--max-seconds", "0", "--max-stage-seconds", "0",
            "--max-idle-seconds", "0", "--max-tool-seconds", "0"])
        state = {"workspace": str(self.workspace)}
        with patch.object(opencode, "local_settings", return_value={"engine": "opencode"}), \
             patch.object(opencode, "check_models"), patch.object(opencode, "check_subscription_routes"), \
             patch.object(support, "local_settings", side_effect=AssertionError("No Codex login")):
            settings = autocode_configure.configure(args, state, planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertEqual("opencode", settings["engine"])
        self.assertEqual(("zai/glm-5.3", "high", True), tuple(settings["roles"]["terra"][key] for key in
                         ("model", "reasoning_effort", "model_pinned")))
        self.assertIsNone(settings["limits"]["iteration_ceiling"])
        for key in ("max_seconds", "stage_timeout_seconds", "idle_timeout_seconds", "tool_timeout_seconds"):
            self.assertEqual(0, settings["limits"][key])
        manifest.verify(settings["design_manifest"])
        state["settings"] = settings
        with self.assertRaisesRegex(ValueError, "new-run input"):
            autocode_configure.configure(args, state, planning=planning, milestones=milestones, autopilot=autopilot)

    def test_native_url_with_query_keeps_its_auth_restriction_and_matches_inventory(self):
        import autocode_args
        import autocode_configure
        import autocode_milestones as milestones
        import autocode_opencode as opencode
        import autocode_planning as planning
        import autocode_support as support
        import autopilot
        parser = autocode_args.build_parser("autopilot", opencode.DEFAULT_MODELS)
        args = parser.parse_args(["fixture", "--engine", "codex", "--figma-manifest", str(self.path),
                                  "--figma-file", "https://www.figma.com/design/FILEA?node-id=1-2"])
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}):
            settings = autocode_configure.configure(args, {"workspace": str(self.workspace)},
                planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertEqual("codex", settings["engine"])
        self.assertEqual(args.figma_file, settings["figma_file"])
        args.figma_file = "https://www.figma.com/design/UNDECLARED?node-id=1-2"
        with self.assertRaisesRegex(ValueError, "not declared"):
            autocode_configure.configure(args, {"workspace": str(self.workspace)},
                planning=planning, milestones=milestones, autopilot=autopilot)
        args.figma_file = "https://www.figma.com/design/FILEA?node-id=1-2"
        with patch.object(support, "local_settings", return_value={"auth_mode": "API"}), self.assertRaises(ValueError):
            autocode_configure.configure(args, {"workspace": str(self.workspace)},
                planning=planning, milestones=milestones, autopilot=autopilot)

    def test_two_files_and_declared_frames_cannot_be_silently_dropped(self):
        manifest.load(self.path)
        for change in ("file", "case", "undeclared", "duplicate_id", "duplicate_state"):
            body = copy.deepcopy(self.body)
            if change == "file": body["files"].pop()
            if change == "case": body["cases"].pop()
            if change == "undeclared": body["cases"][0]["node_id"] = "99:1"
            if change == "duplicate_id": body["cases"][1]["id"] = body["cases"][0]["id"]
            if change == "duplicate_state": body["cases"].append({**body["cases"][0], "id": "another"})
            with self.subTest(change=change), self.assertRaises(ValueError):
                manifest.validate(body)

    def test_bundle_survives_external_export_deletion_and_detects_retained_drift(self):
        retained = manifest.retain(manifest.load(self.path), self.workspace)
        shutil.rmtree(self.path.parent)
        manifest.verify(retained)
        self.assertTrue(Path(retained["root"]).is_relative_to(self.workspace))
        (Path(retained["root"]) / "context.txt").write_text("changed tokens")
        with self.assertRaisesRegex(util.Paused, "changed design reference"):
            manifest.context({"design_manifest": retained})
        with self.assertRaises(ValueError):
            manifest.retain(retained, self.workspace)

    def test_metadata_hash_missing_asset_and_path_escape_fail_preflight(self):
        for change in ("viewport", "scale", "hash", "missing", "escape", "absolute", "empty_state", "nan", "bool"):
            body = copy.deepcopy(self.body)
            case = body["cases"][0]
            if change == "viewport": case["viewport"]["width"] = 3
            if change == "scale": case["export_scale"] = 2
            if change == "hash": case["artifacts"]["screenshot"]["sha256"] = "0" * 64
            if change == "missing": case["artifacts"]["screenshot"]["path"] = "missing.png"
            if change == "escape": case["artifacts"]["screenshot"]["path"] = "../screen.png"
            if change == "absolute": case["implementation_paths"] = ["/tmp/code.py"]
            if change == "empty_state": case["state"] = "  "
            if change == "nan": case["export_scale"] = float("nan")
            if change == "bool": case["viewport"]["device_scale_factor"] = True
            self.path.write_text(json.dumps(body))
            with self.subTest(change=change), self.assertRaises(ValueError):
                manifest.load(self.path)

    def test_symlink_escape_and_changed_manifest_identity_are_refused(self):
        outside = self.root / "outside.png"
        png(outside)
        (self.path.parent / "screen.png").unlink()
        (self.path.parent / "screen.png").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "design reference"):
            manifest.load(self.path)
        (self.path.parent / "screen.png").unlink()
        png(self.path.parent / "screen.png")
        record = manifest.load(self.path)
        record["body"]["cases"][0]["route"] = "/changed"
        with self.assertRaisesRegex(ValueError, "identity changed"):
            manifest.verify(record)

    def passing_state(self):
        retained = manifest.retain(manifest.load(self.path), self.workspace)
        viewport = self.body["cases"][0]["viewport"]
        png(self.workspace / "candidate.png", round(viewport["width"] * viewport["device_scale_factor"]),
            round(viewport["height"] * viewport["device_scale_factor"]))
        (self.workspace / "comparison.txt").write_text("Offline fixture comparison; not genuine browser acceptance")
        validation = {"design_manifest_hash": retained["manifest_hash"], "design_results": [
            {"id": case["id"], "status": "PASS", "criterion_ids": ["C1"],
             "candidate_ref": "candidate.png", "comparison_ref": "comparison.txt"} for case in self.body["cases"]],
            "verdict": "PASS", "criteria_revision": "criteria", "source_revision": "source-a",
            "checks": [{"exit_code": 0}], "findings": [], "unverified_criteria": [],
            "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": ["comparison.txt"]}],
            "reviewer_role": "sol"}
        criteria = [{"id": "C1", "criterion": "Fixture behavior", "status": "verified", "evidence": "comparison"}]
        state = {"workspace": str(self.workspace), "version": 2, "settings": {"design_manifest": retained},
                 "criteria_revision": "criteria", "validation": validation, "acceptance_criteria": criteria}
        validation["evidence_hashes"] = {ref: util.file_hash(ref) for ref in coverage.report_refs(state, validation)}
        return state, {"status": "TASK_COMPLETE", "acceptance_criteria": criteria}, {"revision": "source-a"}

    def test_whole_completion_requires_every_design_case_on_current_source(self):
        state, decision, current = self.passing_state()
        self.assertTrue(completion.completion_ready(state, decision, current))
        for change in ("omitted", "unverified", "duplicate", "stale_manifest", "unknown_criterion", "failed_criterion", "no_pin", "stale_source"):
            changed = copy.deepcopy(state)
            validation = changed["validation"]
            row = validation["design_results"][-1]
            if change == "omitted": validation["design_results"].pop()
            if change == "unverified": row["status"] = "NOT_VERIFIED"
            if change == "duplicate": row["id"] = validation["design_results"][0]["id"]
            if change == "stale_manifest": validation["design_manifest_hash"] = "other"
            if change == "unknown_criterion": row["criterion_ids"] = ["UNKNOWN"]
            if change == "failed_criterion": validation["criterion_results"][0]["status"] = "FAIL"
            if change == "no_pin": validation["evidence_hashes"] = {}
            if change == "stale_source": validation["source_revision"] = "old-source"
            with self.subTest(change=change):
                self.assertFalse(completion.completion_ready(changed, decision, current))
        state["validation"]["design_results"][-1]["status"] = "NOT_VERIFIED"
        self.assertIn("greet.filled", completion.rejection(state))

    def test_scaled_reference_export_does_not_shrink_the_browser_css_viewport(self):
        for case in self.body["cases"]:
            case["viewport"] = {"width": 4, "height": 2, "device_scale_factor": 2}
            case["export_scale"] = 0.5
        self.path.write_text(json.dumps(self.body))
        state, decision, current = self.passing_state()
        self.assertTrue(completion.completion_ready(state, decision, current))
        self.assertEqual((8, 4), manifest.png_dimensions(self.workspace / "candidate.png"))
        # A candidate rendered at the reference's smaller export dimensions is wrong.
        png(self.workspace / "candidate.png", width=2, height=1)
        self.assertFalse(completion.completion_ready(state, decision, current))

    def test_reference_png_wrong_viewport_and_changed_capture_cannot_count_as_proof(self):
        state, decision, current = self.passing_state()
        row = state["validation"]["design_results"][0]
        original = row["candidate_ref"]
        row["candidate_ref"] = str(Path(state["settings"]["design_manifest"]["root"]) / "screen.png")
        self.assertFalse(completion.completion_ready(state, decision, current))
        row["candidate_ref"] = original
        png(self.workspace / "candidate.png", width=1)
        self.assertFalse(completion.completion_ready(state, decision, current))
        viewport = self.body["cases"][0]["viewport"]
        png(self.workspace / "candidate.png", round(viewport["width"] * viewport["device_scale_factor"]),
            round(viewport["height"] * viewport["device_scale_factor"]))
        (self.workspace / "comparison.txt").write_text("changed after validation")
        self.assertFalse(completion.completion_ready(state, decision, current))

    def test_report_generation_and_decoding_enforce_complete_inventory_without_mutating_schema(self):
        state, _, _ = self.passing_state()
        schema = util.read(ROOT / "tools/autocode-schemas/v2/sol-report.schema.json")
        before = copy.deepcopy(schema)
        bound = reports.review_generation_schema(schema, state, "sol")
        self.assertIn("design_results", bound["required"])
        # ID enum binding must not leak through the shared string-schema object.
        self.assertNotIn("enum", bound["properties"]["design_results"]["items"]["properties"]["criterion_ids"]["items"])
        self.assertEqual(schema, before)
        broken = copy.deepcopy(state["validation"])
        broken["design_results"].pop()
        with self.assertRaisesRegex(ValueError, "every design case"):
            reports.review_validation_schema(bound, state, {"stage": "sol_report_repair", "original_stage": "sol"}, broken)
        checkpoint = {"type": "object", "required": ["validation"], "properties": {"validation": schema}}
        self.assertIn("design_results", coverage.extend_schema(checkpoint, state, "astra_checkpoint")["properties"]["validation"]["required"])

    def test_builder_self_check_without_design_fields_is_not_a_mismatched_manifest(self):
        # apply_review_result used to call report_refs for every non-Builder stage,
        # including the final-audit builder self_check. That report is self-evidence
        # for criteria; it does not carry design_results. Independent sol reports
        # still have to account for every case (covered above).
        import autopilot
        state, decision, current = self.passing_state()
        self_check = {
            "verdict": "NOT_VERIFIED", "checks": [], "findings": [], "unverified_criteria": ["C1"],
            "criterion_results": [{"id": "C1", "status": "NOT_VERIFIED", "evidence_refs": []}],
            "acceptance_criteria": copy.deepcopy(state["acceptance_criteria"]),
        }
        # report_refs stays strict for independent design reports.
        with self.assertRaisesRegex(ValueError, "different design manifest"):
            coverage.report_refs(state, self_check, stage="sol")
        self.assertEqual([], coverage.report_refs(state, self_check, stage="self_check"))

        class Runtime:
            def check_evidence_options(self, record):
                return {}
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test",
                        "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        record = {"events": str(self.workspace / "events.jsonl"), "source_revision": "source-a",
                  "role": "terra", "stage": "self_check", "output": str(self.workspace / "self.json")}
        (self.workspace / "events.jsonl").write_text("")
        autopilot.apply_review_result(Runtime(), state, "self_check", self_check,
                                      record, self.workspace, self.workspace)
        self.assertEqual("NOT_VERIFIED", state["validation"]["verdict"])

        # Design gaps refuse independent completion, but the final-audit self-check
        # probe (require_independent=False) must not demand design_results.
        probe, decision, current = self.passing_state()
        probe["validation"]["design_results"][-1]["status"] = "NOT_VERIFIED"
        self.assertFalse(coverage.ready(probe))
        self.assertFalse(completion.completion_ready(probe, decision, current))
        self.assertTrue(completion.completion_ready(probe, decision, current, require_independent=False))

    def test_status_exposes_inventory_without_claiming_current_visual_acceptance(self):
        state, _, _ = self.passing_state()
        state["validation"]["design_results"][-1]["status"] = "NOT_VERIFIED"
        design = run_view.view(state)["design"]
        self.assertEqual(["greet.filled"], design["not_passing"])
        self.assertIsNone(design["current_visual_acceptance"])
        self.assertNotIn("design", run_view.view({"status": "RUNNING"}))


class DesignManifestCliTests(unittest.TestCase):
    """A hand-scripted offline provider tests runtime plumbing, not visual/model quality."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="design-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path, self.body = bundle(self.root / "exports")
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.test",
                        "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        bindir = self.root / "bin"
        bindir.mkdir()
        provider = (ROOT / "tools/live_fixture_provider.py").read_text()
        # Extend a copied fixture only; neither the production fake nor a live provider is changed.
        hook = r"""
    design = data.get('design_manifest')
    if design and stage == 'sol':
        report['design_manifest_hash'] = design['manifest_hash']
        rows = []
        for index, case in enumerate(design['body']['cases']):
            capture = output.parent / ('capture-' + case['id'] + '.png')
            capture.write_bytes((Path(design['root']) / case['artifacts']['screenshot']['path']).read_bytes())
            comparison = output.parent / ('compare-' + case['id'] + '.txt')
            comparison.write_text('Offline fixture metadata comparison; not real image acceptance')
            rows.append(dict(id=case['id'], status='PASS', criterion_ids=['C1'],
                             candidate_ref=str(capture), comparison_ref=str(comparison)))
        if os.environ.get('FAKE_DESIGN_UNVERIFIED'):
            rows[-1]['status'] = 'NOT_VERIFIED'
        report['design_results'] = rows
    if design:
        with open(os.environ['FAKE_DESIGN_PROMPTS'], 'a') as log:
            log.write(json.dumps(dict(stage=stage, ids=[c['id'] for c in design['body']['cases']])) + '\n')
"""
        provider = provider.replace('    output.write_text(json.dumps(report))', hook + '    output.write_text(json.dumps(report))')
        # os is present in most fixtures; make it explicit in this copy.
        provider = provider.replace('import json', 'import os\nimport json', 1)
        (bindir / "codex").write_text(provider)
        (bindir / "codex").chmod(0o755)
        self.env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "AUTOCODE_HOME": str(self.root / "registry"),
                    "PYTHONDONTWRITEBYTECODE": "1", "FAKE_DESIGN_PROMPTS": str(self.root / "prompts.jsonl")}

    def start(self):
        return taskrun.TaskRun.start(self.workspace, BRIEF, options=OPTIONS,
            start_options=("--figma-manifest", str(self.path)), env=self.env, timeout=120)

    def test_public_taskrun_retains_exports_and_completes_only_with_all_case_evidence(self):
        run = self.start()
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertEqual(["greet.empty", "greet.filled"], view["design"]["case_ids"])
        shutil.rmtree(self.path.parent)
        run.approve_plan(view["needs"]["token"])
        view = run.advance_until_input()
        self.assertTrue(view["done"], view)
        self.assertEqual([], view["design"]["not_passing"])
        prompts = [json.loads(line) for line in (self.root / "prompts.jsonl").read_text().splitlines()]
        self.assertTrue({"requirements_gather", "astra_discovery", "terra", "sol"} <= {row["stage"] for row in prompts})
        replacement, _ = bundle(self.root / "replacement")
        with self.assertRaisesRegex(taskrun.TaskRunError, "new-run input"):
            run._invoke("replace references", "--figma-manifest", str(replacement))

    def test_partial_visual_pass_reaches_completion_gate_and_stops_with_named_missing_case(self):
        self.env["FAKE_DESIGN_UNVERIFIED"] = "1"
        run = self.start()
        run.approve_plan(run.status()["needs"]["token"])
        view = run.advance_until_input()
        self.assertFalse(view["done"], view)
        self.assertEqual(["greet.filled"], view["design"]["not_passing"])
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
        self.assertIn("Completion rejected", view["stop_reason"])
        self.assertIn("greet.filled", view["stop_reason"])

    def test_invalid_manifest_is_refused_before_any_provider_call_or_run_allocation(self):
        self.body["cases"].pop()
        self.path.write_text(json.dumps(self.body))
        with self.assertRaises(taskrun.TaskRunError):
            self.start()
        self.assertFalse((self.root / "prompts.jsonl").exists())
        self.assertFalse((self.workspace / ".autocode").exists())


if __name__ == "__main__":
    unittest.main()
