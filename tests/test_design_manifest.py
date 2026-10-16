"""Offline design inventory and public CLI coverage; no models or Figma writes."""

import copy
import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import autocode_completion as completion
import autocode_design_coverage as coverage
import autocode_design_identity as design_identity
import autocode_design_inventory as design_inventory
import autocode_design_manifest as manifest
import autocode_design_plan as design_plan
import autocode_report_schema as reports
import autocode_run_view as run_view
import autocode_taskrun as taskrun
import autocode_util as util

from tests.visual_capture_fixtures import make_capture

ROOT = Path(__file__).resolve().parents[1]
BRIEF = (
    "Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
    "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
    "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
    "Python standard library only."
)
OPTIONS = (
    "--engine",
    "codex",
    "--joint-planning",
    "--astra-model",
    "gpt-6-astra",
    "--terra-model",
    "gpt-5.6-terra",
    "--sol-model",
    "gpt-5.6-sol",
    "--completion-model",
    "gpt-6-astra",
    "--glm-model",
    "gpt-5.6-sol",
    "--plan-reviewer-model",
    "gpt-6-astra",
)


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
    artifacts = {
        kind: {"path": path, "sha256": util.file_hash(root / path)}
        for kind, path in (("screenshot", "screen.png"), ("design_context", "context.txt"))
    }
    body = {
        "version": 1,
        "files": [{"key": "FILEA", "nodes": ["1:2"]}, {"key": "FILEB", "nodes": ["3:4"]}],
        "cases": [
            {
                "id": cid,
                "file_key": key,
                "node_id": node,
                "state": state,
                "route": "/greet",
                "implementation_paths": ["greet.py"],
                "viewport": {"width": 2, "height": 1, "device_scale_factor": 1},
                "export_scale": 1,
                "artifacts": copy.deepcopy(artifacts),
            }
            for cid, key, node, state in (
                ("greet.empty", "FILEA", "1:2", "empty"),
                ("greet.filled", "FILEB", "3:4", "filled"),
            )
        ],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(body))
    return path, body


