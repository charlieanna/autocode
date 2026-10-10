import tempfile
import unittest
from pathlib import Path

from buildgraph import Builder, topology


class BuildTests(unittest.TestCase):
    def test_build_and_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache.json"
            graph = {"app": ["lib"], "lib": []}
            calls = []

            def compiler(node, source, deps):
                calls.append(node)
                return source + "".join(deps.values())

            self.assertEqual(topology(graph), ["lib", "app"])
            self.assertEqual(Builder(graph, path).build({"lib": "L", "app": "A"}, compiler), {"lib": "L", "app": "AL"})
            calls.clear()
            Builder(graph, path).build({"lib": "L", "app": "A"}, compiler)
            self.assertEqual(calls, [])

    def test_diamond_invalidation_and_failed_build_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache"
            graph = {"root": [], "a": ["root"], "b": ["root"], "app": ["a", "b"]}
            sources = {key: key for key in graph}
            calls = []

            def compiler(node, source, deps):
                calls.append(node)
                return source + "".join(deps.values())

            Builder(graph, path).build(sources, compiler)
            before = path.read_bytes()
            sources["root"] = "changed"
            calls.clear()

            def fail(node, source, deps):
                if node == "b":
                    raise RuntimeError("broken compiler")
                return compiler(node, source, deps)

            with self.assertRaises(RuntimeError):
                Builder(graph, path).build(sources, fail)
            self.assertEqual(path.read_bytes(), before)
            calls.clear()
            Builder(graph, path).build(sources, compiler)
            self.assertEqual(calls, ["root", "a", "b", "app"])
