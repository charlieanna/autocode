"""Real local Chromium controls, separate from synthetic pixel fixtures."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from tests.visual_check_fixtures import (
    BROWSER_CAPTURE_SOURCE, BROWSER_HTML, Image, VisualProject, sha256,
)


class VisualCheckBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = os.environ.get("AUTOCODE_REQUIRE_VISUAL_BROWSER") == "1"
        try:
            if Image is None:
                raise ImportError("Pillow is not installed")
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                executable = os.environ.get("AUTOCODE_CHROMIUM") or os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
                browser = playwright.chromium.launch(
                    headless=True, **({"executable_path": executable} if executable else {}))
                browser.close()
        except Exception as error:
            if required:
                raise RuntimeError(f"Required visual browser tests cannot launch Chromium: {error}") from error
            raise unittest.SkipTest(f"Optional local Playwright/Chromium unavailable: {error}") from error

    def test_browser_rendered_baseline_detects_layout_and_missing_control_with_working_click(self):
        project = VisualProject(cases=[{"id": "desktop", "state": "ready", "route": "/",
                                       "viewport": {"width": 240, "height": 160, "device_scale_factor": 1}}])
        self.addCleanup(project.close)
        project.write("capture.py", BROWSER_CAPTURE_SOURCE)
        project.write("app.html", BROWSER_HTML)
        project.manifest["cases"][0]["implementation_paths"] = ["app.html"]
        project.pin_manifest()
        baseline_output = project.root / ".autocode/browser-baseline"
        baseline_output.mkdir(parents=True)
        baseline = subprocess.run([sys.executable, "capture.py"], cwd=project.root,
                                  env={**os.environ, "AUTOCODE_VISUAL_OUTPUT": str(baseline_output),
                                       "AUTOCODE_VISUAL_MANIFEST": str(project.manifest_path)},
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(0, baseline.returncode, baseline.stdout + baseline.stderr)
        self.assertIn("Browser click assertion passed", baseline.stdout)
        reference = project.root / "design/desktop.png"
        reference.write_bytes((baseline_output / "desktop.png").read_bytes())
        project.manifest["cases"][0]["artifacts"]["screenshot"]["sha256"] = sha256(reference)
        project.pin_manifest()
        approved = project.inputs()
        variants = [("match", BROWSER_HTML, "PASS", 0),
                    ("sidebar-offset", BROWSER_HTML.replace("left: 0px", "left: 11px"), "FAIL", 1),
                    ("missing-control", BROWSER_HTML.replace("#extra {", "#extra { visibility: hidden;"), "FAIL", 1)]
        for name, html, status, code in variants:
            with self.subTest(variant=name):
                project.write("app.html", html)
                result = project.invoke(output=f".autocode/visual-checks/browser-{name}")
                self.assertEqual(code, result.returncode, result.stdout + result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(status, report["status"])
                self.assertEqual(status, report["cases"][0]["status"])
                changed = report["cases"][0]["changed_pixels"]
                if status == "PASS":
                    self.assertEqual(0, changed)
                else:
                    self.assertGreater(changed, 0)
                self.assertEqual("NOT_PERFORMED", report["independent_visual_review"])
                self.assertEqual("project-owned fixture, not authenticated browser provenance", report["capture_provenance"])
                self.assertEqual(approved, project.inputs())
                capture_log = project.root / report["output"] / report["capture_log"]["path"]
                self.assertIn("Browser click assertion passed", capture_log.read_text())


if __name__ == "__main__":
    unittest.main()