def inventory_bundle(root, *, include_missing_reference=False):
    """Two connected-file snapshots with shared components and distinct variants."""
    root.mkdir(parents=True, exist_ok=True)
    png(root / "screen.png")
    (root / "context.txt").write_text("Complete screen context and component references")
    (root / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (root / "inter.woff2").write_bytes(b"font fixture")
    pages = {
        "FILEA": (
            "0:1",
            "Main",
            """<CANVAS id="0:1" name="Main">
          <FRAME id="1:2" name="Home" width="2" height="1">
            <FRAME id="1:90" name="Nested layout" width="400" height="100"/>
          </FRAME>
          <FRAME id="1:6" name="Details" width="2" height="1"/>
          <COMPONENT_SET id="1:3" name="Button">
            <COMPONENT id="1:4" name="Primary"/>
            <COMPONENT id="1:5" name="Secondary"/>
          </COMPONENT_SET>
          <RECTANGLE id="1:7" name="Logo"/>
        </CANVAS>""",
        ),
        "FILEB": (
            "2:1",
            "Other",
            """<CANVAS id="2:1" name="Other">
          <FRAME id="3:4" name="Settings" width="2" height="1"/>
          <COMPONENT_SET id="3:5" name="Button">
            <COMPONENT id="3:6" name="Tertiary"/>
          </COMPONENT_SET>
        </CANVAS>""",
        ),
    }
    files = []
    for key, (page_id, page_name, xml) in pages.items():
        xml_path = root / f"{key}-page.xml"
        xml_path.write_text(xml)
        file_xml_path = root / f"{key}-file.xml"
        file_xml_path.write_text("<DOCUMENT>" + xml + "</DOCUMENT>")
        component_page = page_id
        component_node = "1:3" if key == "FILEA" else "3:5"
        variants = (
            [
                {"page_id": component_page, "node_id": "1:4", "properties": {"Type": "primary"}},
                {"page_id": component_page, "node_id": "1:5", "properties": {"Type": "secondary"}},
            ]
            if key == "FILEA"
            else [{"page_id": component_page, "node_id": "3:6", "properties": {"Type": "tertiary"}}]
        )
        font = {
            "id": f"font-{key}",
            "family": "Inter",
            "style": "Regular",
            "status": "available",
            "artifact": {"path": "inter.woff2", "sha256": util.file_hash(root / "inter.woff2")},
        }
        asset = {
            "id": f"logo-{key}",
            "page_id": page_id,
            "node_id": "1:7" if key == "FILEA" else "3:4",
            "name": "Logo",
            "mime_type": "image/svg+xml",
            "status": "available",
            "artifact": {"path": "icon.svg", "sha256": util.file_hash(root / "icon.svg")},
        }
        if include_missing_reference and key == "FILEB":
            font = {
                "id": "font-missing",
                "family": "Unknown Sans",
                "style": "Medium",
                "status": "missing",
                "reason": "Figma font is unavailable in the pinned browser environment",
            }
            asset = {
                "id": "image-missing",
                "page_id": page_id,
                "node_id": "3:4",
                "name": "Hero",
                "mime_type": "image/png",
                "status": "missing",
                "reason": "Figma export did not return the source image",
            }
        files.append(
            {
                "key": key,
                "revision": f"revision-{key}",
                "pages": [
                    {
                        "id": page_id,
                        "name": page_name,
                        "metadata_xml": {"path": xml_path.name, "sha256": util.file_hash(xml_path)},
                    }
                ],
                "metadata_xml": {"path": file_xml_path.name, "sha256": util.file_hash(file_xml_path)},
                "components": [
                    {
                        "key": "SHARED-BUTTON",
                        "name": "Button",
                        "source_page_id": component_page,
                        "source_node_id": component_node,
                        "variants": variants,
                    }
                ],
                "variables": [
                    {
                        "key": "VARIABLE-COLOR",
                        "name": "foreground",
                        "kind": "COLOR",
                        "value": {"hex": "#112233"},
                        "collection": "Foundation",
                        "mode": "Light",
                        "source_id": "VariableID:1",
                        "mode_id": "light",
                    }
                ],
                "fonts": [font],
                "assets": [asset],
                "transitions": (
                    [
                        {
                            "id": "home-details",
                            "source_node_id": "1:2",
                            "target_node_id": "1:6",
                            "trigger": "click",
                            "action": {"type": "NODE", "destinationId": "1:6"},
                        }
                    ]
                    if key == "FILEA"
                    else []
                ),
            }
        )
    cases = []
    for key, page_id, node_id, state in (
        ("FILEA", "0:1", "1:2", "default"),
        ("FILEA", "0:1", "1:6", "default"),
        ("FILEB", "2:1", "3:4", "default"),
    ):
        cases.append(
            {
                "id": f"{key}.{node_id.replace(':', '.')}.{state}",
                "file_key": key,
                "page_id": page_id,
                "node_id": node_id,
                "state": state,
                "route": f"/{node_id.replace(':', '/')}",
                "implementation_paths": ["src/filea.js" if key == "FILEA" else "src/fileb.js"],
                "viewport": {"width": 2, "height": 1, "device_scale_factor": 1},
                "export_scale": 1,
                "artifacts": {
                    kind: {"path": path, "sha256": util.file_hash(root / path)}
                    for kind, path in (("screenshot", "screen.png"), ("design_context", "context.txt"))
                },
            }
        )
    files_by_key = {file["key"]: file for file in files}
    for case in cases:
        file = files_by_key[case["file_key"]]
        case["inventory_refs"] = {
            "components": [row["key"] for row in file["components"]],
            "variables": [row["key"] for row in file["variables"]],
            "fonts": [row["id"] for row in file["fonts"]],
            "assets": [row["id"] for row in file["assets"]],
            "transitions": [row["id"] for row in file["transitions"]],
        }
    for file in files:
        file["screen_states"] = [
            {
                "page_id": case["page_id"],
                "node_id": case["node_id"],
                "state": case["state"],
                "viewport": copy.deepcopy(case["viewport"]),
            }
            for case in cases
            if case["file_key"] == file["key"]
        ]
    for file in files:
        for page in file["pages"]:
            nodes, _ = manifest.inventory._metadata_nodes(page, root)[:2]
            rows = []
            for node in nodes.values():
                rows.append(dict(node, fonts=[], assets=[], variables=[], transitions=[], visual={}))
            rows[0]["fonts"] = [{key: file["fonts"][0][key] for key in ("family", "style")}]
            rows[0]["variables"] = ["VariableID:1"]
            for node in rows:
                for component in file["components"]:
                    if node["id"] == component["source_node_id"]:
                        node["component_key"] = component["key"]
                    for variant in component["variants"]:
                        if node["id"] == variant["node_id"]:
                            node["component_key"] = component["key"] + "-" + node["id"]
                            node["variant_properties"] = variant["properties"]
                node["assets"] = [
                    {key: row[key] for key in ("id", "mime_type")}
                    for row in file["assets"]
                    if row["node_id"] == node["id"]
                ]
                node["transitions"] = [
                    {key: row[key] for key in ("id", "target_node_id", "trigger", "action")}
                    for row in file["transitions"]
                    if row["source_node_id"] == node["id"]
                ]
            source = root / (file["key"] + "-source.json")
            source.write_text(
                json.dumps(
                    dict(
                        version=1,
                        file_key=file["key"],
                        page_id=page["id"],
                        read_only=True,
                        complete=True,
                        errors=[],
                        nodes=rows,
                        variables=file["variables"],
                    )
                )
            )
            page["source_json"] = {"path": source.name, "sha256": util.file_hash(source)}
    for case in cases:
        case["native_size"] = {"width": 2, "height": 1}
    body = {"version": 2, "files": files, "cases": cases, "responsive_targets": []}
    path = root / "manifest-v2.json"
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
        args = parser.parse_args(
            [
                "fixture",
                "--engine",
                "opencode",
                "--provider",
                "opencode",
                "--figma-manifest",
                str(self.path),
                "--terra-model",
                "zai/glm-5.3",
                "--terra-reasoning-effort",
                "high",
                "--pin-model-role",
                "terra",
                "--unlimited-iterations",
                "--max-seconds",
                "0",
                "--max-stage-seconds",
                "0",
                "--max-idle-seconds",
                "0",
                "--max-tool-seconds",
                "0",
            ]
        )
        state = {"workspace": str(self.workspace)}
        with (
            patch.object(opencode, "local_settings", return_value={"engine": "opencode"}),
            patch.object(opencode, "check_models"),
            patch.object(opencode, "check_subscription_routes"),
            patch.object(support, "local_settings", side_effect=AssertionError("No Codex login")),
        ):
            settings = autocode_configure.configure(
                args, state, planning=planning, milestones=milestones, autopilot=autopilot
            )
        self.assertEqual("opencode", settings["engine"])
        self.assertEqual(
            ("zai/glm-5.3", "high", True),
            tuple(settings["roles"]["terra"][key] for key in ("model", "reasoning_effort", "model_pinned")),
        )
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
        args = parser.parse_args(
            [
                "fixture",
                "--engine",
                "codex",
                "--figma-manifest",
                str(self.path),
                "--figma-file",
                "https://www.figma.com/design/FILEA?node-id=1-2",
            ]
        )
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}):
            settings = autocode_configure.configure(
                args, {"workspace": str(self.workspace)}, planning=planning, milestones=milestones, autopilot=autopilot
            )
        self.assertEqual("codex", settings["engine"])
        self.assertEqual(args.figma_file, settings["figma_file"])
        args.figma_file = "https://www.figma.com/design/UNDECLARED?node-id=1-2"
        with self.assertRaisesRegex(ValueError, "not declared"):
            autocode_configure.configure(
                args, {"workspace": str(self.workspace)}, planning=planning, milestones=milestones, autopilot=autopilot
            )
        args.figma_file = "https://www.figma.com/design/FILEA?node-id=1-2"
        with patch.object(support, "local_settings", return_value={"auth_mode": "API"}), self.assertRaises(ValueError):
            autocode_configure.configure(
                args, {"workspace": str(self.workspace)}, planning=planning, milestones=milestones, autopilot=autopilot
            )

    def test_native_page_collector_is_read_only_and_preserves_full_source_facts(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the connector JavaScript protocol")
        result = subprocess.run(
            [node, str(ROOT / "tools/test_figma_inventory_page.cjs")], text=True, capture_output=True, timeout=20
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_plugin_getter_receipt_reconciles_original_resources_and_compressed_transport(self):
        import autocode_design_sources as sources

        from tests.figma_inventory_fixtures import plugin_source_bundle

        if not shutil.which("node"):
            self.skipTest("Node is required for the offline connector fixture")
        path, body, receipt, parts = plugin_source_bundle(self.root / "typed-plugin")
        self.assertEqual(receipt, sources.reassemble(list(reversed(parts))))
        self.assertEqual("figma-plugin-api-properties-v1", receipt["source_format"])
        self.assertEqual(body, manifest.load(path)["body"])
        text = next(row for row in receipt["nodes"] if row["type"] == "TEXT")
        self.assertEqual("Hi 😁 friend", text["visual"]["characters"])
        self.assertEqual(5, text["visual"]["text_segments"][1]["start"])
        self.assertEqual({"wght": 600, "slnt": 0}, text["fonts"][1]["variationSettings"])
        self.assertEqual(["v1", "v2"], text["variables"])
        self.assertLess(parts[0]["encoded_length"], parts[0]["receipt_length"])

    def test_native_vector_paths_none_is_valid_without_relaxing_geometry_getters(self):
        from tests.figma_inventory_fixtures import plugin_source_bundle

        if not shutil.which("node"):
            self.skipTest("Node is required for the offline connector fixture")
        path, body, original, _ = plugin_source_bundle(self.root / "native-vector-none")
        source = path.parent / body["files"][0]["pages"][0]["source_json"]["path"]

        def retain(receipt):
            source.write_text(json.dumps(receipt))
            body["files"][0]["pages"][0]["source_json"]["sha256"] = util.file_hash(source)
            path.write_text(json.dumps(body))

        # Native SVG-imported vectors may return NONE for vectorPaths, as the
        # Plugin API VectorPath type permits. Keep the authored path verbatim.
        receipt = copy.deepcopy(original)
        visual = next(row for row in receipt["nodes"] if row["type"] == "VECTOR")["visual"]
        visual["vectorPaths"][0]["windingRule"] = "NONE"
        retain(receipt)
        loaded = manifest.load(path)
        self.assertEqual(body, loaded["body"])
        retained = json.loads(
            (Path(loaded["root"]) / loaded["body"]["files"][0]["pages"][0]["source_json"]["path"]).read_text()
        )
        self.assertEqual(
            visual["vectorPaths"],
            next(row for row in retained["nodes"] if row["type"] == "VECTOR")["visual"]["vectorPaths"],
        )

        for field, value in [
            ("fillGeometry", "NONE"),
            ("strokeGeometry", "NONE"),
            ("vectorPaths", "unknown"),
            ("vectorPaths", "none"),
            ("vectorPaths", None),
            ("vectorPaths", 0),
        ]:
            broken = copy.deepcopy(original)
            target = next(row for row in broken["nodes"] if row["type"] == "VECTOR")["visual"]
            target[field] = [{"data": visual["vectorPaths"][0]["data"], "windingRule": value}]
            retain(broken)
            with (
                self.subTest(field=field, value=value),
                self.assertRaisesRegex(ValueError, "vector getter is malformed"),
            ):
                manifest.load(path)

    def test_component_set_beta_motion_refusal_is_retained_and_scoped(self):
        import autocode_design_sources as sources

        from tests.figma_inventory_fixtures import plugin_source_bundle

        if not shutil.which("node"):
            self.skipTest("Node is required for the offline connector fixture")
        path, body, original, _ = plugin_source_bundle(self.root / "beta-motion-source")
        self.assertEqual(sources.NATIVE_CAPABILITY_PROFILE, original["property_profile"])
        nodes = {row["id"]: row for row in original["nodes"]}
        capability = nodes["1:3"]["visual"]["motion_capability"]
        self.assertEqual(sources.MOTION_FIELDS, capability["getter_errors"].keys())
        self.assertFalse(sources.MOTION_FIELDS & nodes["1:3"]["visual"].keys())
        self.assertEqual(
            {"TRANSLATION_X": {"timelineDuration": 100, "tracks": []}}, nodes["1:4"]["visual"]["animations"]
        )
        self.assertEqual("NAVIGATE", nodes["1:4"]["transitions"][0]["action"]["navigation"])
        self.assertEqual(body, manifest.load(path)["body"])
        legacy_path, legacy_body, legacy, _ = plugin_source_bundle(
            self.root / "supported-motion-source", component_set_motion="supported"
        )
        self.assertEqual(sources.PROFILE_NAME, legacy["property_profile"])
        self.assertEqual(legacy_body, manifest.load(legacy_path)["body"])
        legacy["property_profile"] = sources.NATIVE_CAPABILITY_PROFILE
        legacy_source = legacy_path.parent / "source.json"
        legacy_source.write_text(json.dumps(legacy))
        legacy_body["files"][0]["pages"][0]["source_json"]["sha256"] = util.file_hash(legacy_source)
        legacy_path.write_text(json.dumps(legacy_body))
        with self.assertRaisesRegex(ValueError, "lacks its original capability evidence"):
            manifest.load(legacy_path)
        for change in (
            "missing_capability",
            "partial_refusal",
            "different_error",
            "wrong_type",
            "legacy_profile",
            "readable_conflict",
            "variant_motion",
            "frame_motion",
            "null_capability",
            "boolean_version",
            "unreadable_marker",
            "motion_shape",
        ):
            receipt = copy.deepcopy(original)
            nodes = {row["id"]: row for row in receipt["nodes"]}
            visual = nodes["1:3"]["visual"]
            if change == "missing_capability":
                visual.pop("motion_capability")
            elif change == "partial_refusal":
                visual["motion_capability"]["getter_errors"].pop("timelines")
            elif change == "different_error":
                visual["motion_capability"]["getter_errors"]["timelines"] = "permission denied"
            elif change == "wrong_type":
                nodes["1:2"]["visual"]["motion_capability"] = visual.pop("motion_capability")
                for field in sources.MOTION_FIELDS:
                    nodes["1:2"]["visual"].pop(field)
            elif change == "legacy_profile":
                receipt["property_profile"] = sources.PROFILE_NAME
            elif change == "readable_conflict":
                visual["animations"] = {}
            elif change == "variant_motion":
                nodes["1:4"]["visual"].pop("animations")
            elif change == "frame_motion":
                nodes["1:2"]["visual"].pop("timelines")
            elif change == "null_capability":
                visual["motion_capability"] = None
            elif change == "boolean_version":
                visual["motion_capability"]["version"] = True
            elif change == "unreadable_marker":
                nodes["1:4"]["visual"]["animations"] = {"__figma_unreadable__": True}
            elif change == "motion_shape":
                nodes["1:4"]["visual"]["timelines"] = {}
            target = path.parent / "source.json"
            target.write_text(json.dumps(receipt))
            body["files"][0]["pages"][0]["source_json"]["sha256"] = util.file_hash(target)
            path.write_text(json.dumps(body))
            with self.subTest(change=change), self.assertRaises(ValueError):
                manifest.load(path)

    def test_section_native_capability_refusal_preserves_children_and_geometry(self):
        import autocode_design_sources as sources

        from tests.figma_inventory_fixtures import plugin_source_bundle

        if not shutil.which("node"):
            self.skipTest("Node is required for the offline connector fixture")
        path, body, original, _ = plugin_source_bundle(self.root / "section-source", include_section=True)
        self.assertEqual(body, manifest.load(path)["body"])
        nodes = {row["id"]: row for row in original["nodes"]}
        visual = nodes["31:2"]["visual"]
        self.assertEqual(sources.SECTION_CAPABILITY_FIELDS, visual["section_capability"]["getter_errors"].keys())
        self.assertFalse(sources.SECTION_CAPABILITY_FIELDS & visual.keys())
        self.assertEqual(["31:3"], visual["child_ids"])
        self.assertEqual(100, visual["width"])
        self.assertEqual("31:2", nodes["31:3"]["visual"]["parent_id"])
        self.assertEqual([], nodes["31:3"]["visual"]["strokeGeometry"])
        for change in (
            "missing_capability",
            "partial_refusal",
            "different_error",
            "wrong_type",
            "legacy_profile",
            "readable_conflict",
            "child_geometry",
            "section_dimensions",
            "child_order",
            "boolean_version",
        ):
            receipt = copy.deepcopy(original)
            nodes = {row["id"]: row for row in receipt["nodes"]}
            visual = nodes["31:2"]["visual"]
            if change == "missing_capability":
                visual.pop("section_capability")
            elif change == "partial_refusal":
                visual["section_capability"]["getter_errors"].pop("strokeGeometry")
            elif change == "different_error":
                visual["section_capability"]["getter_errors"]["minWidth"] = "permission denied"
            elif change == "wrong_type":
                nodes["31:3"]["visual"]["section_capability"] = visual.pop("section_capability")
            elif change == "legacy_profile":
                receipt["property_profile"] = sources.PROFILE_NAME
            elif change == "readable_conflict":
                visual["strokeGeometry"] = []
            elif change == "child_geometry":
                nodes["31:3"]["visual"].pop("strokeGeometry")
            elif change == "section_dimensions":
                visual.pop("width")
            elif change == "child_order":
                visual["child_ids"] = []
            elif change == "boolean_version":
                visual["section_capability"]["version"] = True
            target = path.parent / "source.json"
            target.write_text(json.dumps(receipt))
            body["files"][0]["pages"][0]["source_json"]["sha256"] = util.file_hash(target)
            path.write_text(json.dumps(body))
            with self.subTest(change=change), self.assertRaises(ValueError):
                manifest.load(path)

    def test_plugin_child_order_is_checked_in_one_pass_over_the_parent_map(self):
        # #1008: each container's ordered children come from one index, never a rescan per container.
        import autocode_design_sources as sources

        from tests.figma_inventory_fixtures import plugin_source_bundle

        if not shutil.which("node"):
            self.skipTest("Node is required for the offline connector fixture")
        path, body, receipt, _ = plugin_source_bundle(self.root / "child-order-index")
        scans, pages = [], []

        class Counted(dict):
            def items(self):
                scans.append(1)
                return super().items()

        validate = sources._validate_plugin

        def counted(receipt, metadata, parents, file):
            pages.append(1)
            return validate(receipt, metadata, Counted(parents), file)

        with patch.object(sources, "_validate_plugin", counted):
            self.assertEqual(body, manifest.load(path)["body"])
        self.assertGreater(sum(row["type"] in sources.CHILD_TYPES for row in receipt["nodes"]), 1)
        self.assertEqual(len(pages), len(scans))

    def test_plugin_transport_refuses_partial_corrupt_noncanonical_and_mixed_receipts(self):
        import base64
        import hashlib

        import autocode_design_sources as sources

        from tests.figma_inventory_fixtures import plugin_source_bundle

        if not shutil.which("node"):
            self.skipTest("Node is required for the offline connector fixture")
        _, _, _, original = plugin_source_bundle(self.root / "typed-transport")
        for change in (
            "missing",
            "duplicate",
            "truncated",
            "mixed_hash",
            "mixed_page",
            "digest",
            "code",
            "base64",
            "size",
            "oversized",
            "format",
        ):
            parts = copy.deepcopy(original)
            if change == "missing":
                parts.pop()
            elif change == "duplicate":
                parts.append(copy.deepcopy(parts[0]))
            elif change == "truncated":
                parts[-1]["json_part"] = parts[-1]["json_part"][:-1]
            elif change == "mixed_hash":
                parts[-1]["receipt_sha256"] = "0" * 64
            elif change == "mixed_page":
                parts[-1]["page_id"] = "9:9"
            elif change == "digest":
                for part in parts:
                    part["receipt_sha256"] = "0" * 64
            elif change == "code":
                parts[0]["json_part"] = "/" + parts[0]["json_part"][1:]
            elif change == "base64":
                parts[0]["json_part"] = "!" + parts[0]["json_part"][1:]
            elif change == "size":
                for part in parts:
                    part["receipt_length"] -= 1
            elif change == "oversized":
                for part in parts:
                    part["receipt_length"] = sources.TRANSPORT_LIMIT + 1
            elif change == "format":
                for part in parts:
                    part["encoding"] = "invented-compression"
            with self.subTest(change=change), self.assertRaises(ValueError):
                sources.reassemble(parts)
        # This legally decodes to the same ASCII bytes but literal-only LZW is
        # noncanonical. A matching SHA cannot legitimize a lossy/alternate codec.
        text = b'{"file_key":"FILEA","page_id":"0:1","source_format":"figma-plugin-api-properties-v1","complete":true,"errors":[]}'
        binary = b"\x01\x00" + b"".join(code.to_bytes(2, "big") for code in text)
        encoded = base64.b64encode(binary).decode()
        part = dict(
            transport_version=1,
            encoding="lzw16-base64-json-ascii-v1",
            source_format=sources.PLUGIN_SOURCE,
            file_key="FILEA",
            page_id="0:1",
            receipt_sha256=hashlib.sha256(text).hexdigest(),
            receipt_length=len(text),
            encoded_length=len(encoded),
            part_index=0,
            part_count=1,
            complete=True,
            error_count=0,
            json_part=encoded,
        )
        with self.assertRaisesRegex(ValueError, "noncanonical"):
            sources.reassemble([part])
        with self.assertRaisesRegex(ValueError, "size bound"):
            sources._lzw_decode(sources._lzw_encode(b"A" * 10000), 20)

    def test_gzip_source_transport_rejects_extra_members_corruption_and_size_bombs(self):
        import base64
        import gzip
        import hashlib
        import zlib

        import autocode_design_sources as sources

        from tests.figma_inventory_fixtures import plugin_source_bundle

        if not shutil.which("node"):
            self.skipTest("Node is required for the offline connector fixture")
        _, _, original, parts = plugin_source_bundle(self.root / "gzip-source-controls")
        self.assertEqual("gzip-base64-json-ascii-v1", parts[0]["encoding"])
        encoded = "".join(part["json_part"] for part in sorted(parts, key=lambda row: row["part_index"]))
        binary = base64.b64decode(encoded)
        text = zlib.decompress(binary, wbits=16 + zlib.MAX_WBITS)
        self.assertEqual(original, json.loads(text))
        self.assertEqual(text, sources._gzip_decode(binary, len(text)))
        self.assertEqual(original, sources.reassemble(list(reversed(parts))))
        # Legacy original receipts remain accepted byte-for-byte.
        legacy_binary = sources._lzw_encode(text)

        def packets(payload, *, encoding="gzip-base64-json-ascii-v1", length=len(text), digest=None):
            value = base64.b64encode(payload).decode()
            header = {key: parts[0][key] for key in parts[0] if key not in ("part_index", "part_count", "json_part")}
            header.update(
                encoding=encoding,
                receipt_length=length,
                encoded_length=len(value),
                receipt_sha256=digest or hashlib.sha256(text).hexdigest(),
            )
            return [
                dict(
                    header,
                    part_index=index,
                    part_count=(len(value) + 13999) // 14000,
                    json_part=value[index * 14000 : (index + 1) * 14000],
                )
                for index in range((len(value) + 13999) // 14000)
            ]

        self.assertEqual(original, sources.reassemble(packets(legacy_binary, encoding="lzw16-base64-json-ascii-v1")))
        crc = bytearray(binary)
        crc[-8] ^= 1
        isize = bytearray(binary)
        isize[-4] ^= 1
        metadata = bytearray(binary)
        metadata[4] = 1
        non_ascii = gzip.compress(b"\xff", mtime=0, compresslevel=6)
        non_ascii = non_ascii[:9] + b"\x03" + non_ascii[10:]
        for change, altered, length, digest in (
            ("extra_member", binary + binary, len(text), None),
            ("extra_byte", binary + b"X", len(text), None),
            ("truncated", binary[:-1], len(text), None),
            ("crc", bytes(crc), len(text), None),
            ("isize", bytes(isize), len(text), None),
            ("metadata", bytes(metadata), len(text), None),
            ("wrong_wrapper", zlib.compress(text), len(text), None),
            ("decoded_bound", binary, len(text) - 1, None),
            ("non_ascii", non_ascii, 1, hashlib.sha256(b"\xff").hexdigest()),
            ("oversized", binary, sources.TRANSPORT_LIMIT + 1, None),
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                sources.reassemble(packets(altered, length=length, digest=digest))
        bomb = gzip.compress(b"A" * (2 * 1024 * 1024), mtime=0, compresslevel=6)
        bomb = bomb[:9] + b"\x03" + bomb[10:]
        with self.assertRaisesRegex(ValueError, "decoded size bound"):
            sources._gzip_decode(bomb, 20)

    def test_plugin_source_cannot_omit_original_getters_ranges_aliases_modes_or_variant_identity(self):
        from tests.figma_inventory_fixtures import plugin_source_bundle

        if not shutil.which("node"):
            self.skipTest("Node is required for the offline connector fixture")
        path, body, original, _ = plugin_source_bundle(self.root / "typed-source-refusals")
        for change in (
            "getter",
            "unreadable",
            "transform",
            "bounds",
            "stroke_map",
            "vector_map",
            "identity",
            "parent",
            "child_order",
            "segment_field",
            "segment_gap",
            "segment_text",
            "font",
            "rich_asset",
            "alias",
            "mode",
            "mode_roster",
            "variant",
            "instance",
            "document_roster",
            "document_name",
            "format",
            "provenance_downgrade",
        ):
            receipt = copy.deepcopy(original)
            nodes = {row["id"]: row for row in receipt["nodes"]}
            frame, text = nodes["1:2"], nodes["I4:5;10:12"]
            if change == "getter":
                frame["visual"].pop("paddingLeft")
            elif change == "unreadable":
                frame["visual"]["effects"] = {"__figma_unreadable__": True}
            elif change == "transform":
                frame["visual"]["absoluteTransform"] = {}
            elif change == "bounds":
                frame["visual"]["absoluteBoundingBox"] = {}
            elif change == "stroke_map":
                frame["visual"]["complexStrokeProperties"] = {}
            elif change == "vector_map":
                nodes["1:8"]["visual"]["vectorNetwork"] = {}
            elif change == "identity":
                frame["visual"]["id"] = "1:99"
            elif change == "parent":
                text["visual"]["parent_id"] = "0:1"
            elif change == "child_order":
                frame["visual"]["child_ids"].reverse()
            elif change == "segment_field":
                text["visual"]["text_segments"][1].pop("letterSpacing")
            elif change == "segment_gap":
                text["visual"]["text_segments"][1]["start"] = 6
            elif change == "segment_text":
                text["visual"]["text_segments"][0]["characters"] = "Hi"
            elif change == "font":
                text["fonts"].pop()
            elif change == "rich_asset":
                text["assets"] = []
            elif change == "alias":
                nodes["1:7"]["variables"] = []
            elif change == "mode":
                frame["visual"]["resolvedVariableModes"] = {"c1": "invented"}
            elif change == "mode_roster":
                receipt["variable_sources"][0]["valuesByMode"].pop("dark")
            elif change == "variant":
                nodes["1:4"]["visual"]["variantProperties"] = {"Type": "invented"}
                nodes["1:4"]["variant_properties"] = {"Type": "invented"}
            elif change == "instance":
                nodes["1:9"]["component_ref"]["key"] = "invented"
            elif change == "document_roster":
                receipt["document_pages"].append(dict(id="9:1", name="Omitted page", type="PAGE"))
            elif change == "document_name":
                receipt["document_pages"][0]["name"] = "Invented name"
            elif change == "format":
                receipt["source_format"] = "invented-source"
            elif change == "provenance_downgrade":
                receipt.pop("source_format")
            target = path.parent / "source.json"
            target.write_text(json.dumps(receipt))
            body["files"][0]["pages"][0]["source_json"]["sha256"] = util.file_hash(target)
            path.write_text(json.dumps(body))
            with self.subTest(change=change), self.assertRaises(ValueError):
                manifest.load(path)

    def test_plugin_child_order_uses_metadata_order_with_shuffled_receipt_nodes(self):
        from tests.figma_inventory_fixtures import plugin_source_bundle

        if not shutil.which("node"):
            self.skipTest("Node is required for the offline connector fixture")
        path, body, original, _ = plugin_source_bundle(self.root / "metadata-child-order")
        nodes = {row["id"]: row for row in original["nodes"]}
        self.assertEqual(["1:2", "1:3"], nodes["0:1"]["visual"]["child_ids"])
        self.assertEqual(["I4:5;10:12", "1:7", "1:8", "1:9"], nodes["1:2"]["visual"]["child_ids"])
        self.assertEqual([], nodes["1:4"]["visual"]["child_ids"])
        source = path.parent / body["files"][0]["pages"][0]["source_json"]["path"]
        for reverse_nodes in (False, True):
            receipt = copy.deepcopy(original)
            if reverse_nodes:
                receipt["nodes"].reverse()
            source.write_text(json.dumps(receipt))
            body["files"][0]["pages"][0]["source_json"]["sha256"] = util.file_hash(source)
            path.write_text(json.dumps(body))
            with self.subTest(reverse_nodes=reverse_nodes):
                self.assertEqual(body, manifest.load(path)["body"])

    def test_plugin_child_relationship_corruption_keeps_specific_public_refusals(self):
        from tests.figma_inventory_fixtures import plugin_source_bundle

        if not shutil.which("node"):
            self.skipTest("Node is required for the offline connector fixture")
        path, body, original, _ = plugin_source_bundle(self.root / "invalid-child-relationships")
        source = path.parent / body["files"][0]["pages"][0]["source_json"]["path"]
        for change in ("reordered", "wrong_parent", "emptied", "invented"):
            receipt = copy.deepcopy(original)
            nodes = {row["id"]: row for row in receipt["nodes"]}
            expected = "Figma original child order differs from complete page metadata"
            if change == "reordered":
                nodes["1:2"]["visual"]["child_ids"].reverse()
            elif change == "wrong_parent":
                nodes["I4:5;10:12"]["visual"]["parent_id"] = "0:1"
                expected = "Figma original parent differs from complete page metadata"
            elif change == "emptied":
                nodes["1:2"]["visual"]["child_ids"] = []
            elif change == "invented":
                nodes["1:4"]["visual"]["child_ids"] = ["I4:5;10:12"]
            source.write_text(json.dumps(receipt))
            body["files"][0]["pages"][0]["source_json"]["sha256"] = util.file_hash(source)
            path.write_text(json.dumps(body))
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, expected):
                manifest.load(path)

    def test_retained_manifest_index_is_bound_and_artifacts_cannot_overwrite_it(self):
        record = manifest.retain(manifest.load(self.path), self.workspace)
        Path(record["manifest_path"]).write_text("{}")
        with self.assertRaisesRegex(ValueError, "index"):
            manifest.verify(record)
        body = copy.deepcopy(self.body)
        shutil.copyfile(self.path.parent / "context.txt", self.path.parent / "inventory-manifest.json")
        body["cases"][0]["artifacts"]["design_context"]["path"] = "inventory-manifest.json"
        self.path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "reserved"):
            manifest.retain(manifest.load(self.path), self.workspace)

    def test_library_only_file_preserves_resources_and_component_prototype_endpoints(self):
        path, body = inventory_bundle(self.root / "library-only")
        file = body["files"][1]
        for artifact in (file["metadata_xml"], file["pages"][0]["metadata_xml"]):
            target = path.parent / artifact["path"]
            target.write_text(target.read_text().replace('<FRAME id="3:4" name="Settings" width="2" height="1"/>', ""))
            artifact["sha256"] = util.file_hash(target)
        file["assets"][0]["node_id"] = "3:6"
        file["transitions"] = [
            dict(
                id="variant-toggle",
                source_node_id="3:6",
                target_node_id="3:6",
                trigger="click",
                action={"type": "NODE", "destinationId": "3:6"},
            )
        ]
        target = path.parent / file["pages"][0]["source_json"]["path"]
        receipt = json.loads(target.read_text())
        receipt["nodes"] = [row for row in receipt["nodes"] if row["id"] != "3:4"]
        variant = next(row for row in receipt["nodes"] if row["id"] == "3:6")
        variant["assets"] = [{"id": "logo-FILEB", "mime_type": "image/svg+xml"}]
        variant["transitions"] = [
            {key: row[key] for key in ("id", "target_node_id", "trigger", "action")} for row in file["transitions"]
        ]
        target.write_text(json.dumps(receipt))
        file["pages"][0]["source_json"]["sha256"] = util.file_hash(target)
        file["screen_states"] = []
        body["cases"] = body["cases"][:2]
        for case in body["cases"]:
            case["inventory_refs"]["assets"].append("FILEB/logo-FILEB")
            case["inventory_refs"]["fonts"].append("FILEB/font-FILEB")
            case["inventory_refs"]["transitions"].append("FILEB/variant-toggle")
        path.write_text(json.dumps(body))
        record = manifest.load(path)
        projection = design_inventory.builder_slice(body, path.parent, ["src/filea.js"])
        self.assertEqual(2, len(projection["case_ids"]))
        self.assertIn("variant-toggle", {row["id"] for row in projection["catalog"]["transitions"]})
        import autocode_design_intake as intake

        intake.require_references(
            record, ["https://www.figma.com/design/FILEA?node-id=1-2", "https://www.figma.com/design/FILEB?node-id=3-5"]
        )

    def test_malformed_page_source_is_a_diagnostic_and_never_an_unhandled_exception(self):
        path, body = inventory_bundle(self.root / "malformed-source")
        artifact = body["files"][0]["pages"][0]["source_json"]
        target = path.parent / artifact["path"]
        receipt = json.loads(target.read_text())
        receipt.pop("nodes")
        target.write_text(json.dumps(receipt))
        artifact["sha256"] = util.file_hash(target)
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "Malformed Figma"):
            manifest.load(path)

    def test_component_properties_and_screen_resource_ownership_cannot_be_fabricated(self):
        path, body = inventory_bundle(self.root / "source-bindings")
        for change in ("key", "variant", "asset", "transition"):
            broken = copy.deepcopy(body)
            if change == "key":
                broken["files"][0]["components"][0]["key"] = "FAKE-KEY"
                for case in broken["cases"][:2]:
                    case["inventory_refs"]["components"] = ["FAKE-KEY"]
            if change == "variant":
                broken["files"][0]["components"][0]["variants"][0]["properties"] = {"Type": "invented"}
            if change == "asset":
                broken["cases"][-1]["inventory_refs"]["assets"] = []
                broken["cases"][0]["inventory_refs"]["assets"].append("FILEB/logo-FILEB")
            if change == "transition":
                broken["cases"][0]["inventory_refs"]["transitions"] = []
            path.write_text(json.dumps(broken))
            with self.subTest(change=change), self.assertRaises(ValueError):
                manifest.load(path)

    def test_reference_delta_binds_affected_source_properties_without_invalidating_other_frames(self):
        path, body = inventory_bundle(self.root / "source-drift")
        old = manifest.load(path)
        replacement = self.root / "source-drift-new"
        shutil.copytree(path.parent, replacement)
        source = replacement / "FILEA-source.json"
        receipt = json.loads(source.read_text())
        next(row for row in receipt["nodes"] if row["id"] == "1:2")["visual"] = {"fills": [{"color": "changed"}]}
        source.write_text(json.dumps(receipt))
        body["files"][0]["pages"][0]["source_json"]["sha256"] = util.file_hash(source)
        (replacement / path.name).write_text(json.dumps(body))
        change = design_identity.delta(old, manifest.load(replacement / path.name))
        self.assertEqual(["FILEA.1.2.default"], change["changed"])
        self.assertEqual(["FILEA.1.6.default", "FILEB.3.4.default"], change["unchanged"])

    def test_relocated_identical_reference_bytes_do_not_invalidate_design_evidence(self):
        path, body = inventory_bundle(self.root / "identity-relocation")
        original = manifest.load(path)
        replacement = self.root / "identity-relocation-new"
        shutil.copytree(path.parent, replacement)
        (replacement / "renamed-context.txt").write_bytes((replacement / "context.txt").read_bytes())
        body["cases"][0]["artifacts"]["design_context"]["path"] = "renamed-context.txt"
        (replacement / path.name).write_text(json.dumps(body))
        change = design_identity.delta(original, manifest.load(replacement / path.name))
        self.assertEqual([], change["changed"])
        self.assertEqual(sorted(case["id"] for case in body["cases"]), change["unchanged"])

    def test_shared_variable_aliases_resolve_by_library_key_not_file_local_id(self):
        path, body = inventory_bundle(self.root / "variable-aliases")
        for file in body["files"]:
            color = file["variables"][0]
            color["source_id"] = "VariableID:" + file["key"]
            alias = {
                **copy.deepcopy(color),
                "key": "VARIABLE-ALIAS",
                "name": "alias",
                "source_id": "AliasID:" + file["key"],
                "value": {"type": "VARIABLE_ALIAS", "id": color["source_id"]},
            }
            file["variables"].append(alias)
            for case in body["cases"]:
                if case["file_key"] == file["key"]:
                    case["inventory_refs"]["variables"].append(alias["key"])
            page = file["pages"][0]
            source_path = path.parent / page["source_json"]["path"]
            receipt = json.loads(source_path.read_text())
            receipt["variables"] = file["variables"]
            for node in receipt["nodes"]:
                if node["variables"]:
                    node["variables"] = [color["source_id"]]
            source_path.write_text(json.dumps(receipt))
            page["source_json"]["sha256"] = util.file_hash(source_path)
        path.write_text(json.dumps(body))
        manifest.load(path)
        catalog = design_inventory.catalog(body, path.parent)
        self.assertEqual(2, len(catalog["variables"]))
        reordered = copy.deepcopy(body)
        reordered["files"].reverse()
        self.assertEqual(catalog, design_inventory.catalog(reordered, path.parent))
        conflicting = copy.deepcopy(body)
        conflicting["files"][1]["variables"][0]["value"] = {"hex": "#ffffff"}
        source_path = path.parent / conflicting["files"][1]["pages"][0]["source_json"]["path"]
        receipt = json.loads(source_path.read_text())
        receipt["variables"] = conflicting["files"][1]["variables"]
        source_path.write_text(json.dumps(receipt))
        conflicting["files"][1]["pages"][0]["source_json"]["sha256"] = util.file_hash(source_path)
        path.write_text(json.dumps(conflicting))
        with self.assertRaisesRegex(ValueError, "conflicting definitions"):
            manifest.load(path)

    def test_two_files_and_declared_frames_cannot_be_silently_dropped(self):
        manifest.load(self.path)
        for change in ("file", "case", "undeclared", "duplicate_id", "duplicate_state"):
            body = copy.deepcopy(self.body)
            if change == "file":
                body["files"].pop()
            if change == "case":
                body["cases"].pop()
            if change == "undeclared":
                body["cases"][0]["node_id"] = "99:1"
            if change == "duplicate_id":
                body["cases"][1]["id"] = body["cases"][0]["id"]
            if change == "duplicate_state":
                body["cases"].append({**body["cases"][0], "id": "another"})
            with self.subTest(change=change), self.assertRaises(ValueError):
                manifest.validate(body)

    def test_v2_inventory_discovers_all_page_frames_and_merges_shared_component_variants(self):
        path, body = inventory_bundle(self.root / "inventory")
        record = manifest.load(path)
        catalog = design_inventory.catalog(body, path.parent)
        self.assertEqual(catalog, design_inventory.catalog(copy.deepcopy(body), path.parent))
        self.assertEqual(3, len(catalog["screen_frames"]))
        self.assertEqual("SHARED-BUTTON", catalog["components"][0]["key"])
        self.assertEqual(3, len(catalog["components"][0]["variants"]))
        self.assertEqual(["FILEA", "FILEB"], [row["file_key"] for row in catalog["fonts"][0]["sources"]])
        self.assertEqual(1, len(catalog["variables"]))
        self.assertEqual([], manifest.blockers(record))
        self.assertEqual(3, len(manifest.context({"design_manifest": record})["catalog"]["cases"]))

    def test_builder_context_is_limited_to_affected_cases_and_their_inventory(self):
        path, _ = inventory_bundle(self.root / "builder-slice")
        record = manifest.retain(manifest.load(path), self.workspace)
        settings = {"design_manifest": record}
        builder = manifest.context(settings, stage="terra", current_task={"affected_paths": ["src/filea.js"]})
        self.assertEqual(["FILEA.1.2.default", "FILEA.1.6.default"], builder["builder_scope"]["case_ids"])
        self.assertNotIn("body", builder)
        self.assertEqual(
            [case["id"] for case in record["body"]["cases"]], [row["id"] for row in builder["case_roster"]]
        )
        self.assertEqual({"FILEA", "FILEB"}, {row["key"] for row in builder["catalog"]["files"]})
        # Shared design-system identities keep their distinct variants from other approved files.
        self.assertEqual(3, len(builder["catalog"]["components"][0]["variants"]))
        self.assertEqual([], builder["builder_scope"]["unmapped_affected_paths"])

        unrelated = manifest.context(settings, stage="terra", current_task={"affected_paths": ["tests/"]})
        self.assertEqual([], unrelated["builder_scope"]["case_ids"])
        self.assertEqual(["tests"], unrelated["builder_scope"]["unmapped_affected_paths"])
        self.assertEqual(3, len(manifest.context(settings, stage="astra_review")["catalog"]["cases"]))

    def test_v2_every_inventory_row_requires_a_case_mapping(self):
        _, body = inventory_bundle(self.root / "unmapped-inventory")
        body["cases"][0]["inventory_refs"]["fonts"].clear()
        body["cases"][1]["inventory_refs"]["fonts"].clear()
        with self.assertRaisesRegex(ValueError, "no case mapping"):
            manifest.validate(body, root=self.root / "unmapped-inventory")

    def test_v2_undeclared_source_typography_assets_variables_and_transitions_are_refused(self):
        path, body = inventory_bundle(self.root / "unreported-source")
        for section in ("fonts", "assets", "variables", "transitions"):
            broken = copy.deepcopy(body)
            broken["files"][0][section] = []
            for case in broken["cases"]:
                if case["file_key"] == "FILEA":
                    case["inventory_refs"][section] = []
            path.write_text(json.dumps(broken))
            with self.subTest(section=section), self.assertRaisesRegex(ValueError, "omit|change source"):
                manifest.load(path)

    def test_v2_native_dimensions_cannot_be_replaced_by_thumbnail_dimensions(self):
        path, body = inventory_bundle(self.root / "native-size")
        body["cases"][0]["native_size"]["width"] = 1440
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "Native Figma size"):
            manifest.load(path)

    def test_v2_invalid_native_dimensions_are_input_errors(self):
        path, body = inventory_bundle(self.root / "invalid-native-size")
        for value in (None, True, "1440", float("inf"), float("nan"), [], 0, -1):
            broken = copy.deepcopy(body)
            broken["cases"][0]["native_size"]["width"] = value
            path.write_text(json.dumps(broken))
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finite and positive"):
                manifest.load(path)

    def test_v2_generated_planner_report_loads_and_installs_design_coverage(self):
        import autocode as runner
        import autocode_goal_lifecycle as lifecycle
        import autocode_planning as planning
        import autopilot
        from goal_fixtures import body as goal_body

        path, inventory = inventory_bundle(self.root / "planner-inventory")
        design = manifest.load(path)
        contract = goal_body()
        contract["milestones"][0]["affected_paths"] = ["src/"]
        contract["initial_task"] = {
            "objective": "Validate the complete supplied UI",
            "affected_paths": ["src/"],
            "kind": "validate",
            "milestone_id": "M1",
            "requirements": ["Inspect every supplied design case"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["python3 -m unittest discover"],
        }
        contract["design_coverage"] = {
            "manifest_hash": design["manifest_hash"],
            "cases": [
                {"id": case["id"], "criterion_ids": ["C1"], "milestone_ids": ["M1"]} for case in inventory["cases"]
            ],
            "responsive_derivations": [],
        }

        def state():
            return {
                "task_id": "fixture-task",
                "task": "Validate the supplied UI",
                "workspace": str(self.workspace),
                "settings": {"joint_planning": True, "planning_flow": "v2", "design_manifest": design},
                "answers": {},
                "user_events": [],
                "acceptance_criteria": [],
            }

        def load(candidate, report, name):
            output, schema_path = self.root / f"{name}.json", self.root / f"{name}.schema.json"
            output.write_text(json.dumps({"contract": report, "summary": "All cases planned"}))
            schema_path.write_text(json.dumps(planning.schema_for(candidate, "plan")))
            record = {"stage": "plan", "engine": "codex", "output": str(output), "schema": str(schema_path)}
            return runner.load_stage_report(record, state=candidate), record

        candidate = state()
        value, record = load(candidate, contract, "complete")
        autopilot.apply_planning(candidate, "plan", value, record)
        public = run_view.view(candidate)
        self.assertEqual("plan_review", public["next_stage"])
        self.assertEqual(contract["design_coverage"], public["design"]["plan_coverage"])
        rendered = lifecycle.render(candidate)
        for case in inventory["cases"]:
            self.assertIn(case["id"], rendered)

        for change in ("malformed", "missing_case", "unknown_criterion"):
            with self.subTest(change=change):
                rejected = state()
                before = lifecycle.render(rejected)
                broken = copy.deepcopy(contract)
                if change == "malformed":
                    broken["design_coverage"]["cases"] = "not an array"
                elif change == "missing_case":
                    broken["design_coverage"]["cases"].pop()
                else:
                    broken["design_coverage"]["cases"][0]["criterion_ids"] = ["unknown"]
                value, record = load(rejected, broken, change)
                with self.assertRaisesRegex(
                    ValueError, "expected array|map every approved design case|unknown product criteria"
                ):
                    autopilot.apply_planning(rejected, "plan", value, record)
                self.assertEqual(before, lifecycle.render(rejected))

    def test_v2_plan_coverage_binds_every_case_to_existing_criteria_and_ownership(self):
        path, body = inventory_bundle(self.root / "plan-inventory")
        record = manifest.load(path)
        plan = {
            "acceptance_criteria": [{"id": "AC1"}],
            "open_blocking_questions": [],
            "milestones": [{"id": "M1", "acceptance_criteria": ["AC1"], "affected_paths": ["src/"]}],
            "design_coverage": {
                "manifest_hash": record["manifest_hash"],
                "cases": [
                    {"id": case["id"], "criterion_ids": ["AC1"], "milestone_ids": ["M1"]} for case in body["cases"]
                ],
                "responsive_derivations": [],
            },
        }
        design_plan.validate(record, plan, ready=True)
        for change in ("missing_case", "stale_reference", "unknown_criterion", "unowned_path"):
            broken = copy.deepcopy(plan)
            if change == "missing_case":
                broken["design_coverage"]["cases"].pop()
            if change == "stale_reference":
                broken["design_coverage"]["manifest_hash"] = "other"
            if change == "unknown_criterion":
                broken["design_coverage"]["cases"][0]["criterion_ids"] = ["unknown"]
            if change == "unowned_path":
                broken["milestones"][0]["affected_paths"] = ["tests/"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                design_plan.validate(record, broken, ready=True)
        self.assertIn("FILEB.3.4.default", "\n".join(design_plan.render(record, plan)))
        # Both declarations are approved contract data; a second valid CID must
        # not silently assign a source case to a different visual criterion.
        plan["acceptance_criteria"].append({"id": "AC2"})
        plan["milestones"][0]["acceptance_criteria"].append("AC2")
        mapping = {case["id"]: ["AC1"] for case in body["cases"]}
        declaration = "VISUAL_CASE_CRITERIA=" + json.dumps(mapping)
        plan["constraints"] = [declaration]
        design_plan.validate(record, plan, ready=True)
        wrong = copy.deepcopy(mapping)
        first = body["cases"][0]["id"]
        wrong[first] = ["AC2"]
        duplicate = json.dumps(mapping).replace("{", "{" + json.dumps(first) + ': ["AC2"], ', 1)
        for rows in (
            ["VISUAL_CASE_CRITERIA=" + json.dumps(wrong)],
            [declaration, declaration],
            ["VISUAL_CASE_CRITERIA=" + duplicate],
            ["VISUAL_CASE_CRITERIA={"],
        ):
            broken = copy.deepcopy(plan)
            broken["constraints"] = rows
            with self.subTest(visual_declaration=rows), self.assertRaises(ValueError):
                design_plan.validate(record, broken, ready=True)
        body["cases"][0]["implementation_paths"].append("styles/home.css")
        path.write_text(json.dumps(body))
        record = manifest.load(path)
        plan["design_coverage"]["manifest_hash"] = record["manifest_hash"]
        with self.assertRaisesRegex(ValueError, "owning its implementation paths"):
            design_plan.validate(record, plan, ready=True)
        plan["milestones"][0]["affected_paths"].append("styles/")
        design_plan.validate(record, plan, ready=True)

    def test_absent_responsive_reference_requires_documented_derivation(self):
        path, body = inventory_bundle(self.root / "responsive")
        body["responsive_targets"] = [
            {
                "id": "home.mobile",
                "source_case_id": body["cases"][0]["id"],
                "reference_case_id": "",
                "viewport": {"width": 1, "height": 2, "device_scale_factor": 1},
                "constraints": [],
            }
        ]
        path.write_text(json.dumps(body))
        record = manifest.load(path)
        plan = {
            "acceptance_criteria": [{"id": "AC1"}],
            "open_blocking_questions": [],
            "milestones": [{"id": "M1", "acceptance_criteria": ["AC1"], "affected_paths": ["src/"]}],
            "design_coverage": {
                "manifest_hash": record["manifest_hash"],
                "cases": [
                    {"id": case["id"], "criterion_ids": ["AC1"], "milestone_ids": ["M1"]} for case in body["cases"]
                ],
                "responsive_derivations": [],
            },
        }
        with self.assertRaisesRegex(ValueError, "responsive target"):
            design_plan.validate(record, plan, ready=True)
        plan["design_coverage"]["responsive_derivations"] = [
            {
                "target_id": "home.mobile",
                "exact_match": False,
                "basis": "derived_behavior",
                "behavior": "Stack the two panes while retaining chat navigation",
                "criterion_ids": ["AC1"],
                "milestone_ids": ["M1"],
            }
        ]
        design_plan.validate(record, plan, ready=True)
        plan["design_coverage"]["responsive_derivations"][0]["exact_match"] = True
        with self.assertRaises(ValueError):
            design_plan.validate(record, plan, ready=True)

    def test_v2_incomplete_source_frame_and_transition_endpoint_block_preflight(self):
        path, body = inventory_bundle(self.root / "incomplete")
        body["cases"].pop()
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "missing cases.*FILEB"):
            manifest.load(path)
        _, body = inventory_bundle(self.root / "broken-transition")
        body["files"][0]["transitions"][0]["target_node_id"] = "9:99"
        path = self.root / "broken-transition" / "manifest-v2.json"
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "unknown Figma node|source prototype actions"):
            manifest.load(path)

    def test_v2_omitted_page_or_component_variant_is_rejected_against_source(self):
        path, body = inventory_bundle(self.root / "omitted-page")
        file_metadata = body["files"][0]["metadata_xml"]
        metadata_path = path.parent / file_metadata["path"]
        metadata_path.write_text(
            metadata_path.read_text().replace("</DOCUMENT>", '<CANVAS id="9:9" name="Omitted page"/></DOCUMENT>')
        )
        file_metadata["sha256"] = util.file_hash(metadata_path)
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "file pages and metadata differ"):
            manifest.load(path)

        path, body = inventory_bundle(self.root / "omitted-variant")
        body["files"][0]["components"][0]["variants"].pop()
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "incomplete variant coverage"):
            manifest.load(path)

        path, body = inventory_bundle(self.root / "omitted-component")
        body["files"][0]["components"] = []
        for case in body["cases"]:
            if case["file_key"] == "FILEA":
                case["inventory_refs"]["components"] = []
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "component inventory differs"):
            manifest.load(path)

    def test_v2_missing_approved_state_case_is_not_hidden_by_frame_coverage(self):
        path, body = inventory_bundle(self.root / "omitted-state")
        body["files"][0]["screen_states"].append(
            {
                "page_id": "0:1",
                "node_id": "1:2",
                "state": "empty",
                "viewport": {"width": 2, "height": 1, "device_scale_factor": 1},
            }
        )
        path.write_text(json.dumps(body))
        with self.assertRaisesRegex(ValueError, "source states and cases differ"):
            manifest.load(path)

    def test_v2_missing_fonts_and_assets_are_visible_blockers_and_refuse_coverage(self):
        path, body = inventory_bundle(self.root / "missing", include_missing_reference=True)
        record = manifest.load(path)
        blocked = {"settings": {"design_manifest": record}, "validation": {}}
        self.assertEqual(2, len(manifest.blockers(record)))
        self.assertFalse(coverage.ready(blocked))
        self.assertIn("unavailable required fonts/assets", completion.rejection(blocked))

    def test_v2_metadata_drift_is_hash_bound_and_isolated_retention_is_read_only(self):
        path, body = inventory_bundle(self.root / "metadata")
        record = manifest.retain(manifest.load(path), self.workspace)
        page_path = Path(record["root"]) / body["files"][0]["pages"][0]["metadata_xml"]["path"]
        original = page_path.read_text()
        page_path.write_text(original + "\n")
        with self.assertRaisesRegex(util.Paused, "changed design reference"):
            manifest.context({"design_manifest": record})
        self.assertEqual(original, (path.parent / body["files"][0]["pages"][0]["metadata_xml"]["path"]).read_text())

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
            if change == "viewport":
                case["viewport"]["width"] = 3
            if change == "scale":
                case["export_scale"] = 2
            if change == "hash":
                case["artifacts"]["screenshot"]["sha256"] = "0" * 64
            if change == "missing":
                case["artifacts"]["screenshot"]["path"] = "missing.png"
            if change == "escape":
                case["artifacts"]["screenshot"]["path"] = "../screen.png"
            if change == "absolute":
                case["implementation_paths"] = ["/tmp/code.py"]
            if change == "empty_state":
                case["state"] = "  "
            if change == "nan":
                case["export_scale"] = float("nan")
            if change == "bool":
                case["viewport"]["device_scale_factor"] = True
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
        if not (self.workspace / ".git").exists():
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
        (self.workspace / "greet.py").write_text('print("fixture")\n')
        comparison = self.workspace / ".autocode" / "comparison.txt"
        comparison.write_text("Offline fixture comparison; not genuine browser acceptance")
        validation = {
            "design_manifest_hash": retained["manifest_hash"],
            "design_results": [
                {
                    "id": case["id"],
                    "status": "PASS",
                    "criterion_ids": ["C1"],
                    **make_capture(self.workspace, retained["manifest_hash"], case),
                    "comparison_ref": str(comparison),
                }
                for case in self.body["cases"]
            ],
            "verdict": "PASS",
            "criteria_revision": "criteria",
            "source_revision": util.snapshot(self.workspace)["revision"],
            "checks": [{"exit_code": 0}],
            "findings": [],
            "unverified_criteria": [],
            "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": [str(comparison)]}],
            "reviewer_role": "sol",
        }
        criteria = [{"id": "C1", "criterion": "Fixture behavior", "status": "verified", "evidence": "comparison"}]
        state = {
            "workspace": str(self.workspace),
            "version": 2,
            "settings": {"design_manifest": retained},
            "criteria_revision": "criteria",
            "validation": validation,
            "acceptance_criteria": criteria,
        }
        validation["evidence_hashes"] = {ref: util.file_hash(ref) for ref in coverage.report_refs(state, validation)}
        return state, {"status": "TASK_COMPLETE", "acceptance_criteria": criteria}, util.snapshot(self.workspace)

    def test_whole_completion_requires_every_design_case_on_current_source(self):
        state, decision, current = self.passing_state()
        self.assertTrue(completion.completion_ready(state, decision, current))
        for change in (
            "omitted",
            "unverified",
            "duplicate",
            "stale_manifest",
            "unknown_criterion",
            "failed_criterion",
            "no_pin",
            "stale_source",
        ):
            changed = copy.deepcopy(state)
            validation = changed["validation"]
            row = validation["design_results"][-1]
            if change == "omitted":
                validation["design_results"].pop()
            if change == "unverified":
                row["status"] = "NOT_VERIFIED"
            if change == "duplicate":
                row["id"] = validation["design_results"][0]["id"]
            if change == "stale_manifest":
                validation["design_manifest_hash"] = "other"
            if change == "unknown_criterion":
                row["criterion_ids"] = ["UNKNOWN"]
            if change == "failed_criterion":
                validation["criterion_results"][0]["status"] = "FAIL"
            if change == "no_pin":
                validation["evidence_hashes"] = {}
            if change == "stale_source":
                validation["source_revision"] = "old-source"
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
        candidate = Path(state["validation"]["design_results"][0]["candidate_ref"])
        self.assertEqual((8, 4), manifest.png_dimensions(candidate))
        # A candidate rendered at the reference's smaller export dimensions is wrong.
        png(candidate, width=2, height=1)
        self.assertFalse(completion.completion_ready(state, decision, current))

    def test_reference_png_wrong_viewport_and_changed_capture_cannot_count_as_proof(self):
        state, decision, current = self.passing_state()
        row = state["validation"]["design_results"][0]
        original = row["candidate_ref"]
        row["candidate_ref"] = str(Path(state["settings"]["design_manifest"]["root"]) / "screen.png")
        self.assertFalse(completion.completion_ready(state, decision, current))
        row["candidate_ref"] = original
        png(Path(original), width=1)
        self.assertFalse(completion.completion_ready(state, decision, current))
        viewport = self.body["cases"][0]["viewport"]
        png(
            Path(original),
            round(viewport["width"] * viewport["device_scale_factor"]),
            round(viewport["height"] * viewport["device_scale_factor"]),
        )
        Path(row["comparison_ref"]).write_text("changed after validation")
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
            reports.review_validation_schema(
                bound, state, {"stage": "sol_report_repair", "original_stage": "sol"}, broken
            )
        checkpoint = {"type": "object", "required": ["validation"], "properties": {"validation": schema}}
        self.assertIn(
            "design_results",
            coverage.extend_schema(checkpoint, state, "astra_checkpoint")["properties"]["validation"]["required"],
        )

    def test_builder_self_check_without_design_fields_is_not_a_mismatched_manifest(self):
        # apply_review_result used to call report_refs for every non-Builder stage,
        # including the final-audit builder self_check. That report is self-evidence
        # for criteria; it does not carry design_results. Independent sol reports
        # still have to account for every case (covered above).
        import autopilot

        state, decision, current = self.passing_state()
        self_check = {
            "verdict": "NOT_VERIFIED",
            "checks": [],
            "findings": [],
            "unverified_criteria": ["C1"],
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
        record = {
            "events": str(self.workspace / "events.jsonl"),
            "source_revision": "source-a",
            "role": "terra",
            "stage": "self_check",
            "output": str(self.workspace / "self.json"),
        }
        (self.workspace / "events.jsonl").write_text("")
        autopilot.apply_review_result(
            Runtime(), state, "self_check", self_check, record, self.workspace, self.workspace
        )
        self.assertEqual("NOT_VERIFIED", state["validation"]["verdict"])

        # Design gaps refuse independent completion, but the final-audit self-check
        # probe (require_independent=False) must not demand design_results.
        probe, decision, current = self.passing_state()
        probe["validation"]["design_results"][-1]["status"] = "NOT_VERIFIED"
        self.assertFalse(coverage.ready(probe))
        self.assertFalse(completion.completion_ready(probe, decision, current))
        self.assertTrue(completion.completion_ready(probe, decision, current, require_independent=False))

    def test_status_retains_inventory_error_when_original_receipt_is_missing(self):
        path, body = inventory_bundle(self.root / "status-inventory")
        selected = manifest.retain(manifest.load(path), self.workspace)
        receipt = Path(selected["root"]) / body["files"][0]["pages"][0]["source_json"]["path"]
        receipt.unlink()
        view = run_view.view({"status": "PAUSED_DESIGN_REFERENCE", "settings": {"design_manifest": selected}})
        self.assertIn("inventory_error", view["design"])
        self.assertEqual([case["id"] for case in body["cases"]], view["design"]["not_passing"])
        self.assertIsNone(view["design"]["current_visual_acceptance"])
        self.assertFalse(coverage.ready({"settings": {"design_manifest": selected}}))

    def test_status_builds_one_fresh_authenticated_inventory_per_projection(self):
        path, body = inventory_bundle(self.root / "status-single-inventory")
        record = manifest.load(path)
        state = {"status": "RUNNING", "settings": {"design_manifest": record}}
        unchanged = copy.deepcopy(state)
        with (
            patch.object(design_inventory, "catalog", wraps=design_inventory.catalog) as catalog,
            patch.object(design_inventory, "blockers", wraps=design_inventory.blockers) as blockers,
        ):
            for _ in range(2):
                catalog.reset_mock()
                blockers.reset_mock()
                design = run_view.view(state)["design"]
                self.assertEqual(1, catalog.call_count)
                self.assertEqual(1, blockers.call_count)
                self.assertEqual([case["id"] for case in body["cases"]], design["case_ids"])
                self.assertEqual(design["case_ids"], design["not_passing"])
                self.assertIsNone(design["current_visual_acceptance"])
        self.assertEqual(unchanged, state)

    def test_status_reauthenticates_changed_reference_on_the_next_projection(self):
        path, body = inventory_bundle(self.root / "status-fresh-reference")
        record = manifest.load(path)
        state = {"status": "RUNNING", "settings": {"design_manifest": record}}
        self.assertNotIn("inventory_error", run_view.view(state)["design"])
        artifact = path.parent / body["files"][0]["pages"][0]["source_json"]["path"]
        artifact.write_bytes(artifact.read_bytes() + b"\n")
        changed = run_view.view(state)["design"]
        self.assertIn("inventory_error", changed)
        self.assertEqual(changed["case_ids"], changed["not_passing"])
        self.assertIsNone(changed["current_visual_acceptance"])

    def test_status_retains_missing_resource_blockers_with_shared_inventory(self):
        path, _ = inventory_bundle(self.root / "status-missing-resources", include_missing_reference=True)
        state = {"status": "RUNNING", "settings": {"design_manifest": manifest.load(path)}}
        design = run_view.view(state)["design"]
        self.assertEqual(2, len(design["inventory_blockers"]))
        self.assertEqual(design["case_ids"], design["not_passing"])
        self.assertIsNone(design["current_visual_acceptance"])
        self.assertFalse(coverage.ready(state))
        self.assertIn("unavailable required fonts/assets", completion.rejection(state))

    def test_status_exposes_inventory_without_claiming_current_visual_acceptance(self):
        state, _, _ = self.passing_state()
        state["validation"]["design_results"][-1]["status"] = "NOT_VERIFIED"
        design = run_view.view(state)["design"]
        self.assertEqual(["greet.filled"], design["not_passing"])
        self.assertIsNone(design["current_visual_acceptance"])
        self.assertNotIn("design", run_view.view({"status": "RUNNING"}))

    def retained_results_state(self):
        state, _, _ = self.passing_state()
        previous = copy.deepcopy(state["validation"])
        old = state["settings"]["design_manifest"]
        updated = copy.deepcopy(self.body)
        updated["cases"].append({**copy.deepcopy(updated["cases"][0]), "id": "greet.disabled", "state": "disabled"})
        original = self.path.read_bytes()
        try:
            self.path.write_text(json.dumps(updated))
            current = manifest.retain(manifest.load(self.path), self.workspace)
        finally:
            self.path.write_bytes(original)
        self.assertNotEqual(old["manifest_hash"], current["manifest_hash"])
        state["settings"].update(design_manifest=current, design_manifest_history=[old])
        state["design_input_changes"] = [
            {
                "previous_hash": old["manifest_hash"],
                "current_hash": current["manifest_hash"],
                "previous_validation": previous,
            }
        ]
        state["validation"] = {}
        return state, previous

    def test_status_checks_retained_captures_once_per_projection(self):
        state, previous = self.retained_results_state()
        before = copy.deepcopy(state)
        expected = previous["design_results"]
        with (
            patch.object(coverage.visual, "verify", wraps=coverage.visual.verify) as verify,
            patch.object(coverage.source_scope, "snapshot", wraps=coverage.source_scope.snapshot) as snapshot,
        ):
            for _ in range(2):
                verify.reset_mock()
                snapshot.reset_mock()
                design = run_view.view(state)["design"]
                self.assertEqual(len(expected), verify.call_count)
                self.assertEqual(1, snapshot.call_count)
                self.assertEqual(expected, design["reusable_case_results"])
                self.assertEqual(["greet.disabled"], design["not_passing"])
                self.assertIsNone(design["current_visual_acceptance"])
        self.assertEqual(before, state)
        self.assertFalse(coverage.ready(state))

    def test_status_rechecks_retained_source_and_evidence_on_next_projection(self):
        for change in ("source", "capture", "comparison"):
            with self.subTest(change=change):
                state, previous = self.retained_results_state()
                first = run_view.view(state)["design"]
                self.assertEqual(2, len(first["reusable_case_results"]))
                row = previous["design_results"][0]
                path = {
                    "source": self.workspace / "greet.py",
                    "capture": self.workspace / row["capture_ref"],
                    "comparison": Path(row["comparison_ref"]),
                }[change]
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"\nchanged after status\n")
                    second = run_view.view(state)["design"]
                    kept = [] if change in ("source", "comparison") else previous["design_results"][1:]
                    self.assertEqual(kept, second["reusable_case_results"])
                    self.assertEqual(
                        [
                            case["id"]
                            for case in state["settings"]["design_manifest"]["body"]["cases"]
                            if case["id"] not in {r["id"] for r in kept}
                        ],
                        second["not_passing"],
                    )
                    self.assertIsNone(second["current_visual_acceptance"])
                    self.assertFalse(coverage.ready(state))
                finally:
                    path.write_bytes(original)


