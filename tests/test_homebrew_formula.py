import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("homebrew_formula", ROOT / "scripts" / "homebrew_formula.py")
assert SPEC and SPEC.loader
brew = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(brew)


def release(name, version, *, sdist=True):
    rows = [{"packagetype": "bdist_wheel", "url": f"https://files/{name}-{version}.whl", "digests": {"sha256": "w"}}]
    if sdist:
        rows.append(
            {
                "packagetype": "sdist",
                "url": f"https://files/{name}-{version}.tar.gz",
                "digests": {"sha256": f"{name}-{version}-sha"},
            }
        )
    return {"info": {"version": version}, "urls": rows}


PYPI = {
    "autocode-supervisor/0.8.0": release("autocode_supervisor", "0.8.0"),
    "psutil": {"releases": {"7.0.0": [{}], "7.2.2": [{}], "7.3.0rc1": [{}], "8.0.0": [{}], "7.9.0": []}},
    "psutil/7.2.2": release("psutil", "7.2.2"),
}
PYPROJECT = {"project": {"version": "0.8.0", "dependencies": ["psutil>=7.1,<8"]}}


class FormulaTests(unittest.TestCase):
    def test_pins_the_release_sdist_and_the_newest_dependency_its_range_allows(self):
        text = brew.formula("0.8.0", PYPROJECT, PYPI.__getitem__)
        self.assertIn('url "https://files/autocode_supervisor-0.8.0.tar.gz"', text)
        self.assertIn('sha256 "autocode_supervisor-0.8.0-sha"', text)
        self.assertIn('resource "psutil" do\n    url "https://files/psutil-7.2.2.tar.gz"', text)
        self.assertIn('bin.install_symlink libexec/"bin/autocode"', text)
        self.assertNotIn(".whl", text)

    def test_refuses_a_version_pyproject_does_not_name(self):
        with self.assertRaisesRegex(SystemExit, "not 0.9.0"):
            brew.formula("0.9.0", PYPROJECT, PYPI.__getitem__)

    def test_refuses_a_release_without_an_sdist(self):
        fetch = {**PYPI, "autocode-supervisor/0.8.0": release("autocode_supervisor", "0.8.0", sdist=False)}
        with self.assertRaisesRegex(SystemExit, "has no sdist"):
            brew.formula("0.8.0", PYPROJECT, fetch.__getitem__)

    def test_the_repository_dependencies_are_ones_it_can_read(self):
        import tomllib

        for requirement in tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]:
            name, fits = brew.allowed(requirement)
            self.assertTrue(name)
            fits("1.0")

    @unittest.skipUnless(shutil.which("ruby"), "ruby is not installed")
    def test_the_formula_is_valid_ruby(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "autocode.rb"
            path.write_text(brew.formula("0.8.0", PYPROJECT, PYPI.__getitem__))
            subprocess.run(["ruby", "-c", str(path)], check=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
