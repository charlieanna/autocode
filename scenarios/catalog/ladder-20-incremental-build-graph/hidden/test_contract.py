import tempfile
import unittest
from pathlib import Path

from buildgraph import Builder, topology


class BuildContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "cache"
        self.calls = []
        self.graph = {"top": ["left", "right"], "right": ["base"], "left": ["base"], "base": [], "solo": []}
        self.sources = {key: key for key in self.graph}

    def compile(self, node, source, deps):
        self.calls.append(node)
        return source + "(" + ",".join(deps[key] for key in sorted(deps)) + ")"

    def test_diamond_changes_only_affected_nodes_after_restart(self):
        initial = Builder(self.graph, self.path).build(self.sources, self.compile)
        self.calls.clear()
        self.sources["base"] = "new"
        result = Builder(self.graph, self.path).build(self.sources, self.compile)
        self.assertEqual(self.calls, ["base", "left", "right", "top"])
        self.assertNotEqual(result["top"], initial["top"])
        self.assertEqual(result["solo"], initial["solo"])
        self.calls.clear()
        graph = {k: v for k, v in self.graph.items() if k != "solo"}
        result = Builder(graph, self.path).build({k: v for k, v in self.sources.items() if k != "solo"}, self.compile)
        self.assertNotIn("solo", result)
        self.assertEqual(self.calls, [])

    def test_failed_build_and_bad_input_preserve_cache(self):
        Builder(self.graph, self.path).build(self.sources, self.compile)
        before = self.path.read_bytes()
        self.sources["base"] = "changed"

        def fail(node, source, deps):
            if node == "right":
                raise RuntimeError("compiler failure")
            return self.compile(node, source, deps)

        with self.assertRaises(RuntimeError):
            Builder(self.graph, self.path).build(self.sources, fail)
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            Builder(self.graph, self.path).build({}, self.compile)
        self.assertEqual(self.path.read_bytes(), before)
        self.calls.clear()
        self.path.write_text("{broken")
        with self.assertRaises(ValueError):
            Builder(self.graph, self.path).build(self.sources, self.compile)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.path.read_text(), "{broken")

    def test_invalid_graphs_and_lexical_ties(self):
        for graph in ({"a": ["x"]}, {"a": ["a"]}, {"a": [], "b": ["c"], "c": ["b"]}, {"a": [], "b": ["a", "a"]}):
            with self.assertRaises(ValueError):
                topology(graph)
        self.assertEqual(topology({"z": [], "b": ["a"], "a": []}), ["a", "b", "z"])

    def test_dependency_list_change_invalidates_even_equal_output(self):
        graph = {"a": [], "b": [], "c": ["a"]}
        sources = {n: "same" for n in graph}
        compile_fn = lambda node, source, deps: "same"
        Builder(graph, self.path).build(sources, compile_fn)
        graph["c"] = ["b"]
        calls = []
        Builder(graph, self.path).build(sources, lambda n, s, d: calls.append(n) or "same")
        self.assertEqual(calls, ["c"])

    def test_removed_node_is_not_resurrected_from_stale_cache(self):
        Builder(self.graph, self.path).build(self.sources, self.compile)
        reduced = {key: deps for key, deps in self.graph.items() if key != "solo"}
        Builder(reduced, self.path).build(
            {key: value for key, value in self.sources.items() if key != "solo"}, self.compile
        )
        self.calls.clear()
        Builder(self.graph, self.path).build(self.sources, self.compile)
        self.assertEqual(self.calls, ["solo"])

    def test_callback_receives_only_direct_dependencies(self):
        def compiler(node, source, deps):
            self.assertEqual(set(deps), set(self.graph[node]))
            return self.compile(node, source, deps)

        Builder(self.graph, self.path).build(self.sources, compiler)
