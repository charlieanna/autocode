import collections
import json
import random
import re
import subprocess
import sys
import unittest


class HiddenWordFrequencyTests(unittest.TestCase):
    def invoke(self, text, *args):
        return subprocess.run(
            [sys.executable, "-m", "wordfreq", *args], input=text, capture_output=True, text=True, timeout=10
        )

    def test_tokenization_and_ties(self):
        texts = [
            "zebra apple banana",
            "A a B b",
            "can't stop42now snake_case",
            "café naïve Ångström İ K",
            "\n\t !!! 123",
            "",
            "one\ntwo one\tTHREE",
        ]
        rng = random.Random(712)
        texts.append(" ".join(rng.choice(["Zed", "APPLE", "b", "pear"]) for _ in range(200)))
        for text in texts:
            counts = collections.Counter(word.lower() for word in re.findall("[A-Za-z]+", text))
            expected = [
                {"word": w, "count": n} for w, n in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
            ]
            for limit in [None, 1, 2, 999]:
                with self.subTest(text=text, limit=limit):
                    p = self.invoke(text, *([] if limit is None else ["--limit", str(limit)]))
                    self.assertEqual((p.returncode, p.stderr), (0, ""))
                    self.assertEqual(json.loads(p.stdout), expected[:limit])
                    self.assertTrue(p.stdout.endswith("\n"))

    def test_invalid_arguments(self):
        for args in [["--limit", "0"], ["--limit", "-3"], ["--limit", "x"], ["--limit"], ["--other"], ["extra"]]:
            p = self.invoke("hello", *args)
            self.assertEqual((p.returncode, p.stdout), (2, ""))
            self.assertTrue(p.stderr)
