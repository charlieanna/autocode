"""Public resume continues owned verification instead of repeating the writer."""

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_acceptance_tests import catalog, fake_setup, materialize, taskrun, without_maintenance


@unittest.skipUnless(sys.platform == "linux", "supervised fake provider requires supported Linux")
class AcceptanceVerificationRetryCliTests(unittest.TestCase):
    def test_reference_launch_failure_retries_checks_with_one_writer_and_exact_plan(self):
        with tempfile.TemporaryDirectory(prefix="acceptance-retry-cli-") as temporary:
            root = Path(temporary)
            scenario = dataclasses.replace(
                catalog.load("feature-stock-refusals"),
                fake_fault="",
                fake_criteria={"C1": "test: test_c1_move_transfers_units"},
            )
            project = without_maintenance(materialize(scenario.seed, root / "project"))
            flags, environment = fake_setup(scenario, root, scenario.dir / "reference")
            paths = environment["PATH"].split(os.pathsep)
            environment.update(
                AUTOCODE_HOME=str(root / "registry"),
                PATH=os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]]),
                PYTHONDONTWRITEBYTECODE="1",
            )
            wrapper = root / "fault-cli.py"
            tools = Path(__file__).resolve().parents[1] / "tools"
            wrapper.write_text(f"""import sys
from pathlib import Path
sys.path.insert(0, {str(tools)!r})
import autocode
import autocode_acceptance_reference as reference
actual = reference.collect
marker = Path({str(root / "failed-once")!r})
def fail_once(*args, **kwargs):
    if not marker.exists():
        marker.write_text('original reference launch failed before execution')
        return {{'error': 'Cannot classify passing tests against the original reference: original-reference execution did not produce complete named results'}}
    return actual(*args, **kwargs)
reference.collect = fail_once
raise SystemExit(autocode.cli())
""")
            run = taskrun.TaskRun.start(
                project,
                scenario.brief,
                options=(*flags, "--joint-planning", "--no-adaptive-planning", "--spec-first"),
                command=(sys.executable, str(wrapper)),
                env=environment,
                timeout=180,
                cwd=root,
            )
            for _ in range(8):
                view = run.advance_until_input()
                need = view["needs"]
                if need["kind"] == "approve_plan":
                    break
                self.assertEqual("answer", need["kind"], view)
                question = need["questions"][0]
                run.answer(
                    question["id"], question.get("proposed_default") or "plan", resolver_token=need["resolver_token"]
                )
            else:
                self.fail("No plan approval reached")
            token = need["token"]
            run.approve_plan(token)
            (run.workspace / "stock.py").write_bytes((scenario.dir / "reference/stock.py").read_bytes())
            run.options = (*run.options, "--pause-after-stage")
            view = run.advance()
            if view["status"] == "PAUSED_REQUESTED" and view["next_stage"] == "orchestrator":
                view = run.resume_paused()
            self.assertEqual("PAUSED_REQUESTED", view["status"], view)
            self.assertEqual("write_acceptance_tests", view["next_stage"], view)
            self.assertTrue((root / "failed-once").is_file())
            view = run.resume_paused()
            logs = list(Path(run.run_dir).glob("iterations/*/acceptance-tests*.jsonl"))
            self.assertEqual(1, len(logs), "local failure must not buy another writing or report-repair call")
            if view["status"] == "PAUSED_REQUESTED" and view["next_stage"] == "orchestrator":
                view = run.resume_paused()
            self.assertEqual("PAUSED_REQUESTED", view["status"], view)
            self.assertEqual("builder", view["next_stage"], view)
            self.assertEqual(token, view["approved_contract"]["token"])
            self.assertEqual("passes candidate; fails original reference", view["acceptance_tests"]["results"]["C1"])
            self.assertFalse(list(Path(run.run_dir).glob("iterations/*/builder-*.jsonl")))
