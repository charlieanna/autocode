import unittest

from configmerge import merge


class ConfigMergeTests(unittest.TestCase):
    def test_nested(self):
        self.assertEqual(merge({"db":{"host":"local","port":10}}, {"db":{"port":20}}), {"db":{"host":"local","port":20}})

    def test_arrays_replace(self):
        self.assertEqual(merge({"a":[1,2]}, {"a":[3]}), {"a":[3]})

    def test_inputs_preserved(self):
        base={"a":{"x":1}}
        override={"a":{"y":2}}
        merge(base,override)
        self.assertEqual(base,{"a":{"x":1}})
        self.assertEqual(override,{"a":{"y":2}})
