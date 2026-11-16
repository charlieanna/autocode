"""Exact legacy list-prefix digests stay linear without truncating history."""

import hashlib
import json
import subprocess
import unittest
from unittest.mock import patch

import autocode_util as util


class DigestPrefixTests(unittest.TestCase):
    def setUp(self):
        self.spawn = self.enterContext(patch.object(subprocess, "Popen", side_effect=AssertionError("no process")))

    def tearDown(self):
        self.spawn.assert_not_called()

    def test_every_legacy_canonical_prefix_matches_with_unicode_nested_and_scalar_values(self):
        values = [None, True, False, 1, -2.5, "é\n雪", {"z": [1, {"b": "x", "a": 2}], "a": None}, [], {}]
        for length in range(len(values) + 1):
            with self.subTest(length=length):
                # This is the exact pre-refactor encoding, independently of
                # either production digest helper.
                expected = hashlib.sha256(
                    json.dumps(values[:length], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                        "utf-8"
                    )
                ).hexdigest()
                self.assertEqual(expected, util.digest(values[:length]))
                self.assertEqual(length, util.digest_prefix_length(values, expected))
        self.assertIsNone(util.digest_prefix_length(values, util.digest(["missing-prefix"])))
        self.assertIsNone(util.digest_prefix_length(False, util.digest([])))
        self.assertIsNone(util.digest_prefix_length(values, None))

    def test_missing_or_old_prefix_serializes_each_event_once_at_growing_history_sizes(self):
        canonical = util._canonical_json
        for size in (256, 512, 1024):
            values = [{"id": str(index), "text": "x" * 40} for index in range(size)]
            for expected, result in (("missing", None), (util.digest(values[:7]), 7)):
                with self.subTest(size=size, result=result):
                    with patch.object(util, "_canonical_json", wraps=canonical) as encoded:
                        self.assertEqual(result, util.digest_prefix_length(values, expected))
                    self.assertEqual(size, encoded.call_count)
                    self.assertEqual(values, [call.args[0] for call in encoded.call_args_list])
