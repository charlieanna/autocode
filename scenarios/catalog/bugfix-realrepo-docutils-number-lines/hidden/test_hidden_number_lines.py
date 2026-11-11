"""Line numbers of an included file and of a "code" block, through the public publish_doctree."""

import tempfile
import unittest
from pathlib import Path

from docutils import nodes
from docutils.core import publish_doctree

LINES = "alpha\nbeta\ngamma\n"
QUIET = {"report_level": 5, "halt_level": 5, "warning_stream": ""}


def numbers(source: str) -> list[str]:
    doctree = publish_doctree(source, settings_overrides=QUIET)
    found = doctree.findall(lambda node: isinstance(node, nodes.inline) and "ln" in node["classes"])
    return [node.astext().strip() for node in found]


def include(option: str, kind: str = ":literal:") -> list[str]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "included.txt"
        path.write_text(LINES)
        return numbers(f".. include:: {path}\n   {kind}\n   :number-lines:{option}\n")


def code(option: str) -> list[str]:
    body = "".join(f"   {line}\n" for line in LINES.splitlines())
    return numbers(f".. code::\n   :number-lines:{option}\n\n{body}")


class IncludeNumberLines(unittest.TestCase):
    def test_start_value_zero(self):
        self.assertEqual(["0", "1", "2"], include(" 0"))

    def test_start_value_zero_for_included_code(self):
        self.assertEqual(["0", "1", "2"], include(" 0", kind=":code:"))

    def test_no_value_starts_at_one(self):
        self.assertEqual(["1", "2", "3"], include(""))

    def test_other_start_values(self):
        self.assertEqual(["7", "8", "9"], include(" 7"))
        self.assertEqual(["-1", "0", "1"], include(" -1"))


class CodeNumberLines(unittest.TestCase):
    def test_start_value_zero(self):
        self.assertEqual(["0", "1", "2"], code(" 0"))

    def test_no_value_starts_at_one(self):
        self.assertEqual(["1", "2", "3"], code(""))

    def test_other_start_value(self):
        self.assertEqual(["7", "8", "9"], code(" 7"))


if __name__ == "__main__":
    unittest.main()
