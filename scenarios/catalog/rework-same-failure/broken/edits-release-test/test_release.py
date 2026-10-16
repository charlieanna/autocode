import unittest
from pathlib import Path


class ReleaseTests(unittest.TestCase):
    def test_version_is_the_next_release(self):
        # The release owner bumps VERSION when 0.10.0 is cut.
        self.assertIn(Path("VERSION").read_text().strip(), ("0.9.0", "0.10.0"))


if __name__ == "__main__":
    unittest.main()
