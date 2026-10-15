"""Pure alias traversal semantics; no models, filesystem sources or Figma calls."""

import unittest

import autocode_design_sources as sources


class DesignSourceTraversalTests(unittest.TestCase):
    def test_aliases_keep_nested_bindings_falsey_ids_and_fresh_results(self):
        shared = {"type": "VARIABLE_ALIAS", "id": "shared"}
        body = {
            "type": "VARIABLE_ALIAS",
            "id": 0,
            "variableId": "direct",
            "children": [
                shared,
                {"expression": {"variableId": "expression"}},
                {"type": "VARIABLE_ALIAS", "id": "", "variableId": ""},
                {"type": "VARIABLE_ALIAS", "id": None},
                shared,
            ],
            "ignored": ({"variableId": "tuple-is-not-JSON"},),
        }
        self.assertEqual({0, "", None, "direct", "shared", "expression"}, sources.aliases(body))
        shared["id"] = "changed"
        self.assertEqual({0, "", None, "direct", "changed", "expression"}, sources.aliases(body))
        self.assertEqual(set(), sources.aliases([None, False, 1, "ordinary", {"variableId": 0}]))

    def test_alias_errors_keep_parent_and_left_to_right_order(self):
        with self.assertRaises(KeyError) as missing:
            sources.aliases([{"type": "VARIABLE_ALIAS"}, {"variableId": []}])
        self.assertEqual(("id",), missing.exception.args)
        for value in [{"type": "VARIABLE_ALIAS", "id": []}, {"variableId": ["bad"]}]:
            with self.subTest(value=value), self.assertRaises(TypeError):
                sources.aliases(value)
        with self.assertRaises(TypeError):
            sources.aliases({"type": "VARIABLE_ALIAS", "id": {}, "child": {"type": "VARIABLE_ALIAS"}})
        with self.assertRaises(KeyError):
            sources.aliases({"left": {"type": "VARIABLE_ALIAS"}, "right": {"variableId": [1]}})

    def test_alias_cycles_refuse_without_skipping_shared_subtrees(self):
        cyclic_list = []
        cyclic_list.append(cyclic_list)
        cyclic_dict = {}
        cyclic_dict["self"] = cyclic_dict
        for value in [cyclic_list, cyclic_dict]:
            with self.subTest(kind=type(value).__name__), self.assertRaises(RecursionError):
                sources.aliases(value)
        shared = [{"variableId": "retained"}]
        self.assertEqual({"retained"}, sources.aliases([shared, shared]))


if __name__ == "__main__":
    unittest.main()
