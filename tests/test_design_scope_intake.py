"""Synthetic full-source scoped intake; no native qualification or model spend."""

import copy
import hashlib  # noqa: F401 - preserve existing import behavior
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch  # noqa: F401 - preserve existing import behavior

import autocode_design_inventory as inventory
import autocode_design_manifest as manifest
import autocode_util as util

from tests.test_design_manifest import BRIEF, OPTIONS, bundle, inventory_bundle

ROOT = Path(__file__).resolve().parents[1]


class ScopedIntakeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="scope-intake-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path, self.body = inventory_bundle(self.root / "exports")
        # Details is a genuinely dependency-free frame in this declared fixture.
        # Full file/page/node/resource receipts remain untouched and retained.
        self.body["cases"] = [self.body["cases"][1]]
        self.body["cases"][0]["inventory_refs"] = {
            key: [] for key in ("components", "variables", "fonts", "assets", "transitions")
        }
        self.path.write_text(json.dumps(self.body))
        self.authority = self.root / "reviewed-scope.json"
        self.write_authority()
        self.workspace = self.root / "project"
        self.workspace.mkdir()

    def write_authority(self, **changes):
        artifact = dict(
            version=1,
            manifest_hash=util.digest(self.body),
            approved_case_ids=[case["id"] for case in self.body["cases"]],
            approval_evidence=[dict(kind="approved_scope_decision", sha256="a" * 64)],
        )
        artifact.update(changes)
        self.authority.write_text(json.dumps(artifact, separators=(",", ":")) + "\n")
        self.pin = util.file_hash(self.authority)

    def arguments(self):
        return dict(approved_scope=self.authority, approved_scope_sha256=self.pin)

    def retained(self):
        raw = manifest.load(self.path, **self.arguments())
        self.assertNotIn("scope_admission", raw, "Transient operator input never becomes a provider record field")
        return manifest.retain(raw, self.workspace, **self.arguments())

    def test_legacy_and_package_issued_authority_cannot_admit_partial_scope(self):
        with self.assertRaises(ValueError):
            manifest.load(self.path)
        (self.path.parent / "approved-scope.json").write_bytes(self.authority.read_bytes())
        with self.assertRaises(ValueError):
            manifest.load(self.path)
        tampered = copy.deepcopy(self.body)
        tampered["scope_admission"] = {"path": self.authority.name, "sha256": self.pin}
        self.path.write_text(json.dumps(tampered))
        with self.assertRaises(ValueError):
            manifest.load(self.path, **self.arguments())

    def test_both_or_none_pin_and_v2_only(self):
        for args in (
            {"approved_scope": self.authority},
            {"approved_scope_sha256": self.pin},
            {"approved_scope": self.authority, "approved_scope_sha256": "b" * 64},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                manifest.load(self.path, **args)
        v1, body = bundle(self.root / "v1")
        with self.assertRaises(ValueError):
            manifest.load(v1, **self.arguments())

    def test_explicit_intake_retains_exact_authority_and_reloads_fresh_process(self):
        record = self.retained()
        self.assertEqual(
            {"path": "approval/approved-scope-" + self.pin + ".json", "sha256": self.pin}, record["scope_admission"]
        )
        self.assertEqual(
            self.authority.read_bytes(), (Path(record["root"]) / record["scope_admission"]["path"]).read_bytes()
        )
        shutil.rmtree(self.path.parent)
        self.authority.unlink()
        packet = self.root / "trusted-record.json"
        packet.write_text(json.dumps(record))
        code = 'import json,sys;import autocode_design_manifest as m;r=m.verify(json.load(open(sys.argv[1])));c=m.context({"design_manifest":r});print(json.dumps({"pin":r["scope_admission"],"catalog":c["catalog"]}))'
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(packet)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "tools"), "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        parsed = json.loads(result.stdout)
        self.assertEqual(record["scope_admission"], parsed["pin"])
        catalog = parsed["catalog"]
        self.assertEqual(3, catalog["full_source_frame_count"])
        self.assertEqual(1, catalog["app_case_count"])
        self.assertEqual(2, len(catalog["scope_partition"]["retained_frames"]))
        self.assertEqual(3, len(catalog["screen_states"]), "Historical original state facts stay retained")
        self.assertEqual(2, len(catalog["scope_partition"]["retained_resources"]["components"]))
        self.assertEqual([], catalog["scope_partition"]["used_resources"]["components"])
        self.assertEqual(self.body, record["body"])

    def test_retained_artifact_byte_pin_symlink_path_and_empty_drift_refuse(self):
        record = self.retained()
        path = Path(record["root"]) / record["scope_admission"]["path"]
        original = path.read_bytes()
        for data in (b"", original + b" "):
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                manifest.verify(record)
        path.write_bytes(original)
        outside = self.root / "outside.json"
        outside.write_bytes(original)
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(ValueError):
            manifest.verify(record)
        path.unlink()
        path.write_bytes(original)
        for field, value in [("path", "../outside.json"), ("sha256", "b" * 64)]:
            changed = copy.deepcopy(record)
            changed["scope_admission"][field] = value
            with self.assertRaises(ValueError):
                manifest.verify(changed)
        changed = copy.deepcopy(record)
        changed["operator_scope_path"] = str(outside)
        with self.assertRaises(ValueError):
            manifest.verify(changed)

    def test_changed_authority_same_body_is_not_silently_retained(self):
        original = self.retained()
        previous = (Path(original["root"]) / original["scope_admission"]["path"]).read_bytes()
        self.write_authority(approval_evidence=[dict(kind="reviewed_native_case_binding", sha256="c" * 64)])
        raw = manifest.load(self.path, **self.arguments())
        with self.assertRaises(ValueError):
            manifest.retain(raw, self.workspace, **self.arguments())
        self.assertEqual(previous, (Path(original["root"]) / original["scope_admission"]["path"]).read_bytes())
        manifest.verify(original)
        with self.assertRaises(ValueError):
            manifest.verify(original, **self.arguments())

    def test_external_input_and_retained_parent_symlinks_refuse(self):
        link = self.root / "link.json"
        link.symlink_to(self.authority)
        with self.assertRaises(ValueError):
            manifest.load(self.path, approved_scope=link, approved_scope_sha256=self.pin)
        target = self.root / "other"
        target.mkdir()
        (self.workspace / ".autocode").symlink_to(target, target_is_directory=True)
        raw = manifest.load(self.path, **self.arguments())
        with self.assertRaises(ValueError):
            manifest.retain(raw, self.workspace, **self.arguments())

    def test_every_original_page_component_variant_asset_source_predicate_remains(self):
        original = copy.deepcopy(self.body)
        mutations = [
            lambda b: b["files"][0]["components"][0]["variants"].pop(),
            lambda b: b["files"][0]["pages"].clear(),
            lambda b: b["files"][0]["assets"][0].update(node_id="99:99"),
            lambda b: b["files"][0]["transitions"][0]["action"].update(destinationId="99:99"),
        ]
        for mutation in mutations:
            self.body = copy.deepcopy(original)
            mutation(self.body)
            self.path.write_text(json.dumps(self.body))
            self.write_authority()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                manifest.load(self.path, **self.arguments())
        self.body = original
        self.path.write_text(json.dumps(original))
        self.write_authority()
        page = self.body["files"][0]["pages"][0]
        source = self.path.parent / page["source_json"]["path"]
        data = json.loads(source.read_text())
        data["nodes"].pop()
        source.write_text(json.dumps(data))
        page["source_json"]["sha256"] = util.file_hash(source)
        self.path.write_text(json.dumps(self.body))
        self.write_authority()
        with self.assertRaises(ValueError):
            manifest.load(self.path, **self.arguments())

    def test_false_usage_required_historical_target_and_bound_state_rejected(self):
        original = copy.deepcopy(self.body)
        self.body["cases"][0]["inventory_refs"]["fonts"] = ["font-FILEA"]
        self.path.write_text(json.dumps(self.body))
        self.write_authority()
        with self.assertRaisesRegex(ValueError, "false use"):
            manifest.load(self.path, **self.arguments())
        self.body = copy.deepcopy(original)
        self.body["cases"][0]["state"] = "invented"
        self.path.write_text(json.dumps(self.body))
        self.write_authority()
        with self.assertRaises(ValueError):
            manifest.load(self.path, **self.arguments())
        self.body = copy.deepcopy(original)
        case = self.body["cases"][0]
        case["node_id"] = "1:2"
        case["inventory_refs"]["transitions"] = ["home-details"]
        self.path.write_text(json.dumps(self.body))
        self.write_authority()
        with self.assertRaisesRegex(ValueError, "outside approved"):
            manifest.load(self.path, **self.arguments())

    def test_node_navigation_needs_its_destination_but_back_and_url_do_not(self):
        original = copy.deepcopy(self.body)
        source = self.path.parent / original["files"][0]["pages"][0]["source_json"]["path"]
        original_source = json.loads(source.read_text())
        full_path, full_body = inventory_bundle(self.root / "unscoped")
        full_source = full_path.parent / full_body["files"][0]["pages"][0]["source_json"]["path"]
        full_receipt = json.loads(full_source.read_text())

        def load_action(action, *, used=True, scoped=True):
            self.body = copy.deepcopy(original if scoped else full_body)
            receipt = copy.deepcopy(original_source if scoped else full_receipt)
            selected_source = source if scoped else full_source
            selected_path = self.path if scoped else full_path
            transition = self.body["files"][0]["transitions"][0]
            transition.update(
                source_node_id="1:6" if scoped and used else "1:2",
                action=action,
                target_node_id=action.get("destinationId") or "",
            )
            if scoped:
                self.body["cases"][0]["inventory_refs"]["transitions"] = [transition["id"]] if used else []
            for row in receipt["nodes"]:
                row["transitions"] = [value for value in row["transitions"] if value["id"] != transition["id"]]
            node = next(row for row in receipt["nodes"] if row["id"] == transition["source_node_id"])
            node["transitions"] = [{key: transition[key] for key in ("id", "target_node_id", "trigger", "action")}]
            selected_source.write_text(json.dumps(receipt))
            self.body["files"][0]["pages"][0]["source_json"]["sha256"] = util.file_hash(selected_source)
            selected_path.write_text(json.dumps(self.body))
            if scoped:
                self.write_authority()
            return manifest.load(selected_path, **self.arguments() if scoped else {})

        def conditional(action):
            for _ in range(2):
                action = {"type": "CONDITIONAL", "conditionalBlocks": [{"actions": [action]}]}
            return action

        for navigation in ("NAVIGATE", "CHANGE_TO"):
            for destination in (None, "", "omitted"):
                action = {"type": "NODE", "navigation": navigation}
                if destination != "omitted":
                    action["destinationId"] = destination
                for source_action in (action, conditional(action)):
                    for scoped in (False, True):
                        with self.subTest(action=source_action, scoped=scoped):
                            with self.assertRaisesRegex(ValueError, "NODE action.*destination"):
                                load_action(source_action, scoped=scoped)
        for action in (
            {"type": "NODE", "navigation": "NAVIGATE", "destinationId": "1:6"},
            conditional({"type": "NODE", "navigation": "NAVIGATE", "destinationId": "1:6"}),
            {"type": "BACK"},
            {"type": "URL", "url": "https://example.test/"},
            {"type": "UPDATE_MEDIA_RUNTIME", "destinationId": None, "mediaAction": "PLAY"},
        ):
            for scoped in (False, True):
                with self.subTest(action=action, scoped=scoped):
                    loaded = load_action(action, scoped=scoped)
                    self.assertEqual(action, loaded["body"]["files"][0]["transitions"][0]["action"])

        unresolved = {"type": "NODE", "navigation": "NAVIGATE", "destinationId": None}
        for index, action in enumerate((unresolved, conditional(unresolved))):
            with self.subTest(excluded_action=action):
                loaded = load_action(action, used=False)
                target = self.workspace / ("excluded-" + str(index))
                target.mkdir()
                record = manifest.retain(loaded, target, **self.arguments())
                catalog = manifest.context({"design_manifest": record})["catalog"]
                retained = catalog["scope_partition"]["retained_resources"]["transitions"]
                self.assertEqual(["home-details"], [row["id"] for row in retained])
                self.assertEqual("outside approved product scope", retained[0]["scope_reason"])
                self.assertEqual(action, retained[0]["action"])

    def test_full_missing_resources_remain_blockers_but_unused_delivery_is_separate(self):
        file = self.body["files"][1]
        font = file["fonts"][0]
        font.pop("artifact")
        font.update(status="missing", reason="Synthetic historical unavailable font")
        self.path.write_text(json.dumps(self.body))
        self.write_authority()
        record = self.retained()
        catalog = manifest.context({"design_manifest": record})["catalog"]
        self.assertTrue(catalog["blockers"])
        self.assertTrue(manifest.blockers(record))
        self.assertEqual([], catalog["delivery_blockers"])
        self.assertEqual("missing", catalog["scope_partition"]["retained_resources"]["fonts"][1]["status"])
        self.assertEqual(
            1,
            len(
                manifest.context(
                    {"design_manifest": record}, stage="terra", current_task={"affected_paths": ["src/filea.js"]}
                )["catalog"]["cases"]
            ),
        )

    def test_verified_delivery_blockers_gate_used_missing_only_and_keep_visual_proof_mandatory(self):
        import autocode_completion as completion
        import autocode_design_coverage as coverage

        file = self.body["files"][0]
        font = file["fonts"][0]
        font.pop("artifact")
        font.update(status="missing", reason="Synthetic actual missing font")
        self.path.write_text(json.dumps(self.body))
        self.write_authority()
        unused = self.retained()
        self.assertTrue(manifest.blockers(unused))
        self.assertEqual([], manifest.delivery_blockers(unused))
        state = {"settings": {"design_manifest": unused}, "validation": {}}
        self.assertFalse(coverage.ready(state), "No actual independent visual proof still refuses readiness")
        self.assertIn("Design coverage needs fresh independent evidence", completion.rejection(state))
        self.assertNotIn("unavailable required fonts", completion.rejection(state))
        # Bind the same unavailable declaration to the actual approved frame,
        # then refresh the explicit synthetic source/hash/authority coherently.
        page = file["pages"][0]
        source = self.path.parent / page["source_json"]["path"]
        data = json.loads(source.read_text())
        next(row for row in data["nodes"] if row["id"] == "1:6")["fonts"] = [
            {"family": font["family"], "style": font["style"]}
        ]
        source.write_text(json.dumps(data))
        page["source_json"]["sha256"] = util.file_hash(source)
        self.body["cases"][0]["inventory_refs"]["fonts"] = [font["id"]]
        self.path.write_text(json.dumps(self.body))
        self.write_authority()
        used = manifest.retain(
            manifest.load(self.path, **self.arguments()), self.root / "used-workspace", **self.arguments()
        )
        self.assertEqual(manifest.blockers(used), manifest.delivery_blockers(used))
        self.assertFalse(coverage.ready({"settings": {"design_manifest": used}, "validation": {}}))
        self.assertIn(
            "unavailable required fonts",
            completion.rejection({"settings": {"design_manifest": used}, "validation": {}}),
        )
        projected = coverage.projection({"settings": {"design_manifest": unused}, "validation": {}})
        self.assertTrue(projected["inventory_blockers"])
        self.assertEqual([], projected["delivery_blockers"])
        owned = Path(unused["root"]) / unused["scope_admission"]["path"]
        owned.write_bytes(owned.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            manifest.delivery_blockers(unused)
        self.assertFalse(coverage.ready(state), "Changed retained authority cannot become completion evidence")
        self.assertIn("Design references are missing or changed", completion.rejection(state))

    def test_admission_is_not_reusable_for_changed_complete_body(self):
        record = self.retained()
        admission = manifest.admitted_scope(record)
        changed = copy.deepcopy(self.body)
        changed["files"][0]["revision"] = "changed"
        with self.assertRaises(ValueError):
            manifest.validate(changed, root=record["root"], admission=admission)
        with self.assertRaises(ValueError):
            inventory.catalog(changed, record["root"], admission=admission)

    def test_public_copy_uses_existing_authority_not_a_replacement(self):
        record = self.retained()
        copied = self.root / "public"
        shutil.copytree(self.path.parent, copied)
        loaded = manifest.reload_public(record, copied / self.path.name)
        self.assertEqual(self.body, loaded["body"])
        self.assertNotIn("scope_admission", loaded)
        changed = copy.deepcopy(self.body)
        changed["cases"][0]["route"] = "/changed"
        (copied / self.path.name).write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            manifest.reload_public(record, copied / self.path.name)

    def test_legacy_complete_bundle_still_accepts_and_has_no_scoped_ledger(self):
        path, body = inventory_bundle(self.root / "legacy")
        record = manifest.retain(manifest.load(path), self.root / "legacy-workspace")
        self.assertNotIn("scope_admission", record)
        self.assertNotIn("scope_partition", manifest.context({"design_manifest": record})["catalog"])
        self.assertEqual(body, manifest.verify(record)["body"])

    def legacy_approval_context_roundtrip(self, factory, name):
        path, body = factory(self.root / name)
        context = path.parent / "context.txt"
        original = context.read_bytes()
        (path.parent / "approval").mkdir()
        context.rename(path.parent / "approval" / "context.txt")
        for case in body["cases"]:
            case["artifacts"]["design_context"]["path"] = "approval/context.txt"
        path.write_text(json.dumps(body))
        retained = manifest.retain(manifest.load(path), self.root / (name + "-workspace"))
        shutil.rmtree(path.parent)
        packet = self.root / (name + "-record.json")
        packet.write_text(json.dumps(retained))
        code = "import json,sys;import autocode_design_manifest as m;r=m.verify(json.load(open(sys.argv[1])));print(json.dumps(r))"
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(packet)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "tools"), "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        reloaded = json.loads(result.stdout)
        self.assertEqual(body, reloaded["body"])
        self.assertNotIn("scope_admission", reloaded, "A legacy artifact directory cannot issue admission")
        self.assertEqual(original, (Path(retained["root"]) / "approval/context.txt").read_bytes())

    def test_legacy_v1_approval_context_load_retain_and_fresh_reload(self):
        self.legacy_approval_context_roundtrip(bundle, "legacy-v1-approval")

    def test_legacy_v2_approval_context_load_retain_and_fresh_reload(self):
        self.legacy_approval_context_roundtrip(inventory_bundle, "legacy-v2-approval")

    def test_authenticated_scope_refuses_approval_namespace_artifact_collision(self):
        (self.path.parent / "approval").mkdir()
        (self.path.parent / "context.txt").rename(self.path.parent / "approval/context.txt")
        self.body["cases"][0]["artifacts"]["design_context"]["path"] = "approval/context.txt"
        self.path.write_text(json.dumps(self.body))
        self.write_authority()
        raw = manifest.load(self.path, **self.arguments())
        with self.assertRaisesRegex(ValueError, "reserved"):
            manifest.retain(raw, self.workspace, **self.arguments())
        self.assertFalse((self.workspace / ".autocode/design-inputs").exists())


