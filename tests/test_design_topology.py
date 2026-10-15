"""Source topology/provenance controls; synthetic data is never fidelity proof."""

import copy
import hashlib
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import autocode_design_inventory as inventory
import autocode_design_manifest as manifest
import autocode_design_topology as topology
from autocode_util import file_hash

from tests.figma_inventory_fixtures import plugin_source_bundle


def receipt():
    def node(ident, kind, name, parent, children=None):
        visual = dict(id=ident, type=kind, name=name, parent_id=parent)
        row = dict(id=ident, type=kind, name=name, visual=visual)
        if kind != "PAGE":
            visual.update(x=1.25, y=-2, width=100, height=20)
            row.update(width=100, height=20)
        if children is not None:
            visual["child_ids"] = children
        return row

    return dict(
        version=1,
        file_key="FILEA",
        page_id="0:1",
        read_only=True,
        complete=True,
        errors=[],
        source_format="figma-plugin-api-properties-v1",
        property_profile="autocode-design-properties-v1",
        api_version="1.0.0",
        nodes=[
            node("0:1", "PAGE", "Main", "0:0", ["1:2", "1:3"]),
            node("1:2", "FRAME", 'Screen "quoted" & <escaped>', "0:1", ["1:5", "I1:2;1:6"]),
            node("1:5", "TEXT", "Label", "1:2"),
            node("I1:2;1:6", "TEXT", "Instance child", "1:2"),
            node("1:3", "COMPONENT_SET", "Button", "0:1", ["1:4"]),
            node("1:4", "COMPONENT", "Type=primary", "1:3", []),
        ],
    )


def encoded(value):
    return json.dumps(value).encode()


