#! /usr/bin/env python3

"""
Regression test: the "include" directive's :number-lines: accepts the
start value 0 (Sphinx issue #14261).
"""

from pathlib import Path
import sys
import unittest

if __name__ == '__main__':
    # prepend the "docutils root" to the Python library path
    # so we import the local `docutils` package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from docutils.core import publish_doctree
from docutils import nodes

INCLUDE1 = Path(__file__).resolve().with_name('include1.rst')


def line_numbers(options):
    source = f'.. include:: {INCLUDE1}\n   :literal:\n{options}'
    doctree = publish_doctree(source,
                              settings_overrides={'warning_stream': ''})
    return [node.astext().strip()
            for node in doctree.findall(nodes.inline)
            if 'ln' in node['classes']]


class IncludeNumberLinesTestCase(unittest.TestCase):

    def test_start_value_zero(self):
        self.assertEqual(['0', '1', '2', '3'],
                         line_numbers('   :number-lines: 0\n'))

    def test_default_start_value(self):
        self.assertEqual(['1', '2', '3', '4'],
                         line_numbers('   :number-lines:\n'))


if __name__ == '__main__':
    unittest.main()
