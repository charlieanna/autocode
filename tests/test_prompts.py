"""Prompt set identity is exact bytes, import-cached, and installable data."""

import ast
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_prompts as prompts


class PromptResourceTests(unittest.TestCase):
    def test_complete_named_resource_set_has_one_content_identity(self):
        root = Path(prompts.__file__).with_name("prompts")
        actual = {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*.md"))}
        self.assertGreater(len(actual), 20)
        self.assertEqual(actual, prompts.CONTENTS)
        self.assertEqual(tuple(actual), prompts.FILES)
        self.assertEqual(prompts.content_hash(actual), prompts.HASH)
        for name, value in actual.items():
            with self.subTest(resource=name):
                self.assertEqual(value, prompts.get(name).encode("utf-8"))

    def test_identity_binds_names_contents_order_and_boundaries(self):
        self.assertEqual(
            prompts.content_hash({"b": b"two", "a": b"one"}), prompts.content_hash({"a": b"one", "b": b"two"})
        )
        self.assertNotEqual(prompts.content_hash({"a": b"same"}), prompts.content_hash({"renamed": b"same"}))
        self.assertNotEqual(prompts.content_hash({"a": b"same"}), prompts.content_hash({"a": b"changed"}))
        self.assertNotEqual(prompts.content_hash({"ab": b"c"}), prompts.content_hash({"a": b"bc"}))
        self.assertNotEqual(prompts.content_hash({"a": b"one", "b": b"two"}), prompts.content_hash({"a": b"onebthree"}))

    def test_render_uses_the_import_cached_set_without_disk_reads(self):
        name = prompts.FILES[0]
        expected = prompts.get(name)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("render reread prompt resources")):
            self.assertEqual(expected, prompts.get(name))
            self.assertEqual(prompts.HASH, prompts.content_hash(prompts.CONTENTS))
        with self.assertRaises(TypeError):
            prompts.CONTENTS[name] = b"mutated"
        with self.assertRaises(KeyError):
            prompts.get("missing-resource.md")

    def import_fixture(self, root):
        module_path = root / "autocode_prompts.py"
        module_path.write_bytes(Path(prompts.__file__).read_bytes())
        spec = importlib.util.spec_from_file_location("isolated_prompt_fixture", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_loading_preserves_unicode_carriage_returns_and_terminal_whitespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / "prompts"
            resources.mkdir()
            original = "\nRequirements: café\r\nKeep the last spaces  ".encode()
            (resources / "requirements.md").write_bytes(original)
            module = self.import_fixture(root)
            self.assertEqual(original, module.CONTENTS["requirements.md"])
            self.assertEqual(original, module.get("requirements.md").encode("utf-8"))
            before = module.HASH
            (resources / "requirements.md").write_bytes(b"changed after import")
            self.assertEqual(before, module.HASH)
            self.assertEqual(original, module.get("requirements.md").encode("utf-8"))
            fresh = self.import_fixture(root)
            self.assertNotEqual(before, fresh.HASH)
            self.assertEqual(b"changed after import", fresh.get("requirements.md").encode("utf-8"))
            (resources / "requirements.md").rename(resources / "renamed.md")
            renamed = self.import_fixture(root)
            self.assertEqual(("renamed.md",), renamed.FILES)
            self.assertNotEqual(fresh.HASH, renamed.HASH)
            self.assertEqual(original, module.get("requirements.md").encode("utf-8"))

    def test_non_utf8_prompt_resources_refuse_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = root / "prompts"
            resources.mkdir()
            (resources / "broken.md").write_bytes(b"invalid \xff")
            with self.assertRaises(UnicodeDecodeError):
                self.import_fixture(root)

    def test_missing_or_empty_package_resources_refuse_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(FileNotFoundError):
                self.import_fixture(root)
            (root / "prompts").mkdir()
            with self.assertRaises(ValueError):
                self.import_fixture(root)

    def test_resource_inventory_matches_model_facing_assembly_references(self):
        tools = Path(prompts.__file__).parent
        references = set()
        for path in tools.rglob("*.py"):
            if "dashboard" in path.parts:
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "prompts"
                    and node.func.attr == "get"
                ):
                    self.assertEqual(len(node.args), 1, str(path))
                    references.add(ast.literal_eval(node.args[0]))
        self.assertEqual(set(prompts.FILES), references)


if __name__ == "__main__":
    unittest.main()
