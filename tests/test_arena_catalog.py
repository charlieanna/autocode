"""Prepare real Git revisions through the Arena CLI without network or model calls."""

import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("arena_prepare", ROOT / "arena/prepare.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class CatalogTests(unittest.TestCase):
    def test_catalog_lists_real_pinned_projects_without_preparing(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, prepare.main(["--list"]))
        cases = json.loads(output.getvalue())
        self.assertEqual(
            {"Boltons", "Humanize", "More Itertools", "SymPy", "Django", "pytest", "TypeScript", "Go x/net/http2"},
            {c["project"] for c in cases},
        )
        self.assertEqual({"development", "regression", "holdout"}, {c["split"] for c in cases})
        self.assertEqual({"starter", "hard"}, {c["tier"] for c in cases})
        self.assertEqual(
            ["typescript-dependent-destructuring-baselines-v2"],
            [case["id"] for case in cases if "preparation_note" in case],
        )
        for case in cases:
            for field in ("base_commit", "reference_commit"):
                self.assertRegex(case[field], r"^[0-9a-f]{40}$")
            self.assertNotEqual(case["base_commit"], case["reference_commit"])
            fixture = ROOT / "arena/cases" / case["id"]
            payload = prepare.fixture_directory(ROOT / "arena", case.get("fixture_case", case["id"]))
            self.assertTrue((payload / "oracle.py").is_file())
            self.assertIn("problem-only adaptation", json.loads((fixture / "issue.json").read_text())["provenance"])

    def test_successor_uses_own_brief_and_original_oracle_and_adapter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "upstream"
            repo.mkdir()

            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

            git("init", "-q")
            (repo / "answer.py").write_text('print("broken")\n')
            git("add", ".")
            git("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "baseline")
            base = git("rev-parse", "HEAD")
            (repo / "answer.py").write_text('print("correct")\n')
            git("add", ".")
            git("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "reference")
            fixed = git("rev-parse", "HEAD")
            original = root / "cases/original"
            successor = root / "cases/successor"
            original.mkdir(parents=True)
            successor.mkdir(parents=True)
            original_issue = {"title": "Original task", "body": "Print correct and retain all expectations."}
            successor_issue = json.loads(
                (ROOT / "arena/cases/typescript-dependent-destructuring-baselines-v2/issue.json").read_text()
            )
            successor_case = next(
                row
                for row in json.loads((ROOT / "arena/catalog.json").read_text())["cases"]
                if row["id"] == "typescript-dependent-destructuring-baselines-v2"
            )
            successor_note = successor_case["preparation_note"]
            (original / "issue.json").write_text(json.dumps(original_issue))
            (successor / "issue.json").write_text(json.dumps(successor_issue))
            oracle = original / "oracle.py"
            oracle.write_text(
                "import json, sys\nfrom pathlib import Path\n"
                'print(json.dumps({"checks": [{"name": "output", "ok": '
                '(Path(sys.argv[1]) / "answer.py").read_text() == \'print("correct")\\n\'}]}))\n'
            )
            adapter = original / "test_overlay/tests/preservation.test.cjs"
            adapter.parent.mkdir(parents=True)
            adapter.write_text("// Original preservation adapter.\n")
            original_bytes = {path: path.read_bytes() for path in (original / "issue.json", oracle, adapter)}
            case = {
                "id": "original",
                "repository": str(repo),
                "base_commit": base,
                "reference_commit": fixed,
                "issue_ref": "example/project#1",
                "split": "development",
                "checks": ["output"],
                "test_overlay": True,
            }
            destination = root / "arena"
            prepare.prepare(
                destination,
                [
                    case,
                    {
                        **case,
                        "id": "successor",
                        "fixture_case": "original",
                        "issue_ref": successor_case["issue_ref"],
                        "preparation_note": successor_note,
                    },
                ],
                catalog_root=root,
                oracle_timeout=5,
            )
            admitted = json.loads((destination / "catalog.json").read_text())["cases"]
            self.assertEqual(["original", "successor"], [row["id"] for row in admitted])
            self.assertIn(original_issue["body"], admitted[0]["task"])
            self.assertNotIn(successor_issue["body"], admitted[0]["task"])
            self.assertIn(successor_issue["body"], admitted[1]["task"])
            original_note = (
                "Evaluator setup adds identical native preservation tests to the pinned baseline and reference. "
                "Runtime source remains the upstream baseline " + base + ". "
                "Extend the existing adapter tests with regression cases; retain its preservation guards."
            )
            self.assertEqual(
                original_note + "\n",
                admitted[0]["task"].split("Note from the maintainer running this task:\n", 1)[1],
            )
            self.assertEqual(
                successor_note + "\n",
                admitted[1]["task"].split("Note from the maintainer running this task:\n", 1)[1],
            )
            # Exact task declared for the separately approved successor ingestion.
            self.assertEqual(
                "411aef57a59d8b1d3382a5306954d970fd4f2f0b7fb40824e817ab6bdacbb872",
                hashlib.sha256(admitted[1]["task"].encode()).hexdigest(),
            )
            self.assertEqual(admitted[0]["oracle_sha256"], admitted[1]["oracle_sha256"])
            self.assertEqual(admitted[0]["base_commit"], admitted[1]["base_commit"])
            for ident in ("original", "successor"):
                self.assertEqual(oracle.read_bytes(), (destination / "cases" / ident / "oracle.py").read_bytes())
                self.assertEqual(
                    adapter.read_bytes(),
                    (destination / "cases" / ident / "negative-control/tests/preservation.test.cjs").read_bytes(),
                )
                self.assertEqual(
                    adapter.read_bytes(),
                    (destination / "references" / ident / "tests/preservation.test.cjs").read_bytes(),
                )
                controls = json.loads((destination / "cases" / ident / "controls.json").read_text())
                self.assertFalse(controls["negative"][0]["ok"])
                self.assertTrue(controls["positive"][0]["ok"])
            self.assertEqual(original_bytes, {path: path.read_bytes() for path in original_bytes})

    def test_prepare_rejects_malformed_explicit_note_before_fetch(self):
        for note in (None, False, 1, [], {}, "", " \n\t"):
            with self.subTest(note=note), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                destination = root / "arena"
                with self.assertRaisesRegex(ValueError, "preparation_note must be nonempty text"):
                    prepare.prepare(destination, [{"id": "successor", "preparation_note": note}])
                self.assertEqual([], json.loads((destination / "catalog.json").read_text())["cases"])
                self.assertFalse((destination / "sources").exists())

    def test_shared_fixture_rejects_path_and_symlink_escapes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "cases/original"
            fixture.mkdir(parents=True)
            self.assertEqual(fixture, prepare.fixture_directory(root, "original"))
            for ident in ("../original", "/absolute", ".", "..", "nested/original", None):
                with self.subTest(ident=ident), self.assertRaisesRegex(ValueError, "simple case ID"):
                    prepare.fixture_directory(root, ident)
            outside = root / "outside"
            outside.mkdir()
            (root / "cases/linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "real directory"):
                prepare.fixture_directory(root, "linked")
            with self.assertRaisesRegex(ValueError, "real directory"):
                prepare.fixture_directory(root, "missing")

    def test_prepare_validates_controls_and_keeps_future_fix_out_of_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "upstream"
            repo.mkdir()

            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

            git("init", "-q")
            (repo / "answer.py").write_text('print("broken")\n')
            git("add", ".")
            git("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "baseline")
            base = git("rev-parse", "HEAD")
            (repo / "answer.py").write_text('print("correct")\n')
            git("add", ".")
            git("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "fix")
            fixed = git("rev-parse", "HEAD")
            fixture = root / "cases/demo"
            fixture.mkdir(parents=True)
            (fixture / "issue.json").write_text(json.dumps({"title": "Correct output", "body": "Print correct."}))
            (fixture / "oracle.py").write_text(
                "import json, subprocess, sys\nfrom pathlib import Path\n"
                'result = subprocess.run([sys.executable, "-B", str(Path(sys.argv[1]) / "answer.py")], '
                "capture_output=True, text=True, timeout=5)\n"
                'print(json.dumps({"checks": [{"name": "output", "ok": '
                'result.returncode == 0 and result.stdout == "correct\\n"}]}))\n'
            )
            case = dict(
                id="demo",
                repository=str(repo),
                base_commit=base,
                reference_commit=fixed,
                issue_ref="example/demo#1",
                split="development",
                checks=["output"],
                test_overlay=True,
            )
            overlay = fixture / "test_overlay/tests/preservation.test.cjs"
            overlay.parent.mkdir(parents=True)
            overlay.write_text("// Evaluator-owned preservation adapter, no runtime implementation.\n")
            destination = root / "arena"
            prepare.prepare(destination, [case], catalog_root=root, oracle_timeout=5)
            catalog = json.loads((destination / "catalog.json").read_text())
            self.assertEqual(["demo"], [row["id"] for row in catalog["cases"]])
            controls = json.loads((destination / "cases/demo/controls.json").read_text())
            self.assertFalse(controls["negative"][0]["ok"])
            self.assertTrue(controls["positive"][0]["ok"])
            baseline = destination / "cases/demo/negative-control"
            self.assertEqual('print("broken")\n', (baseline / "answer.py").read_text())
            self.assertEqual(overlay.read_bytes(), (baseline / "tests/preservation.test.cjs").read_bytes())
            self.assertEqual(
                overlay.read_bytes(), (destination / "references/demo/tests/preservation.test.cjs").read_bytes()
            )
            trace = json.loads((destination / "cases/demo/preparation.json").read_text())
            self.assertEqual(base, trace["upstream_base_commit"])
            self.assertEqual(fixed, trace["upstream_reference_commit"])
            self.assertEqual(catalog["cases"][0]["base_commit"], trace["adapted_base_commit"])
            self.assertNotEqual(base, trace["adapted_base_commit"])
            self.assertEqual(
                base,
                subprocess.check_output(
                    ["git", "-C", str(destination / "sources/demo"), "rev-parse", "HEAD^"], text=True
                ).strip(),
            )
            self.assertIn("identical native preservation tests", catalog["cases"][0]["task"])
            self.assertEqual("", subprocess.check_output(["git", "-C", str(baseline), "remote"], text=True))
            with self.assertRaisesRegex(ValueError, "fresh Arena"):
                prepare.prepare(destination, [case], catalog_root=root)
            # A broken reference cannot silently populate the workload catalog.
            with self.assertRaises(subprocess.CalledProcessError):
                prepare.prepare(root / "invalid", [{**case, "reference_commit": base}], catalog_root=root)
            self.assertEqual([], json.loads((root / "invalid/catalog.json").read_text())["cases"])

    def test_overlay_refuses_runtime_paths_and_symlinks_before_changing_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            overlay = root / "overlay"
            source, reference = root / "source", root / "reference"
            source.mkdir()
            reference.mkdir()
            (overlay / "src").mkdir(parents=True)
            runtime = overlay / "src/product.js"
            runtime.write_text("exports.answer = 42;\n")
            with self.assertRaisesRegex(ValueError, "only add test source"):
                prepare.add_test_overlay(overlay, source, reference, "unused")
            runtime.unlink()
            (overlay / "tests").mkdir()
            (overlay / "tests/escape.test.cjs").symlink_to(root / "outside")
            with self.assertRaisesRegex(ValueError, "symlinks"):
                prepare.add_test_overlay(overlay, source, reference, "unused")
            self.assertEqual([], list(source.iterdir()))
            self.assertEqual([], list(reference.iterdir()))

    def test_overlay_does_not_replace_existing_tests_in_either_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, reference, overlay = root / "source", root / "reference", root / "overlay"
            for tree in (source, reference, overlay):
                (tree / "tests").mkdir(parents=True)
            subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
            (source / "product.js").write_text("exports.answer=1;\n")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source),
                    "-c",
                    "user.name=T",
                    "-c",
                    "user.email=t@example.test",
                    "commit",
                    "-qm",
                    "baseline",
                ],
                check=True,
            )
            base = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
            existing = reference / "tests/preservation.test.cjs"
            existing.write_text("existing reference test\n")
            (overlay / "tests/preservation.test.cjs").write_text("replacement\n")
            (overlay / "tests/new.test.cjs").write_text("new test\n")
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                prepare.add_test_overlay(overlay, source, reference, base)
            self.assertEqual("existing reference test\n", existing.read_text())
            self.assertFalse((source / "tests/new.test.cjs").exists())
            self.assertFalse((reference / "tests/new.test.cjs").exists())

    def test_overlay_rejects_file_or_symlink_parents_before_copying(self):
        for parent_kind in ("file", "symlink"):
            with self.subTest(parent_kind=parent_kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, reference, overlay = root / "source", root / "reference", root / "overlay"
                for tree in (source, reference, overlay):
                    tree.mkdir()
                subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
                (source / "product.js").write_text("exports.answer=1;\n")
                subprocess.run(["git", "-C", str(source), "add", "."], check=True)
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(source),
                        "-c",
                        "user.name=T",
                        "-c",
                        "user.email=t@example.test",
                        "commit",
                        "-qm",
                        "baseline",
                    ],
                    check=True,
                )
                base = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
                (overlay / "tests").mkdir()
                (overlay / "tests/preservation.test.cjs").write_text("new preservation test\n")
                if parent_kind == "file":
                    (reference / "tests").write_text("not a directory\n")
                else:
                    (reference / "src").mkdir()
                    (reference / "tests").symlink_to(reference / "src", target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "parents must be real directories"):
                    prepare.add_test_overlay(overlay, source, reference, base)
                self.assertFalse((source / "tests").exists())
                self.assertFalse((reference / "src/preservation.test.cjs").exists())
