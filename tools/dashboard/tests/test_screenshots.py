"""Recorded image selection and filesystem boundaries; no runner mutation."""

import copy
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_screenshots import MAX_IMAGE_BYTES, project, read_image

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-image"


class ScreenshotEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "project"
        self.run = self.workspace / ".autocode/runs/selected"
        self.run.mkdir(parents=True)
        self.path = self.run / "page.png"
        self.path.write_bytes(PNG)
        self.view = {
            "criteria": [{"id": "R4", "criterion": "Errors are visible"}],
            "validation": {
                "source_revision": "source-a",
                "criterion_results": [{"id": "R4", "status": "FAIL", "evidence_refs": [str(self.path)]}],
            },
        }

    def selected(self):
        return project(self.view)[0]["id"]

    def read(self):
        return read_image(self.view, self.selected(), self.workspace, self.run)

    def test_saved_image_is_linked_to_criterion_without_inventing_acceptance(self):
        before = copy.deepcopy(self.view)
        rows = project(self.view)
        self.assertEqual("R4", rows[0]["criterion_id"])
        self.assertEqual("source-a", rows[0]["source_revision"])
        self.assertEqual("FAIL", rows[0]["status"])
        self.assertNotIn("ref", rows[0])
        self.assertEqual((PNG, "image/png"), self.read())
        self.assertEqual(before, self.view)
        self.assertEqual(rows, project(self.view))

    def test_unknown_or_old_report_identity_cannot_open_an_image(self):
        old = self.selected()
        self.view["validation"]["source_revision"] = "source-b"
        for token in (old, "arbitrary-path.png", "../page.png"):
            with self.assertRaises(ValueError):
                read_image(self.view, token, self.workspace, self.run)

    def test_only_unique_recorded_images_for_known_criteria_are_listed(self):
        row = self.view["validation"]["criterion_results"][0]
        row["evidence_refs"] += [str(self.path), "check:browser", "page.svg", "log.txt"]
        self.view["validation"]["criterion_results"].append({"id": "unknown", "evidence_refs": [str(self.path)]})
        self.assertEqual(1, len(project(self.view)))
        self.assertEqual([], project({}))

    def test_escape_sibling_run_symlink_and_directory_are_rejected(self):
        sibling = self.workspace / ".autocode/runs/sibling"
        sibling.mkdir()
        (sibling / "page.png").write_bytes(PNG)
        outside = self.root / "outside.png"
        outside.write_bytes(PNG)
        link = self.run / "link.png"
        link.symlink_to(outside)
        parent = self.run / "linked"
        parent.symlink_to(self.root, target_is_directory=True)
        folder = self.run / "folder.png"
        folder.mkdir()
        for ref in (
            str(outside),
            "../outside.png",
            str(link),
            str(parent / "outside.png"),
            str(folder),
            str(sibling / "page.png"),
        ):
            with self.subTest(ref=ref):
                self.view["validation"]["criterion_results"][0]["evidence_refs"] = [ref]
                with self.assertRaises(ValueError):
                    self.read()

    def test_fifo_oversize_and_nonimage_are_rejected(self):
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaises(ValueError):
            self.read()
        self.path.unlink()
        with self.path.open("wb") as f:
            f.write(PNG)
            f.truncate(MAX_IMAGE_BYTES + 1)
        with self.assertRaises(ValueError):
            self.read()
        self.path.write_bytes(b"<html>not a PNG</html>")
        with self.assertRaises(ValueError):
            self.read()

    def test_recorded_image_fingerprint_rejects_changed_contents(self):
        self.view["validation"]["evidence_hashes"] = {str(self.path): hashlib.sha256(PNG).hexdigest()}
        self.assertTrue(project(self.view)[0]["hash_recorded"])
        self.assertEqual((PNG, "image/png"), self.read())
        self.path.write_bytes(PNG + b"changed")
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.read()

    def test_relative_workspace_capture_is_supported(self):
        capture = self.workspace / ".autocode/captures/example/page.png"
        capture.parent.mkdir(parents=True)
        capture.write_bytes(PNG)
        self.view["validation"]["criterion_results"][0]["evidence_refs"] = [str(capture.relative_to(self.workspace))]
        self.assertEqual((PNG, "image/png"), self.read())


class ScreenshotHTTPTests(unittest.TestCase):
    def test_registered_same_origin_image_route_and_stale_refusal(self):
        import http.client
        import threading
        from types import SimpleNamespace
        from urllib.parse import urlencode

        from agent_console import Handler, LoopbackHTTPServer

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            run = workspace / "run"
            run.mkdir()
            image = run / "page.png"
            image.write_bytes(PNG)
            view = {
                "criteria": [{"id": "R4"}],
                "validation": {
                    "source_revision": "v1",
                    "criterion_results": [{"id": "R4", "status": "FAIL", "evidence_refs": [str(image)]}],
                },
            }
            selected = project(view)[0]["id"]
            archived = []
            console = SimpleNamespace(
                removed_project=lambda _: False,
                archived_task=lambda _: bool(archived),
                workspace_for=lambda raw: workspace if raw == str(workspace) else (_ for _ in ()).throw(ValueError()),
                run_for=lambda ws, raw: run if raw == str(run) else None,
                task_view=lambda ws, r: view,
            )
            server = LoopbackHTTPServer(("127.0.0.1", 0), Handler)
            server.console = console
            host = "127.0.0.1:" + str(server.server_port)
            server.hosts = {host}
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()

            def get(token=selected, selected_run=str(run), headers=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request(
                    "GET",
                    "/api/screenshot?" + urlencode({"workspace": str(workspace), "run": selected_run, "image": token}),
                    headers=headers or {},
                )
                response = connection.getresponse()
                result = (response.status, dict(response.getheaders()), response.read())
                connection.close()
                return result

            try:
                status, headers, raw = get()
                self.assertEqual((200, PNG), (status, raw))
                self.assertEqual("image/png", headers["Content-Type"])
                self.assertEqual("private, no-store", headers["Cache-Control"])
                self.assertEqual("nosniff", headers["X-Content-Type-Options"])
                self.assertEqual(403, get(headers={"Origin": "https://unrelated.example"})[0])
                self.assertEqual(404, get(selected_run=str(workspace / "other"))[0])
                self.assertEqual(400, get(token="arbitrary-file.png")[0])
                archived.append(True)
                self.assertEqual(404, get()[0])
                archived.clear()
                view["validation"]["source_revision"] = "v2"
                self.assertEqual(400, get()[0])
                self.assertEqual(200, get(token=project(view)[0]["id"])[0])
            finally:
                server.shutdown()
                server.server_close()
                worker.join()