class ScopedPublicCliTests(unittest.TestCase):
    setUp = ScopedIntakeTests.setUp
    write_authority = ScopedIntakeTests.write_authority

    def test_public_dry_run_validates_explicit_scope_without_saving_it(self):
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            check=True,
        )
        command = [
            sys.executable,
            "-B",
            str(ROOT / "tools/autocode.py"),
            "Inspect scoped design",
            "--workspace",
            str(self.workspace),
            "--in-place",
            "--dry-run",
            "--figma-manifest",
            str(self.path),
            "--figma-review",
            "automatic",
            "--figma-approved-scope",
            str(self.authority),
            "--figma-approved-scope-sha256",
            self.pin,
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            env={**os.environ, "AUTOCODE_HOME": str(self.root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        preview = value["view"]["design_input_preview"]
        self.assertEqual([case["id"] for case in self.body["cases"]], preview["case_ids"])
        self.assertEqual(self.pin, preview["approved_scope_sha256"])
        self.assertEqual(3, preview["catalog"]["full_source_frame_count"])
        self.assertFalse(preview["retained"])
        self.assertIsNone(preview["visual_acceptance"])
        self.assertNotIn("design_manifest", value.get("settings") or {})
        self.assertFalse((self.workspace / ".autocode/design-inputs").exists())
        self.assertFalse((self.workspace / ".autocode/runs").exists())

    def start_scoped(self):
        import autocode_taskrun as taskrun

        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            check=True,
        )
        bindir = self.root / "bin"
        bindir.mkdir()
        from tests.figma_inventory_fixtures import install_inventory_hook

        provider = bindir / "codex"
        provider.write_text((ROOT / "tools/live_fixture_provider.py").read_text())
        install_inventory_hook(provider)
        for case in self.body["cases"]:
            case["implementation_paths"] = ["greet.py"]
        self.path.write_text(json.dumps(self.body))
        self.write_authority()
        env = {
            "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "FAKE_DESIGN_PROMPTS": str(self.root / "prompts.jsonl"),
            "FAKE_CAPTURE_REPO": str(ROOT),
        }
        command = (sys.executable, "-B", str(ROOT / "tools/autocode.py"))
        run = taskrun.TaskRun.start(
            self.workspace,
            BRIEF,
            command=command,
            options=OPTIONS,
            start_options=(
                "--figma-manifest",
                str(self.path),
                "--figma-approved-scope",
                str(self.authority),
                "--figma-approved-scope-sha256",
                self.pin,
            ),
            env=env,
            timeout=120,
            cwd=ROOT,
        )
        return run

    def test_public_cli_routes_authority_to_owned_retention_and_status_reload(self):
        run = self.start_scoped()
        view = run.status()
        self.assertEqual([self.body["cases"][0]["id"]], view["design"]["case_ids"])
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        saved = json.loads(run._invoke("status", "--status").stdout)["settings"]["design_manifest"]
        self.assertEqual(view["design"]["manifest_hash"], saved["manifest_hash"])
        retained = Path(saved["root"])
        self.assertTrue(retained.resolve().is_relative_to(self.workspace / ".autocode"))
        self.assertEqual(self.pin, saved["scope_admission"]["sha256"])
        self.assertEqual(self.authority.read_bytes(), (retained / saved["scope_admission"]["path"]).read_bytes())
        shutil.rmtree(self.path.parent)
        self.authority.unlink()
        current = run.status()
        self.assertEqual(view["design"]["case_ids"], current["design"]["case_ids"])
        self.assertEqual(view["design"]["inventory"], current["design"]["inventory"])
        self.assertNotIn("inventory_error", current["design"])
        self.assertIsNone(
            current["design"]["current_visual_acceptance"], "Synthetic CLI retention is never native acceptance"
        )

    def test_public_scoped_reference_revision_preserves_authority_history_and_review(self):
        run = self.start_scoped()
        old = run.status()
        original = json.loads(run._invoke("status", "--status").stdout)["settings"]
        original_record = original["design_manifest"]
        old_authority = (Path(original_record["root"]) / original_record["scope_admission"]["path"]).read_bytes()
        replacement = self.root / "changed-exports"
        shutil.copytree(self.path.parent, replacement)
        changed = copy.deepcopy(self.body)
        (replacement / "changed-context.txt").write_text("Updated approved Details context")
        changed["cases"][0]["artifacts"]["design_context"] = {
            "path": "changed-context.txt",
            "sha256": util.file_hash(replacement / "changed-context.txt"),
        }
        candidate = replacement / "manifest-v2.json"
        candidate.write_text(json.dumps(changed))
        authority = self.root / "changed-reviewed-scope.json"
        artifact = dict(
            version=1,
            manifest_hash=util.digest(changed),
            approved_case_ids=[changed["cases"][0]["id"]],
            approval_evidence=[dict(kind="approved_scope_decision", sha256="d" * 64)],
        )
        authority.write_text(json.dumps(artifact, separators=(",", ":")) + "\n")
        pin = util.file_hash(authority)

        def revise(*scope_flags, expected=old["design"]["manifest_hash"], path=candidate):
            command = [
                *run.command,
                "--workspace",
                str(run.workspace),
                "--run-dir",
                str(run.run_dir),
                "--revise-figma-manifest",
                str(path),
                "--expected-design-hash",
                expected,
                "--design-change-reason",
                "Reviewed Details reference correction",
                *scope_flags,
            ]
            return subprocess.run(
                command, cwd=ROOT, env={**os.environ, **run.env}, capture_output=True, text=True, timeout=30
            )

        for flags, expected in [
            ((), old["design"]["manifest_hash"]),
            (("--figma-approved-scope", str(authority)), old["design"]["manifest_hash"]),
            (("--figma-approved-scope-sha256", pin), old["design"]["manifest_hash"]),
            (
                ("--figma-approved-scope", str(authority), "--figma-approved-scope-sha256", "b" * 64),
                old["design"]["manifest_hash"],
            ),
            (
                ("--figma-approved-scope", str(self.authority), "--figma-approved-scope-sha256", self.pin),
                old["design"]["manifest_hash"],
            ),
            (("--figma-approved-scope", str(authority), "--figma-approved-scope-sha256", pin), "wrong"),
        ]:
            result = revise(*flags, expected=expected)
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertEqual(original, json.loads(run._invoke("status", "--status").stdout)["settings"])
        resumed = subprocess.run(
            [
                *run.command,
                "--workspace",
                str(run.workspace),
                "--run-dir",
                str(run.run_dir),
                "--resume-paused",
                "--figma-approved-scope",
                str(authority),
                "--figma-approved-scope-sha256",
                pin,
            ],
            cwd=ROOT,
            env={**os.environ, **run.env},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(2, resumed.returncode, "Ordinary resume cannot replace a scope pin")
        self.assertEqual(original, json.loads(run._invoke("status", "--status").stdout)["settings"])
        full_path, full_body = inventory_bundle(self.root / "full-replacement")
        refused = revise(path=full_path)
        self.assertEqual(2, refused.returncode, refused.stdout + refused.stderr)
        self.assertIn(
            "renewed external reviewed scope",
            refused.stderr,
            "Full legacy coverage cannot silently drop saved admission",
        )
        self.assertEqual(original, json.loads(run._invoke("status", "--status").stdout)["settings"])
        # A valid external approval for an unchanged body cannot swap authority.
        same = self.root / "same-body-new-approval.json"
        same_artifact = {**artifact, "manifest_hash": original_record["manifest_hash"]}
        same.write_text(json.dumps(same_artifact))
        same_pin = util.file_hash(same)
        refused = revise("--figma-approved-scope", str(same), "--figma-approved-scope-sha256", same_pin, path=self.path)
        self.assertEqual(2, refused.returncode, refused.stdout + refused.stderr)
        self.assertIn("unchanged", refused.stderr, "Same manifest bytes cannot swap an authenticated scope")
        self.assertEqual(original, json.loads(run._invoke("status", "--status").stdout)["settings"])

        result = revise("--figma-approved-scope", str(authority), "--figma-approved-scope-sha256", pin)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        proposed = run.status()
        current = json.loads(run._invoke("status", "--status").stdout)["settings"]
        self.assertEqual("PAUSED_DESIGN_INPUT_CHANGED", proposed["status"])
        self.assertFalse(proposed["done"])
        self.assertEqual(original["roles"], current["roles"])
        self.assertEqual(original["limits"], current["limits"])
        self.assertEqual(original_record, current["design_manifest_history"][-1])
        self.assertEqual(
            old_authority, (Path(original_record["root"]) / original_record["scope_admission"]["path"]).read_bytes()
        )
        new_record = current["design_manifest"]
        self.assertEqual(util.digest(changed), new_record["manifest_hash"])
        self.assertEqual(
            {"path": "approval/approved-scope-" + pin + ".json", "sha256": pin}, new_record["scope_admission"]
        )
        self.assertEqual(
            authority.read_bytes(), (Path(new_record["root"]) / new_record["scope_admission"]["path"]).read_bytes()
        )
        self.assertEqual(
            [changed["cases"][0]["id"]], proposed["design"]["reference_changes"][-1]["coverage_change"]["changed"]
        )
        self.assertIsNone(proposed["design"]["current_visual_acceptance"])
        self.assertFalse((self.workspace / "greet.py").exists())
        import autocode_taskrun as taskrun

        with self.assertRaisesRegex(taskrun.TaskRunError, "unchanged"):
            run.revise_design(
                candidate,
                new_record["manifest_hash"],
                "Unchanged references cannot swap scope",
                approved_scope=authority,
                approved_scope_sha256=pin,
            )
        shutil.rmtree(replacement)
        authority.unlink()
        self.assertEqual(new_record["manifest_hash"], run.status()["design"]["manifest_hash"])
        reviewed = run.resume_paused()
        self.assertEqual("approve_plan", reviewed["needs"]["kind"], reviewed)
        self.assertNotEqual(old["needs"]["token"], reviewed["needs"]["token"])
        self.assertFalse((self.workspace / "greet.py").exists(), "Reference correction never authorizes implementation")


if __name__ == "__main__":
    unittest.main()
