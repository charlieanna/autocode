"""Exercise the repository's checkout policy with real Git."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ATTRIBUTES = Path(__file__).resolve().parents[1] / ".gitattributes"
TEXT = b"alpha\nbeta\n"
FILES = {
    "sample.py": TEXT,
    "opaque.bin": b"\x00\xff\r\n\x80\x00",
    "asset.png": b"\x00PNG\r\n",
    "asset.icns": b"\x00ICNS\r\n",
    # Even an ASCII font placeholder must not undergo text conversion.
    "asset.woff2": b"font placeholder\r\n",
}


class GitAttributesTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        config = self.root / "empty-gitconfig"
        config.write_text("")
        self.empty_attributes = str(config)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(config),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_COUNT": "0",
                    "GIT_ATTR_NOSYSTEM": "1"}
        self.seed = self.root / "seed"
        self.seed.mkdir()
        (self.seed / ".gitattributes").write_bytes(ATTRIBUTES.read_bytes())
        for name, content in FILES.items():
            (self.seed / name).write_bytes(content)
        self.git(self.seed, "init", "--quiet")
        self.git(self.seed, "-c", "core.autocrlf=false", "add", ".")
        self.git(self.seed, "-c", "user.name=Git Control", "-c",
                 "user.email=git-control@example.invalid", "commit", "--quiet", "-m", "Seed")

    def git(self, repo, *args):
        return subprocess.run(["git", "-c", f"core.attributesFile={self.empty_attributes}",
                               "-C", str(repo), *args], env=self.env,
                              capture_output=True, check=True, timeout=30).stdout

    def checkout(self, mode):
        target = self.root / mode
        self.git(self.root, "clone", "--quiet", "--no-hardlinks", "--no-checkout",
                 str(self.seed), str(target))
        self.git(target, "-c", f"core.autocrlf={mode}", "checkout", "HEAD", "--", ".")
        return target

    def test_checkouts_pin_lf_and_preserve_binary_and_font_bytes(self):
        for mode in ("false", "input", "true"):
            with self.subTest(core_autocrlf=mode):
                repo = self.checkout(mode)
                for name, content in FILES.items():
                    self.assertEqual(content, (repo / name).read_bytes(), name)
                self.assertEqual(b"", self.git(repo, "-c", "core.autocrlf=false",
                                              "-c", "core.fileMode=false", "status", "--porcelain"))

    def test_real_content_edits_remain_visible(self):
        repo = self.checkout("true")
        (repo / "sample.py").write_bytes(TEXT + b"gamma\n")
        self.assertIn(b"+gamma", self.git(repo, "-c", "core.autocrlf=false", "diff", "--", "sample.py"))
