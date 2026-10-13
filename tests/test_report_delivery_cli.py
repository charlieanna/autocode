"""Report-delivery prompt must match how the tool persists the final message.

Live gocode-cross runs (docs/bugs/2026-10-12-gocode-cross-product-fixes.md) asked
the model to write the report with a tool while ``--sandbox read-only`` forbade
writes. Requirements then returned an empty contract with a REPORT_WRITE blocker.
When the provider argv exposes ``-o {report}``, Autocode must ask for a final
response instead. SCENARIO_FAKE_READONLY_REPORT reproduces the live shape.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


class ReportDeliveryCLI(unittest.TestCase):
    def setUp(self):
        from scenarios import run  # noqa: F401, I001 - initialize the harness before importing it
        from harness import catalog

        results = Path(__file__).resolve().parents[1] / ".scenario-runs"
        results.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(prefix="report-delivery-", dir=results)
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.scenario = catalog.load("greenfield-greeting-cli")
        for name in ("config", "codex-home"):
            (self.root / name).mkdir()

    def drive(self, *, persist_final_message: bool):
        from harness import hybrid, profiles
        from harness.driver import Driver, default_autocode, fake_setup
        from harness.project import materialize

        self.project = materialize(self.scenario.seed, self.root / "project")
        flags, env = fake_setup(self.scenario, self.root, self.scenario.reference)
        providers = self.root / "config" / "autocode" / "providers"
        providers.mkdir(parents=True)
        fake = [sys.executable, str(self.root / "bin" / "codex")]
        if persist_final_message:
            tool = {
                **hybrid.standin_tool(fake),
                "command": [
                    *fake,
                    "exec",
                    "--model",
                    "{model}",
                    "--sandbox",
                    "{sandbox}",
                    "--output-schema",
                    "{schema}",
                    "-o",
                    "{report}",
                    "-",
                ],
            }
        else:
            # Same shape as the broken gocode.toml: -o / schema / sandbox live only
            # inside the sh -c string, so Autocode cannot see -o {report} and still
            # asks for a tool write. The fake still receives those flags after expansion.
            # $0/$1 are the stand-in interpreter and script; -o is not an argv neighbor
            # of {report} in this template (persists_final_message stays false).
            tool = {
                **hybrid.standin_tool(fake),
                "command": [
                    "sh",
                    "-c",
                    'exec "$0" "$1" exec --model "$2" --sandbox "$3" --output-schema "$4" -o "$5" -',
                    *fake,
                    "{model}",
                    "{sandbox}",
                    "{schema}",
                    "{report}",
                ],
            }
        (providers / f"{hybrid.STANDIN}.toml").write_text(hybrid.toml(tool))
        flags = profiles.flags({"provider": hybrid.STANDIN, "models": hybrid.STANDIN_MODELS})
        env.update(
            XDG_CONFIG_HOME=str(self.root / "config"),
            CODEX_HOME=str(self.root / "codex-home"),
            SCENARIO_FAKE_READONLY_REPORT="1",
        )
        self.driver = Driver(
            self.project, self.root, flags, env, autocode=default_autocode(), max_steps=30, timeout_seconds=300
        )
        return self.driver.drive(self.scenario.brief)

    def discovery_reports(self):
        reports = []
        for row in self.driver.state().get("stages") or []:
            if row.get("stage") not in ("astra_discovery", "astra_discovery_report_repair"):
                continue
            path = Path(row.get("output") or "")
            if path.is_file():
                reports.append(json.loads(path.read_text()))
        return reports

    def test_final_message_persistence_completes_without_report_write_blocker(self):
        view = self.drive(persist_final_message=True)
        self.assertEqual("TASK_COMPLETE", view.get("status"), view.get("stop_reason"))
        for report in self.discovery_reports():
            self.assertEqual([], report.get("access_blockers") or [])
            criteria = (report.get("contract") or {}).get("acceptance_criteria") or []
            self.assertTrue(criteria, "discovery kept a substantive contract")
        state = self.driver.state()
        self.assertNotIn("REPORT_WRITE", state.get("answers") or {})
        self.assertFalse(any(q.get("id") == "REPORT_WRITE" for q in state.get("pending_questions") or []))

    def test_tool_write_prompt_under_readonly_stops_honestly(self):
        view = self.drive(persist_final_message=False)
        self.assertNotEqual("TASK_COMPLETE", view.get("status"), view.get("stop_reason"))
        self.assertFalse(view.get("done"))
        # The fault planted the live empty-contract shape at least once.
        planted = [report for report in self.discovery_reports() if report.get("access_blockers")]
        self.assertTrue(planted, "readonly report fault did not fire")
        self.assertTrue(any(not ((r.get("contract") or {}).get("acceptance_criteria") or []) for r in planted))


if __name__ == "__main__":
    unittest.main()
