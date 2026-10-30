"""Discovery evidence reaches a read-only intake without selecting the launch checkout."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_repo_catalog as catalog
import autocode_catalog_context as context


class CatalogContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "home")})
        environment.start()
        self.addCleanup(environment.stop)
        self.repo = self.root / "sources" / "api"
        self.repo.mkdir(parents=True)
        (self.repo / "go.mod").write_text("module example.test/api\n\ngo 1.24\n")
        for args in (
            ["init", "-q"], ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "-c", "commit.gpgsign=false", "commit", "-qm", "base"],
        ):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)
        self.state = {"workspace": str(self.root / "unrelated"), "run_dir": str(self.root / "unrelated/.autocode/runs/task")}

    def test_empty_catalog_has_no_intake_context_or_read_authority(self):
        self.assertIsNone(context.context(self.state))
        self.assertEqual([], context.read_roots(self.state))

    def test_context_pins_all_sources_without_promoting_the_launch_directory(self):
        catalog.add_root(self.repo.parent)
        first = context.context(self.state)
        row = first["repositories"][0]
        self.assertEqual(str(self.repo), row["path"])
        self.assertEqual("api", row["name"])
        self.assertEqual(40, len(row["head"]))
        self.assertIn("read-only", first["instruction"])
        self.assertEqual([str(self.repo)], context.read_roots(self.state))
        self.state["workspace"] = str(self.root / "another-launch")
        self.assertEqual(first, context.context(self.state))
        catalog.remove_root(self.repo.parent)
        with self.assertRaisesRegex(ValueError, "changed"):
            context.context(self.state)

    def test_catalog_citations_require_pinned_regular_source_and_valid_line_bounds(self):
        catalog.add_root(self.repo.parent)
        row = context.context(self.state)["repositories"][0]
        citation = "catalog/" + row["id"] + "/go.mod:1"
        found = context.evidence(self.state, citation)
        self.assertEqual(str(self.repo), found["repository"])
        self.assertEqual(row["head"], found["revision"])
        self.assertEqual("go.mod:1", found["file"])
        for bad in (citation + "00", "catalog/unknown/go.mod", "catalog/" + row["id"] + "/../go.mod"):
            self.assertIsNone(context.evidence(self.state, bad), bad)
        (self.repo / "go.mod").write_text("uncommitted source\n")
        self.assertIsNone(context.evidence(self.state, citation))

    def test_incomplete_inventory_is_a_specific_discovery_failure(self):
        catalog.add_root(self.repo.parent)
        with patch.object(catalog, "discover", return_value={"complete": False, "diagnostics": [{"code": "depth_limit"}]}):
            with self.assertRaisesRegex(ValueError, "depth_limit"):
                context.context(self.state)

    def test_saved_inventory_cannot_add_an_unconfigured_read_root(self):
        catalog.add_root(self.repo.parent)
        context.context(self.state)
        receipt = Path(self.state["run_dir"]) / "planning/source-catalog.json"
        forged = json.loads(receipt.read_text())
        forged["repositories"][0]["path"] = str(self.root)
        receipt.write_text(json.dumps(forged))
        with self.assertRaisesRegex(ValueError, "changed"):
            context.read_roots(self.state)

    def test_initial_investigator_receives_the_catalog(self):
        import autocode_bug_job as bug_job
        catalog.add_root(self.repo.parent)
        self.state.update(task="Fix the unreadable history", settings={})
        data = bug_job.packet(self.state)
        self.assertEqual(str(self.repo), data["source_catalog"]["repositories"][0]["path"])

    def test_catalog_source_citations_can_ground_external_repository_scope(self):
        import autocode_workspace_scope as scope
        catalog.add_root(self.repo.parent)
        row = context.context(self.state)["repositories"][0]
        citation = "catalog/" + row["id"] + "/go.mod:1"
        part = {"target": "api", "work": "Preserve the EPP result", "source_quote": "", "evidence": citation}
        kept, dropped = scope.validated(
            {"spans_repositories": True, "external_parts": [part]}, sources=(),
            source_refs=[citation], workspace=self.state["workspace"],
            evidence_resolver=lambda ref: context.evidence(self.state, ref),
        )
        self.assertEqual([part], kept["external_parts"])
        self.assertEqual([], dropped)

    def test_requirements_handoff_receives_the_same_pinned_source_catalog(self):
        import autocode_stage_context as stage_context
        from tests.test_bug_job import state_for
        catalog.add_root(self.repo.parent)
        state = {**state_for(self.state["workspace"]), "run_dir": self.state["run_dir"]}
        with patch.object(stage_context.support, "snapshot", return_value={"revision": "r1", "head": "h1"}):
            prompt, _ = stage_context.context_packet(state, "requirements_gather", Path(state["run_dir"]) / "state.json")
        data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(str(self.repo), data["source_catalog"]["repositories"][0]["path"])

    def test_spec_first_public_planner_context_receives_the_pinned_catalog(self):
        from units import autoplanner
        from tests.test_bug_job import state_for
        catalog.add_root(self.repo.parent)
        state = {**state_for(self.state["workspace"]), "run_dir": self.state["run_dir"]}
        state["settings"]["spec_first"] = True
        prompt, _ = autoplanner.context(state, "requirements_gather", Path(state["run_dir"]) / "state.json")
        data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(str(self.repo), data["source_catalog"]["repositories"][0]["path"])

    def test_coordinator_carries_the_cited_repository_and_revision_into_approval(self):
        import autocode_coordinate as coordinate
        catalog.add_root(self.repo.parent)
        row = context.context(self.state)["repositories"][0]
        citation = "catalog/" + row["id"] + "/go.mod:1"
        document = {"anchor": self.state["run_dir"], "members": [], "initial_scope": [
            {"target": "api", "source_quote": "", "evidence": citation},
        ]}
        found = coordinate.request_evidence(document, {}, "api")
        self.assertEqual(str(self.repo), found["evidence_repository"])
        self.assertEqual(row["head"], found["evidence_revision"])

    def test_investigator_accepts_validated_catalog_citations_from_an_unrelated_workspace(self):
        import autocode_bug_job as bug_job
        from autocode_workspace_scope_state import current
        from tests.test_bug_job import diagnosis, state_for
        catalog.add_root(self.repo.parent)
        row = context.context(self.state)["repositories"][0]
        part = {"target": "api", "work": "Preserve result metadata", "source_quote": "", "evidence": "catalog/" + row["id"] + "/go.mod:1"}
        state = {**state_for(self.state["workspace"]), "run_dir": self.state["run_dir"]}
        value = diagnosis("not_reproduced", questions=[], workspace_scope={"spans_repositories": True, "external_parts": [part]})
        bug_job.apply(state, value, {"changed_files": []}, state["workspace"])
        self.assertEqual([part], current(state)["external_parts"])

    def test_requirements_accept_catalog_source_refs_without_demanding_launch_repo_evidence(self):
        import autocode_requirements_handoff as handoff
        from tests.test_requirements_handoff import report
        from tests.test_bug_job import state_for
        catalog.add_root(self.repo.parent)
        row = context.context(self.state)["repositories"][0]
        citation = "catalog/" + row["id"] + "/go.mod:1"
        part = {"target": "api", "work": "Preserve result metadata", "source_quote": "", "evidence": citation}
        state = {**state_for(self.state["workspace"]), "run_dir": self.state["run_dir"]}
        value = report(intended_outcome="Readable history", required_behaviors=["Preserve result metadata"],
                       acceptance_tests=["Result code is 1000"], open_questions=[], source_refs=[citation],
                       workspace_scope={"spans_repositories": True, "external_parts": [part]})
        checked = []
        handoff.accept(state, value, {"output": None}, check_code_refs=lambda *args: checked.append(args), queue=lambda *a, **k: None)
        self.assertEqual([part], state["requirements_handoff"]["report"]["workspace_scope"]["external_parts"])
        self.assertEqual([], checked)

    def test_clear_catalog_bug_requirements_continue_to_one_plan_approval_but_keep_real_questions(self):
        import autocode_requirements_review as review
        from tests.test_bug_job import state_for
        from tests.test_requirements_handoff import QUESTION, report
        catalog.add_root(self.repo.parent)
        state = {**state_for(self.state["workspace"]), "run_dir": self.state["run_dir"]}
        state["settings"]["spec_first"] = True
        value = report(intended_outcome="Readable history", required_behaviors=["Preserve result metadata"],
                       acceptance_tests=["Result code is 1000"], open_questions=[])
        state["requirements_handoff"] = {"report": value}
        self.assertEqual([], review.questions(state))
        value["open_questions"] = [QUESTION]
        self.assertEqual([QUESTION], review.questions(state))