class TopologyTests(unittest.TestCase):
    def test_exact_source_binding_preserves_typed_ordered_tree(self):
        raw = encoded(receipt())
        xml = topology.generate(raw)
        self.assertEqual(xml, topology.generate(raw))
        tree = topology.verify(xml, raw, hashlib.sha256(raw).hexdigest())
        self.assertEqual(tree.attrib["metadata_kind"], "collector-derived")
        self.assertEqual([n.attrib["id"] for n in tree], ["1:2", "1:3"])
        self.assertEqual(tree[0].attrib["name"], 'Screen "quoted" & <escaped>')
        self.assertEqual(tree[1].attrib["type"], "COMPONENT_SET")
        self.assertEqual(tree[1][0].attrib["type"], "COMPONENT")
        self.assertEqual(tree[0].attrib["x"], "1.25")
        self.assertNotIn("width", tree.attrib)  # PAGE has no invented scene geometry.

    def test_original_receipt_identity_and_completeness_are_required(self):
        for field, value in [
            ("complete", False),
            ("read_only", False),
            ("errors", ["getter refused"]),
            ("source_format", "figma-rest-json-rest-v1"),
            ("property_profile", "unknown"),
        ]:
            with self.subTest(field=field):
                changed = receipt()
                changed[field] = value
                with self.assertRaises(ValueError):
                    topology.generate(encoded(changed))
        with self.assertRaises(ValueError):
            topology.generate(b'{"version":1,"version":1}')

    def test_malformed_graphs_and_unreadable_geometry_are_refused(self):
        variants = []
        changed = receipt()
        changed["nodes"].append(copy.deepcopy(changed["nodes"][1]))
        variants.append(changed)
        changed = receipt()
        changed["nodes"][2]["visual"]["parent_id"] = "1:3"
        variants.append(changed)
        changed = receipt()
        changed["nodes"][1]["visual"]["child_ids"].append("1:5")
        variants.append(changed)
        changed = receipt()
        changed["nodes"][1]["visual"]["child_ids"].append("1:99")
        variants.append(changed)
        changed = receipt()
        changed["nodes"][1]["visual"]["child_ids"].remove("1:5")
        variants.append(changed)
        changed = receipt()
        changed["nodes"][2]["visual"]["x"] = float("inf")
        variants.append(changed)
        changed = receipt()
        changed["nodes"][2]["visual"].pop("height")
        variants.append(changed)
        changed = receipt()
        changed["nodes"][2]["visual"]["parent_id"] = []
        variants.append(changed)
        changed = receipt()
        changed["nodes"][2]["type"] = changed["nodes"][2]["visual"]["type"] = "UNKNOWN"
        variants.append(changed)
        # A closed, internally consistent disconnected cycle must not become XML.
        changed = receipt()
        changed["nodes"][0]["visual"]["child_ids"].remove("1:3")
        changed["nodes"][4]["visual"]["parent_id"] = "1:4"
        changed["nodes"][5]["visual"]["child_ids"] = ["1:3"]
        variants.append(changed)
        for index, value in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                topology.generate(encoded(value))

    def test_xml_or_source_drift_cannot_reuse_original_provenance(self):
        raw = encoded(receipt())
        xml, digest = topology.generate(raw), hashlib.sha256(raw).hexdigest()
        altered = ET.fromstring(xml)
        altered[0].attrib["width"] = "99"
        with self.assertRaises(ValueError):
            topology.verify(ET.tostring(altered), raw, digest)
        with self.assertRaises(ValueError):
            topology.verify(xml, raw + b" ", digest)
        with self.assertRaises(ValueError):
            topology.verify(xml, raw, "0" * 64)

    def test_elided_native_aliases_fail_while_typed_derivation_preserves_inventory(self):
        with tempfile.TemporaryDirectory() as folder:
            path, body, original, _ = plugin_source_bundle(folder)
            self.assertEqual(manifest.load(path)["body"], body)  # Existing native form remains valid.
            page = body["files"][0]["pages"][0]
            page_path = path.parent / page["metadata_xml"]["path"]
            page_path.write_text("""<canvas id="0:1" name="Main">
              <frame id="1:2" name="Home" width="1440" height="900"/>
              <frame id="1:3" name="Button" width="100" height="20">
                <symbol id="1:4" name="Type=primary" width="100" height="20"/>
              </frame></canvas>""")
            page["metadata_xml"]["sha256"] = file_hash(page_path)
            path.write_text(json.dumps(body))
            with self.assertRaisesRegex(ValueError, "source frames and cases differ"):
                manifest.load(path)
            source_path = path.parent / page["source_json"]["path"]
            page_path.write_bytes(topology.generate(source_path.read_bytes()))
            page["metadata_xml"]["sha256"] = file_hash(page_path)
            path.write_text(json.dumps(body))
            self.assertEqual(manifest.load(path)["body"], body)
            self.assertEqual(inventory.screen_frames(body, path.parent), [("FILEA", "0:1", "1:2")])
            self.assertEqual(len(list(ET.parse(page_path).getroot().iter())), len(original["nodes"]))
            # Derived topology cannot replace the original source/resource checks.
            body["files"][0]["assets"] = []
            path.write_text(json.dumps(body))
            with self.assertRaisesRegex(ValueError, "assets"):
                manifest.load(path)

    def test_inventory_refuses_forged_or_unknown_derived_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = encoded(receipt())
            (root / "source.json").write_bytes(raw)
            xml = topology.generate(raw)
            (root / "page.xml").write_bytes(xml)
            page = dict(
                id="0:1",
                source_json=dict(path="source.json", sha256=file_hash(root / "source.json")),
                metadata_xml=dict(path="page.xml", sha256=file_hash(root / "page.xml")),
            )
            body = dict(files=[dict(key="FILEA", pages=[page])])
            self.assertEqual(inventory.screen_frames(body, root), [("FILEA", "0:1", "1:2")])
            for field, value in [("metadata_kind", "native-xml"), ("source_sha256", "0" * 64)]:
                tree = ET.fromstring(xml)
                tree.attrib[field] = value
                (root / "page.xml").write_bytes(ET.tostring(tree))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    inventory.screen_frames(body, root)
            (root / "page.xml").write_bytes(xml)
            (root / "source.json").write_bytes(raw + b" ")
            with self.assertRaises(ValueError):
                inventory.screen_frames(body, root)


if __name__ == "__main__":
    unittest.main()
