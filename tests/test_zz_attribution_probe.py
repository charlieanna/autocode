"""A deliberate fault, to exercise the attribution step end to end (#2603).

The attribution job only runs when the suite is red (``if: failure()``), so the
only way to see it post its verdict and comment is to give it a red suite. This
module is that fault: it fails, it is new on this branch, so the attribution must
report it ``introduced (new)`` while every other module reads ``pre-existing``
or is not listed at all.

**Revert this before anything merges.**
"""

import unittest


class DeliberateFaultTests(unittest.TestCase):
    def test_this_fails_on_purpose_to_make_the_suite_red(self):
        self.fail("deliberate fault: verifying the attribution step for #2603")