class PlanningPartitionContextTests(unittest.TestCase):
    """Complete raw input is retained once, without changing native authority."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="planning-partition-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path, self.body = self.scoped_bundle(self.root / "exports")
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.authority = self.root / "approved-scope.json"
        self.write_authority()
        self.record = manifest.retain(manifest.load(self.path, **self.arguments()), self.workspace, **self.arguments())
        # Preserve coverage of already-saved v1 bundles after fresh retention
        # moves to v2. This fixture alone creates the historical edition.
        old_root = self.workspace / ".autocode" / "design-inputs-planning-v1" / self.record["manifest_hash"]
        shutil.copytree(self.record["root"], old_root)
        (old_root / "planning-catalog.json").unlink()
        (old_root / "inventory-manifest.json").chmod(0o644)
        self.record = {**self.record, "root": str(old_root), "manifest_path": str(old_root / "inventory-manifest.json")}
        self.settings = {
            "design_manifest": self.record,
            "engine": "opencode",
            "provider": "opencode",
            "roles": {
                role: {"engine": "opencode", "provider": "opencode", "reasoning_effort": "high"}
                for role in ("requirements", "glm", "astra", "plan_reviewer")
            },
        }
        self.sidecar = Path(self.record["root"]) / "planning-scope-partition.json"

    @staticmethod
    def scoped_bundle(root):
        path, body = inventory_bundle(root)
        # The legacy full-delivery fixture lists every declared resource on
        # every case. Scoped admission requires the actual source-node use.
        for case in body["cases"]:
            refs = {section: [] for section in ("components", "variables", "fonts", "assets", "transitions")}
            if case["node_id"] == "1:2":
                refs["transitions"] = ["home-details"]
            elif case["node_id"] == "3:4":
                refs["assets"] = ["logo-FILEB"]
            case["inventory_refs"] = refs
        path.write_text(json.dumps(body))
        return path, body

    def write_authority(self):
        self.authority.write_text(
            json.dumps(
                {
                    "version": 1,
                    "manifest_hash": util.digest(self.body),
                    "approved_case_ids": [row["id"] for row in self.body["cases"]],
                    "approval_evidence": [{"kind": "approved_scope_decision", "sha256": "a" * 64}],
                }
            )
        )

    def arguments(self):
        return {"approved_scope": self.authority, "approved_scope_sha256": util.file_hash(self.authority)}

    def test_exact_partition_reconstructs_full_context_and_keeps_public_catalog(self):
        original = manifest.context(self.settings)
        self.assertEqual("design-inputs-planning-v1", Path(self.record["root"]).parent.name)
        self.assertEqual(0o400, self.sidecar.stat().st_mode & 0o777)
        for role in self.settings["roles"]:
            with self.subTest(role=role):
                compact = manifest.planning_context(self.settings, role=role)
                locator = compact["catalog"]["scope_partition"]
                raw = Path(locator["path"]).read_bytes()
                self.assertEqual(util.file_hash(self.sidecar), locator["sha256"])
                self.assertEqual(len(raw), locator["bytes"])
                self.assertEqual(self.record["manifest_hash"], locator["manifest_hash"])
                self.assertEqual(self.record["scope_admission"]["sha256"], locator["scope_sha256"])
                compact["catalog"]["scope_partition"] = json.loads(raw)
                self.assertEqual(original, compact)
        self.assertEqual(self.body, original["body"])
        self.assertEqual(3, len(original["body"]["cases"]))
        self.assertEqual(
            original["catalog"],
            design_inventory.catalog(self.body, self.record["root"], admission=manifest.admitted_scope(self.record)),
        )
        self.assertEqual(original, manifest.context(self.settings))

    def test_only_saved_opencode_planning_routes_receive_locator(self):
        original = manifest.context(self.settings)
        for change in ("kilo", "unknown-provider", "other-engine", "unsaved-role", "completion"):
            selected = copy.deepcopy(self.settings)
            role = "plan_reviewer"
            if change == "kilo":
                selected["roles"][role]["provider"] = "kilocode"
            elif change == "unknown-provider":
                selected["roles"][role]["provider"] = "unknown"
            elif change == "other-engine":
                selected["roles"][role]["engine"] = "codex"
            elif change == "unsaved-role":
                del selected["roles"][role]
            else:
                role = "completion"
                selected["roles"][role] = {"engine": "opencode", "provider": "opencode"}
            with self.subTest(change=change):
                self.assertEqual(original, manifest.planning_context(selected, role=role))

    def test_old_bundle_falls_back_inline_and_is_never_upgraded(self):
        old_root = self.workspace / ".autocode" / "design-inputs" / self.record["manifest_hash"]
        shutil.copytree(self.record["root"], old_root)
        (old_root / "planning-scope-partition.json").unlink()
        old = {
            **copy.deepcopy(self.record),
            "root": str(old_root),
            "manifest_path": str(old_root / "inventory-manifest.json"),
        }
        settings = {**self.settings, "design_manifest": old}
        before = {
            p.relative_to(old_root).as_posix(): (p.read_bytes(), p.stat().st_mode)
            for p in old_root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(manifest.context(settings), manifest.planning_context(settings, role="plan_reviewer"))
        newly_retained = manifest.retain(old, self.workspace)
        self.assertEqual(self.record["body"], newly_retained["body"])
        self.assertEqual(self.record["scope_admission"], newly_retained["scope_admission"])
        self.assertEqual("design-inputs-planning-v2", Path(newly_retained["root"]).parent.name)
        after = {
            p.relative_to(old_root).as_posix(): (p.read_bytes(), p.stat().st_mode)
            for p in old_root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)
        self.assertFalse((old_root / "planning-scope-partition.json").exists())

    def test_sidecar_missing_tampered_extra_or_noncanonical_bytes_refuse(self):
        original = self.sidecar.read_bytes()
        changed = json.loads(original)
        changed["foreign"] = True
        for raw in (b"{}", json.dumps(changed).encode(), original + b"\n"):
            self.sidecar.chmod(0o600)
            self.sidecar.write_bytes(raw)
            self.sidecar.chmod(0o400)
            with self.subTest(raw=raw[:40]), self.assertRaisesRegex(util.Paused, "partition.*changed"):
                manifest.planning_context(self.settings, role="plan_reviewer")
        self.sidecar.unlink()
        with self.assertRaises(util.Paused):
            manifest.planning_context(self.settings, role="plan_reviewer")
        with self.assertRaises(OSError):
            manifest.retain(self.record, self.workspace)
        self.assertFalse(self.sidecar.exists(), "An incomplete new bundle is never repaired silently")

    def test_sidecar_symlink_nonregular_and_writable_mode_refuse(self):
        original = self.sidecar.read_bytes()
        outside = self.root / "foreign-partition.json"
        outside.write_bytes(original)
        self.sidecar.unlink()
        self.sidecar.symlink_to(outside)
        with self.assertRaisesRegex(util.Paused, "symlinks"):
            manifest.planning_context(self.settings, role="plan_reviewer")
        self.sidecar.unlink()
        self.sidecar.mkdir()
        with self.assertRaisesRegex(util.Paused, "regular file"):
            manifest.planning_context(self.settings, role="plan_reviewer")
        self.sidecar.rmdir()
        self.sidecar.write_bytes(original)
        with self.assertRaisesRegex(util.Paused, "immutable regular file"):
            manifest.planning_context(self.settings, role="plan_reviewer")

    def test_open_substitution_and_oversize_refuse_before_payload_read(self):
        original = self.sidecar.read_bytes()
        outside = self.root / "foreign-partition.json"
        outside.write_bytes(original)
        real_open, real_fdopen = os.open, os.fdopen
        testcase = self

        class NoPayloadRead:
            def __init__(self, descriptor, mode):
                self.stream = real_fdopen(descriptor, mode)

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self, *args):
                testcase.fail("Replaced or oversized input must be refused before payload read")

        for change in ("symlink", "fifo", "oversize"):

            def substitute(path, flags, mode=0o777):
                self.assertEqual(self.sidecar, Path(path))
                self.sidecar.unlink()
                if change == "symlink":
                    self.sidecar.symlink_to(outside)
                elif change == "fifo":
                    os.mkfifo(self.sidecar, 0o400)
                else:
                    self.sidecar.write_bytes(original + b" ")
                    self.sidecar.chmod(0o400)
                return real_open(path, flags, mode)

            with self.subTest(change=change), patch.object(manifest.os, "open", side_effect=substitute) as opened:
                with patch.object(manifest.os, "fdopen", NoPayloadRead), self.assertRaises(util.Paused):
                    manifest.planning_context(self.settings, role="plan_reviewer")
                self.assertEqual(1, opened.call_count)
            self.sidecar.unlink()
            self.sidecar.write_bytes(original)
            self.sidecar.chmod(0o400)
        self.sidecar.chmod(0o600)
        self.sidecar.write_bytes(original + b" ")
        self.sidecar.chmod(0o400)
        with patch.object(manifest.os, "open", side_effect=AssertionError("Oversized input cannot be opened")):
            with self.assertRaisesRegex(util.Paused, "partition.*changed"):
                manifest.planning_context(self.settings, role="plan_reviewer")

    def test_source_artifacts_cannot_collide_with_sidecar_or_its_ancestor(self):
        for name in ("planning-scope-partition.json", "planning-scope-partition.json/context.txt"):
            source_root = self.root / ("collision-" + str(len(name)))
            path, body = self.scoped_bundle(source_root)
            target = source_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / "context.txt", target)
            for case in body["cases"]:
                case["artifacts"]["design_context"] = {"path": name, "sha256": util.file_hash(target)}
            path.write_text(json.dumps(body))
            self.body = body
            self.write_authority()
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "reserved"):
                manifest.retain(manifest.load(path, **self.arguments()), self.workspace, **self.arguments())

    def test_malformed_retained_namespace_and_changed_scope_are_refused(self):
        for change in ("index-escape", "missing-scope", "wrong-scope"):
            selected = copy.deepcopy(self.record)
            if change == "index-escape":
                selected["manifest_path"] = str(self.root / "foreign-index.json")
            elif change == "missing-scope":
                del selected["scope_admission"]
            else:
                selected["scope_admission"]["sha256"] = "b" * 64
            with self.subTest(change=change), self.assertRaises((ValueError, OSError)):
                manifest.verify(selected)

    def test_planning_prompt_round_trip_preserves_task_conversation_and_other_fields(self):
        from units import autoplanner

        state = {
            "task": "Keep the literal `python3 test_app.py` and every approved feature.",
            "workspace": str(self.workspace),
            "settings": {**self.settings, "joint_planning": True},
            "planning": {},
            "answers": {"Q1": "Already approved"},
            "brief_feedback": [{"say": "Preserve all history"}],
            "user_events": [{"kind": "user_message", "say": "Use GLM and MiMo as before"}],
        }
        path = self.workspace / ".autocode" / "runs" / "fixture" / "unused-state-path.json"
        with patch.object(manifest, "planning_context", side_effect=lambda settings, role: manifest.context(settings)):
            original, _ = autoplanner.context(state, "astra_challenge", path)
        compact, _ = autoplanner.context(state, "astra_challenge", path)
        prefix, original_json = original.split('\n{\n  "task":', 1)
        compact_prefix, compact_json = compact.split('\n{\n  "task":', 1)
        self.assertEqual(prefix, compact_prefix)
        original_packet = json.loads('{\n  "task":' + original_json)
        compact_packet = json.loads('{\n  "task":' + compact_json)
        locator = compact_packet["design_manifest"]["catalog"]["scope_partition"]
        compact_packet["design_manifest"]["catalog"]["scope_partition"] = json.loads(Path(locator["path"]).read_bytes())
        self.assertEqual(original_packet, compact_packet)
        # Citation stages keep the full brief once in source_inventory (#1337).
        self.assertEqual("see source_inventory id task:0", compact_packet["task"])
        self.assertEqual(state["task"], compact_packet["source_inventory"][0]["text"])
        self.assertEqual(state["user_events"], compact_packet["user_events"])
        self.assertEqual(state["brief_feedback"], compact_packet["brief_feedback"])
        self.assertEqual(state["answers"], compact_packet["saved_answers"])


class CompletePlanningInputTests(unittest.TestCase):
    """Full planning input retrieval preserves every admitted source field."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="complete-planning-inputs-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path, self.body = PlanningPartitionContextTests.scoped_bundle(self.root / "exports")
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.authority = self.root / "approved-scope.json"
        self.authority.write_text(
            json.dumps(
                {
                    "version": 1,
                    "manifest_hash": util.digest(self.body),
                    "approved_case_ids": [row["id"] for row in self.body["cases"]],
                    "approval_evidence": [{"kind": "approved_scope_decision", "sha256": "a" * 64}],
                }
            )
        )
        arguments = {"approved_scope": self.authority, "approved_scope_sha256": util.file_hash(self.authority)}
        self.record = manifest.retain(manifest.load(self.path, **arguments), self.workspace, **arguments)
        self.settings = {
            "design_manifest": self.record,
            "engine": "opencode",
            "provider": "opencode",
            "roles": {
                role: {"engine": "opencode", "provider": "opencode"}
                for role in ("requirements", "glm", "astra", "plan_reviewer")
            },
        }

    def test_fresh_complete_planning_inputs_reconstruct_full_context(self):
        original = manifest.context(self.settings)
        self.assertEqual("design-inputs-planning-v2", Path(self.record["root"]).parent.name)
        for role in self.settings["roles"]:
            with self.subTest(role=role):
                projected = manifest.planning_context(self.settings, role=role)
                for field in ("body", "catalog"):
                    locator = projected[field]
                    path = Path(locator["path"])
                    self.assertEqual(Path(self.record["root"]), path.parent)
                    self.assertEqual(0o400, path.stat().st_mode & 0o777)
                    self.assertEqual(util.file_hash(path), locator["sha256"])
                    self.assertEqual(path.stat().st_size, locator["bytes"])
                    self.assertEqual(self.record["manifest_hash"], locator["manifest_hash"])
                    self.assertEqual(self.record["scope_admission"]["sha256"], locator["scope_sha256"])
                    projected[field] = json.loads(path.read_bytes())
                # The inline roster is derived from the referenced body; every
                # other field must reconstruct exactly.
                roster = [{key: case[key] for key in ("id", "route", "state")} for case in projected["body"]["cases"]]
                self.assertEqual(roster, projected.pop("case_roster"))
                self.assertEqual(original, projected)
        self.assertEqual(
            original["catalog"],
            design_inventory.catalog(self.body, self.record["root"], admission=manifest.admitted_scope(self.record)),
        )

    def test_referenced_planning_inputs_keep_complete_inline_case_roster(self):
        locator_fields = {"path", "sha256", "bytes", "manifest_hash", "scope_sha256", "retrieval"}
        expected = [{key: case[key] for key in ("id", "route", "state")} for case in self.body["cases"]]
        case_ids = [case["id"] for case in self.body["cases"]]
        self.assertEqual(3, len(set(case_ids)))
        for role in self.settings["roles"]:
            with self.subTest(role=role):
                projected = manifest.planning_context(self.settings, role=role)
                self.assertEqual(locator_fields, set(projected["body"]))
                self.assertEqual(locator_fields, set(projected["catalog"]))
                self.assertEqual(expected, projected.get("case_roster"))
                referenced = json.loads(Path(projected["body"]["path"]).read_bytes())
                self.assertEqual(case_ids, [row["id"] for row in referenced["cases"]])
                handoff = json.dumps(projected)
                for case_id in case_ids:
                    self.assertEqual(1, handoff.count(json.dumps(case_id)), case_id)

    def test_only_original_saved_readers_receive_complete_locators(self):
        full = manifest.context(self.settings)
        for change in ("kilocode", "unknown", "other_engine", "missing_role", "completion"):
            settings = copy.deepcopy(self.settings)
            role = "plan_reviewer"
            if change in ("kilocode", "unknown"):
                settings["roles"][role]["provider"] = change
            elif change == "other_engine":
                settings["roles"][role]["engine"] = "codex"
            elif change == "missing_role":
                settings["roles"].pop(role)
            else:
                role = "completion"
                settings["roles"][role] = {"engine": "opencode", "provider": "opencode"}
            with self.subTest(change=change):
                self.assertEqual(full, manifest.planning_context(settings, role=role))
                self.assertEqual({}, manifest.planning_input_pins(settings, role=role))

    def test_index_and_catalog_tampering_never_gets_filled_or_adopted(self):
        for leaf in ("inventory-manifest.json", "planning-catalog.json"):
            path = Path(self.record["root"]) / leaf
            original = path.read_bytes()
            for changed in (original + b" ", b"0" * len(original)):
                path.chmod(0o600)
                path.write_bytes(changed)
                path.chmod(0o400)
                with self.subTest(leaf=leaf, changed_size=len(changed)), self.assertRaises(util.Paused):
                    manifest.planning_context(self.settings, role="plan_reviewer")
                self.assertEqual(changed, path.read_bytes())
            path.unlink()
            with self.assertRaises((ValueError, OSError)):
                manifest.retain(self.record, self.workspace)
            self.assertFalse(path.exists())
            path.write_bytes(original)
            path.chmod(0o400)

    def test_unsafe_file_type_mode_and_alias_fail_before_payload_read(self):
        for leaf in ("inventory-manifest.json", "planning-catalog.json"):
            path = Path(self.record["root"]) / leaf
            original = path.read_bytes()
            foreign = self.root / (leaf + ".foreign")
            foreign.write_bytes(original)
            for change in ("symlink", "fifo", "writable", "hardlink", "oversize"):
                path.unlink()
                if change == "symlink":
                    path.symlink_to(foreign)
                elif change == "fifo":
                    os.mkfifo(path, 0o400)
                elif change == "hardlink":
                    os.link(foreign, path)
                    path.chmod(0o400)
                else:
                    path.write_bytes(original + (b" " if change == "oversize" else b""))
                    path.chmod(0o400 if change == "oversize" else 0o600)
                real_open = os.open

                def bounded_open(name, flags, mode=0o777):
                    if Path(name) == path:
                        self.fail("Invalid type/mode/length/alias must fail before opening payload")
                    return real_open(name, flags, mode)

                with self.subTest(leaf=leaf, change=change), patch.object(manifest.os, "open", bounded_open):
                    with self.assertRaises(util.Paused):
                        manifest.planning_context(self.settings, role="plan_reviewer")
                path.unlink()
                path.write_bytes(original)
                path.chmod(0o400)

    def test_substitution_between_stat_and_open_is_not_read(self):
        path = Path(self.record["root"]) / "planning-catalog.json"
        original = path.read_bytes()
        real_open, real_fdopen = os.open, os.fdopen
        target_fds = set()
        testcase = self

        class NoPayloadRead:
            def __init__(self, fd, mode):
                self.stream = real_fdopen(fd, mode)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self, *args):
                testcase.fail("Replaced input must fail descriptor checks before payload read")

        for change in ("fifo", "same_bytes_new_inode", "oversize"):

            def substituted_open(name, flags, mode=0o777):
                if Path(name) == path:
                    path.unlink()
                    if change == "fifo":
                        os.mkfifo(path, 0o400)
                    else:
                        path.write_bytes(original + (b" " if change == "oversize" else b""))
                        path.chmod(0o400)
                    fd = real_open(name, flags, mode)
                    target_fds.add(fd)
                    return fd
                return real_open(name, flags, mode)

            def checked_fdopen(fd, mode):
                return NoPayloadRead(fd, mode) if fd in target_fds else real_fdopen(fd, mode)

            with (
                self.subTest(change=change),
                patch.object(manifest.os, "open", substituted_open),
                patch.object(manifest.os, "fdopen", checked_fdopen),
                self.assertRaises(util.Paused),
            ):
                manifest.planning_context(self.settings, role="plan_reviewer")
            target_fds.clear()
            path.unlink()
            path.write_bytes(original)
            path.chmod(0o400)

    def test_failed_staging_and_reserved_artifact_leave_no_bundle(self):
        workspace = self.root / "second-project"
        workspace.mkdir()
        real_check = manifest._immutable_planning_input

        def failed_check(path, expected):
            if Path(path).is_relative_to(workspace) and Path(path).name == "planning-catalog.json":
                raise ValueError("controlled staging verification failure")
            return real_check(path, expected)

        with (
            patch.object(manifest, "_immutable_planning_input", failed_check),
            self.assertRaisesRegex(ValueError, "controlled staging"),
        ):
            manifest.retain(self.record, workspace)
        parent = workspace / ".autocode" / "design-inputs-planning-v2"
        self.assertEqual([], list(parent.iterdir()))
        body = copy.deepcopy(self.body)
        screenshot = body["cases"][0]["artifacts"]["screenshot"]
        shutil.copyfile(self.path.parent / screenshot["path"], self.path.parent / "planning-catalog.json")
        screenshot["path"] = "planning-catalog.json"
        self.path.write_text(json.dumps(body))
        authority = json.loads(self.authority.read_text())
        authority["manifest_hash"] = util.digest(body)
        self.authority.write_text(json.dumps(authority))
        arguments = {"approved_scope": self.authority, "approved_scope_sha256": util.file_hash(self.authority)}
        source = manifest.load(self.path, **arguments)
        with self.assertRaisesRegex(ValueError, "reserved"):
            manifest.retain(source, workspace, **arguments)
        self.assertEqual([], list(parent.iterdir()))

    def test_atomic_publication_collision_cannot_overwrite_or_adopt_existing_bundle(self):
        workspace = self.root / "collision-project"
        workspace.mkdir()
        destination = workspace / ".autocode" / "design-inputs-planning-v2" / self.record["manifest_hash"]
        original_rename = Path.rename

        def collision(path, target):
            self.assertEqual(destination, Path(target))
            destination.mkdir()
            (destination / "foreign-input").write_bytes(b"collision sentinel")
            return original_rename(path, target)

        with patch.object(Path, "rename", collision), self.assertRaises(OSError):
            manifest.retain(self.record, workspace)
        self.assertEqual(["foreign-input"], [path.name for path in destination.iterdir()])
        self.assertEqual(b"collision sentinel", (destination / "foreign-input").read_bytes())
        self.assertEqual([destination], list(destination.parent.iterdir()))
        with self.assertRaises((ValueError, OSError)):
            manifest.retain(self.record, workspace)
        self.assertEqual(b"collision sentinel", (destination / "foreign-input").read_bytes())

    def test_existing_planning_composer_preserves_all_other_literal_inputs(self):
        from units import autoplanner

        state = {
            "task": "Keep every literal `python3 test_app.py` and all input bytes.",
            "workspace": str(self.workspace),
            "settings": {**self.settings, "joint_planning": True},
            "planning": {},
            "answers": {"Q1": "Preserved"},
            "brief_feedback": [{"say": "Preserve the original context"}],
            "user_events": [],
        }
        state_path = self.workspace / ".autocode" / "runs" / "fixture" / "unused-state-path.json"
        with patch.object(manifest, "planning_context", side_effect=lambda settings, role: manifest.context(settings)):
            full, _ = autoplanner.context(state, "astra_challenge", state_path)
        compact, _ = autoplanner.context(state, "astra_challenge", state_path)
        prefix, data = full.split('\n{\n  "task":', 1)
        actual_prefix, actual_data = compact.split('\n{\n  "task":', 1)
        self.assertEqual(prefix, actual_prefix)
        expected = json.loads('{\n  "task":' + data)
        actual = json.loads('{\n  "task":' + actual_data)
        for field in ("body", "catalog"):
            actual["design_manifest"][field] = json.loads(Path(actual["design_manifest"][field]["path"]).read_bytes())
        cases = actual["design_manifest"]["body"]["cases"]
        roster = [{key: case[key] for key in ("id", "route", "state")} for case in cases]
        self.assertEqual(roster, actual["design_manifest"].pop("case_roster"))
        self.assertEqual(expected, actual)

    @unittest.skipUnless(
        sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(), "requires real macOS Seatbelt enforcement"
    )
    def test_existing_contained_bash_reads_complete_inputs_but_cannot_write(self):
        import autocode_tool_containment as containment

        full = manifest.context(self.settings)
        projected = manifest.planning_context(self.settings, role="plan_reviewer")
        paths = [Path(projected[field]["path"]) for field in ("body", "catalog")]
        before = {str(path): (path.read_bytes(), path.stat().st_mode & 0o777) for path in paths}
        boundary = containment.prepare(self.workspace, environment={"PATH": "/usr/bin:/bin"})
        command = shlex.join(["/bin/cat", *(str(path) for path in paths)])
        result = subprocess.run([boundary["shell"], "-c", command], cwd=self.workspace, capture_output=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(b"".join(before[str(path)][0] for path in paths), result.stdout)
        for field, path in zip(("body", "catalog"), paths, strict=False):
            self.assertEqual(full[field], json.loads(before[str(path)][0]))
        denied = subprocess.run(
            [boundary["shell"], "-c", "printf changed > " + shlex.quote(str(paths[1]))],
            cwd=self.workspace,
            capture_output=True,
            timeout=20,
        )
        self.assertNotEqual(0, denied.returncode)
        self.assertRegex(denied.stderr.decode(), "Operation not permitted|Permission denied")
        self.assertEqual(before, {str(path): (path.read_bytes(), path.stat().st_mode & 0o777) for path in paths})


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

        (bindir / "codex").write_text((ROOT / "tools/live_fixture_provider.py").read_text())
        install_inventory_hook(bindir / "codex")
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "FAKE_DESIGN_PROMPTS": str(self.root / "prompts.jsonl"),
            "FAKE_CAPTURE_REPO": str(ROOT),
        }

    def start(self):
        return taskrun.TaskRun.start(
            self.workspace,
            BRIEF,
            options=OPTIONS,
            start_options=("--figma-manifest", str(self.path)),
            env=self.env,
            timeout=120,
        )

    def scope_inventory_to_fixture_task(self):
        for case in self.body["cases"]:
            case["implementation_paths"] = ["greet.py"]
        self.path.write_text(json.dumps(self.body))

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

    def test_public_taskrun_uses_hash_bound_v2_inventory_for_all_files_and_variants(self):
        self.path, self.body = inventory_bundle(self.root / "exports-v2")
        self.scope_inventory_to_fixture_task()
        run = self.start()
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertEqual(3, len(view["design"]["inventory"]["screen_frames"]))
        self.assertEqual(3, len(view["design"]["inventory"]["components"][0]["variants"]))
        self.assertEqual(3, len(view["design"]["plan_coverage"]["cases"]))
        run.approve_plan(view["needs"]["token"])
        view = run.advance_until_input()
        self.assertTrue(view["done"], view)
        self.assertEqual([], view["design"]["not_passing"])
        prompts = [json.loads(line) for line in (self.root / "prompts.jsonl").read_text().splitlines()]
        self.assertEqual({case["id"] for case in self.body["cases"]}, set(prompts[-1]["ids"]))

    def test_public_taskrun_never_completes_with_unavailable_source_assets(self):
        self.path, self.body = inventory_bundle(self.root / "exports-blocked", include_missing_reference=True)
        self.scope_inventory_to_fixture_task()
        run = self.start()
        view = run.status()
        self.assertEqual(2, len(view["design"]["inventory_blockers"]))
        run.approve_plan(view["needs"]["token"])
        view = run.advance_until_input()
        self.assertFalse(view["done"], view)
        self.assertIn("unavailable required fonts/assets", view["stop_reason"])

    def test_public_taskrun_refuses_plan_that_omits_an_approved_design_case(self):
        self.path, self.body = inventory_bundle(self.root / "exports-uncovered-plan")
        self.scope_inventory_to_fixture_task()
        self.env["FAKE_DESIGN_OMIT_PLAN_CASE"] = "1"
        view = self.start().status()
        self.assertFalse(view["done"])
        self.assertNotEqual("approve_plan", view["needs"]["kind"])
        self.assertIn("every approved design case", view["stop_reason"])

    def test_public_native_intake_collects_both_files_before_plan_approval_and_survives_restart(self):
        self.path, self.body = inventory_bundle(self.root / "native-exports")
        self.scope_inventory_to_fixture_task()
        self.env["FAKE_NATIVE_MANIFEST"] = str(self.path)
        run = taskrun.TaskRun.start(
            self.workspace,
            BRIEF,
            options=OPTIONS,
            start_options=(
                "--figma-file",
                "https://www.figma.com/design/FILEA?node-id=1-2",
                "--figma-additional-file",
                "https://www.figma.com/design/FILEB?node-id=3-4",
            ),
            env=self.env,
            timeout=120,
        )
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertEqual(3, len(view["design"]["case_ids"]))
        run = taskrun.TaskRun(self.workspace, run.run_dir, options=OPTIONS, env=self.env, timeout=120)
        self.assertEqual(view["design"]["plan_coverage"], run.status()["design"]["plan_coverage"])
        prompts = [json.loads(line) for line in (self.root / "prompts.jsonl").read_text().splitlines()]
        self.assertEqual("collect_design", prompts[0]["stage"])
        run.approve_plan(view["needs"]["token"])
        self.assertTrue(run.advance_until_input()["done"])

    def test_public_native_unreadable_page_blocks_before_planning(self):
        self.env["FAKE_NATIVE_BLOCKED"] = "1"
        run = taskrun.TaskRun.start(
            self.workspace,
            BRIEF,
            options=OPTIONS,
            start_options=("--figma-file", "https://www.figma.com/design/FILEA"),
            env=self.env,
            timeout=120,
        )
        view = run.status()
        self.assertFalse(view["done"])
        self.assertEqual("PAUSED_DESIGN_INPUT", view["status"], view)
        self.assertIn("Unreadable approved FILEB", view["stop_reason"])
        self.assertFalse((self.workspace / "greet.py").exists())

    def test_native_reference_revision_updates_approved_urls_and_keeps_original_receipts(self):
        self.path, self.body = inventory_bundle(self.root / "native-original")
        self.scope_inventory_to_fixture_task()
        self.env["FAKE_NATIVE_MANIFEST"] = str(self.path)
        references = (
            "https://www.figma.com/design/FILEA?node-id=1-2",
            "https://www.figma.com/design/FILEB?node-id=3-4",
            "https://www.figma.com/design/FILEA?node-id=1-6",
        )
        run = taskrun.TaskRun.start(
            self.workspace,
            BRIEF,
            options=OPTIONS,
            start_options=(
                "--figma-file",
                references[0],
                "--figma-additional-file",
                references[1],
                "--figma-additional-file",
                references[2],
            ),
            env=self.env,
            timeout=120,
        )
        old = run.status()
        saved = json.loads(run._invoke("status", "--status").stdout)["settings"]
        replacement = self.root / "native-replacement"
        shutil.copytree(self.path.parent, replacement)
        body = copy.deepcopy(self.body)
        body["files"][1]["key"] = "FILEC"
        for case in body["cases"]:
            if case["file_key"] == "FILEB":
                case["file_key"] = "FILEC"
                case["id"] = case["id"].replace("FILEB", "FILEC")
        source = body["files"][1]["pages"][0]["source_json"]
        source_path = replacement / source["path"]
        receipt = json.loads(source_path.read_text())
        receipt["file_key"] = "FILEC"
        source_path.write_text(json.dumps(receipt))
        source["sha256"] = util.file_hash(source_path)
        candidate = replacement / self.path.name
        candidate.write_text(json.dumps(body))
        proposed = run.revise_design(
            candidate, old["design"]["manifest_hash"], "Replace the approved second reference file"
        )
        current = json.loads(run._invoke("status", "--status").stdout)["settings"]
        self.assertEqual(
            [references[0], references[2], "https://www.figma.com/design/FILEC"], current["figma_references"]
        )
        self.assertEqual(saved["roles"], current["roles"])
        self.assertEqual(saved["limits"], current["limits"])
        self.assertEqual(list(references), proposed["design"]["reference_changes"][-1]["previous_references"])
        self.assertEqual("PAUSED_DESIGN_INPUT_CHANGED", proposed["status"])
        self.assertFalse(proposed["done"])
        self.assertTrue(Path(saved["design_manifest"]["manifest_path"]).is_file())
        reviewed = run.resume_paused()
        self.assertEqual("approve_plan", reviewed["needs"]["kind"], reviewed)
        self.assertNotEqual(old["needs"]["token"], reviewed["needs"]["token"])

    def test_public_native_worker_failure_retains_owner_and_requires_exact_retry(self):
        self.env["FAKE_NATIVE_FAILURE"] = "1"
        run = taskrun.TaskRun.start(
            self.workspace,
            BRIEF,
            options=OPTIONS,
            start_options=("--figma-file", "https://www.figma.com/design/FILEA"),
            env=self.env,
            timeout=120,
        )
        view = run.status()
        self.assertEqual("PAUSED_JOB_FAILURE", view["status"], view)
        self.assertEqual("collect_design", view["next_stage"])
        self.assertIn("Design inventory: provider exited 3", view["stop_reason"])
        self.assertEqual("retry_job", view["needs"]["kind"])
        self.assertFalse(view["done"])
        self.assertFalse((self.workspace / "greet.py").exists())
        self.path, self.body = inventory_bundle(self.root / "native-retry")
        self.body["files"] = self.body["files"][:1]
        self.body["cases"] = self.body["cases"][:2]
        self.scope_inventory_to_fixture_task()
        saved = json.loads(run._invoke("status", "--status").stdout)["settings"]
        run.env.pop("FAKE_NATIVE_FAILURE")
        run.env["FAKE_NATIVE_MANIFEST"] = str(self.path)
        recovered = run.retry_job(view["needs"]["job_retry_token"])
        self.assertEqual("approve_plan", recovered["needs"]["kind"], recovered)
        current = json.loads(run._invoke("status", "--status").stdout)["settings"]
        self.assertEqual(saved["roles"], current["roles"])
        self.assertEqual(saved["limits"], current["limits"])

    def test_reference_revision_preserves_old_versions_and_requires_new_plan_review(self):
        self.path, self.body = inventory_bundle(self.root / "old-version")
        self.scope_inventory_to_fixture_task()
        run = self.start()
        old = run.status()
        run.approve_plan(old["needs"]["token"])
        completed = run.advance_until_input()
        self.assertTrue(completed["done"], completed)
        replacement = self.root / "new-version"
        shutil.copytree(self.path.parent, replacement)
        body = copy.deepcopy(self.body)
        (replacement / "changed-context.txt").write_text("Updated source screen constraint")
        body["cases"][-1]["artifacts"]["design_context"] = {
            "path": "changed-context.txt",
            "sha256": util.file_hash(replacement / "changed-context.txt"),
        }
        path = replacement / "manifest-v2.json"
        path.write_text(json.dumps(body))
        original = manifest.load(self.path)
        current = manifest.load(path)
        self.assertEqual([body["cases"][-1]["id"]], design_identity.delta(original, current)["changed"])
        with self.assertRaisesRegex(taskrun.TaskRunError, "exact inspected design hash"):
            run.revise_design(path, "wrong", "Updated one source frame")
        proposed = run.revise_design(path, old["design"]["manifest_hash"], "Updated one source frame")
        self.assertFalse(proposed["done"])
        self.assertEqual("PAUSED_DESIGN_INPUT_CHANGED", proposed["status"])
        self.assertEqual([body["cases"][-1]["id"]], proposed["design"]["not_passing"])
        self.assertEqual(2, len(proposed["design"]["reusable_case_results"]))
        history = proposed["design"]["reference_changes"][-1]
        self.assertEqual(old["design"]["manifest_hash"], history["previous_hash"])
        reattached = taskrun.TaskRun(self.workspace, run.run_dir, options=OPTIONS, env=self.env, timeout=120)
        reviewed = reattached.resume_paused()
        self.assertEqual("approve_plan", reviewed["needs"]["kind"], reviewed)
        self.assertNotEqual(old["needs"]["token"], reviewed["needs"]["token"])
        self.assertIn(body["cases"][-1]["id"], reattached.show_goal())
        self.assertEqual(history, reviewed["design"]["reference_changes"][-1])
        reattached.approve_plan(reviewed["needs"]["token"])
        finished = reattached.advance_until_input()
        self.assertTrue(finished["done"], finished)
        self.assertEqual(current["manifest_hash"], finished["design"]["manifest_hash"])

    def test_reference_revision_never_replaces_an_operational_pause(self):
        # A revision restarts plan review in place of the pause, and resuming it launched the plan
        # stages past a provider rate limit nobody acknowledged (#379, #486; docs/bugs/2026-10-06-operational-pause-authority.md).
        import autocode as runner
        import autocode_resolver_runtime as resolver_runtime
        import autocode_support as support

        self.path, self.body = inventory_bundle(self.root / "paused-version")
        self.scope_inventory_to_fixture_task()
        run = self.start()
        old = run.status()
        run.approve_plan(old["needs"]["token"])
        state_path = run.run_dir / "state.json"
        state = json.loads(state_path.read_text())
        state.update(status="PAUSED_RATE_LIMIT", stop_reason="Provider rate limit fixture stop")
        with patch.dict(os.environ, self.env):
            self.assertTrue(
                resolver_runtime.record_operational_exhaustion(
                    runner, state, run.run_dir, support.Paused(state["status"], state["stop_reason"])
                )
            )
            runner.write_json(state_path, state)
        paused = run.status()
        self.assertEqual("WAITING_FOR_USER", paused["status"], paused)
        prompts = (self.root / "prompts.jsonl").read_text()
        replacement = self.root / "paused-replacement"
        shutil.copytree(self.path.parent, replacement)
        body = copy.deepcopy(self.body)
        (replacement / "changed-context.txt").write_text("Updated source screen constraint")
        body["cases"][-1]["artifacts"]["design_context"] = {
            "path": "changed-context.txt",
            "sha256": util.file_hash(replacement / "changed-context.txt"),
        }
        candidate = replacement / "manifest-v2.json"
        candidate.write_text(json.dumps(body))
        with self.assertRaisesRegex(taskrun.TaskRunError, "does not acknowledge PAUSED_RATE_LIMIT"):
            run.revise_design(candidate, old["design"]["manifest_hash"], "Updated one source frame")
        held = run.resume_paused()
        self.assertEqual(old["design"]["manifest_hash"], held["design"]["manifest_hash"])
        self.assertEqual("WAITING_FOR_USER", held["status"], held)
        self.assertEqual(prompts, (self.root / "prompts.jsonl").read_text(), "no provider may launch past the pause")

    def test_invalid_manifest_is_refused_before_any_provider_call_or_run_allocation(self):
        self.body["cases"].pop()
        self.path.write_text(json.dumps(self.body))
        with self.assertRaises(taskrun.TaskRunError):
            self.start()
        self.assertFalse((self.root / "prompts.jsonl").exists())
        self.assertFalse((self.workspace / ".autocode").exists())

    def test_stale_a_cannot_earn_acceptance_for_b_even_when_fresh_b_is_available(self):
        self.env["FAKE_DESIGN_STALE_CAPTURE"] = "1"
        run = self.start()
        run.approve_plan(run.status()["needs"]["token"])
        view = run.advance_until_input()
        self.assertFalse(view["done"], view)
        self.assertIn("Stale implementation capture", json.dumps(view))


if __name__ == "__main__":
    unittest.main()
