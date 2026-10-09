import json
import subprocess
import sys
import unittest


class WordFrequencyTests(unittest.TestCase):
    def invoke(self, text, *args):
        return subprocess.run([sys.executable, "-m", "wordfreq", *args], input=text, capture_output=True, text=True, timeout=10)

    def test_counts(self):
        p = self.invoke("Red red blue")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout), [{"word":"red", "count":2}, {"word":"blue", "count":1}])

    def test_empty(self):
        self.assertEqual(json.loads(self.invoke("").stdout), [])

    def test_limit(self):
        self.assertEqual(json.loads(self.invoke("red red blue", "--limit", "1").stdout), [{"word":"red", "count":2}])
