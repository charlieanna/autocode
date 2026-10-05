"""MultiComponentBuild.integrate against real Git worktrees, with no model run.

Each component's work is an uncommitted change in a real worktree, as a finished
component run leaves it; only the run's outcome is stood in for (a done view).
The CLI-level regression for the same behaviour is
CliTests.test_cli_integrates_into_the_same_target_twice in test_multicomponent.
"""
import dataclasses
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_multicomponent as mc


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.test", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


class IntegrateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="multicomponent-integrate-")
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve() / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "README.md").write_text("base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()
        self.components = {}

    def commit(self, files):
        """Commit ``files`` on the repository's HEAD; components started after this are built on it."""
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "more")
        self.base = git(self.repo, "rev-parse", "HEAD").strip()

    def finished(self, cid, files):
        """A component whose worktree holds ``files`` as uncommitted changes and whose run is done."""
        component = mc.Component(id=cid, description=f"the {cid} component", requirements=("R1",),
                                 depends_on=(), publishes_contracts=(), consumes_contracts=())
        workspace = self.repo / ".autocode-components" / cid
        git(self.repo, "worktree", "add", "-q", "-b", f"components/{cid}", str(workspace), self.base)
        for relative, content in files.items():
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.components[cid] = mc.ComponentResult(component, workspace, self.base, view={"done": True})
        return workspace

    def build(self):
        architecture = mc.Architecture(components={cid: r.component for cid, r in self.components.items()})
        build = mc.MultiComponentBuild(self.repo, architecture)
        build.results = {cid: dataclasses.replace(result) for cid, result in self.components.items()}
        return build

    def target(self, name="integration", commit="HEAD"):
        path = self.repo / name
        git(self.repo, "worktree", "add", "-q", "--detach", str(path), commit)
        return path

    def snapshot(self, target):
        return {path.relative_to(target).as_posix(): path.read_text()
                for path in sorted((target / "components").rglob("*")) if path.is_file()}

    def test_integrating_twice_into_the_same_target_changes_nothing_the_second_time(self):
        self.finished("alpha", {"components/alpha/app.py": "print('alpha')\n"})
        self.finished("beta", {"components/beta/app.py": "print('beta')\n"})
        target = self.target()
        first = self.build().integrate(target)
        self.assertEqual((["alpha", "beta"], [], None), (first["integrated"], first["already_applied"],
                                                         first["failed"]))
        before = self.snapshot(target)

        second = self.build().integrate(target)
        self.assertIsNone(second["failed"], second.get("detail"))
        self.assertEqual(["alpha", "beta"], second["integrated"])
        self.assertEqual(["alpha", "beta"], second["already_applied"])
        self.assertEqual(before, self.snapshot(target))

    def test_a_component_finished_after_an_earlier_integration_is_added_to_the_same_target(self):
        # The resume case: the first run integrated what was done, another component
        # finished later, and the same command is run again.
        self.finished("alpha", {"components/alpha/app.py": "print('alpha')\n"})
        self.finished("beta", {"components/beta/app.py": "print('beta')\n"})
        target = self.target()
        waiting = self.build()
        waiting.results["beta"].view = {"done": False}
        self.assertEqual(["alpha"], waiting.integrate(target)["integrated"])

        outcome = self.build().integrate(target)
        self.assertIsNone(outcome["failed"], outcome.get("detail"))
        self.assertEqual((["alpha", "beta"], ["alpha"]), (outcome["integrated"], outcome["already_applied"]))
        self.assertEqual("print('beta')\n", (target / "components" / "beta" / "app.py").read_text())

    def test_a_target_whose_integrated_file_was_edited_is_refused(self):
        self.finished("alpha", {"components/alpha/app.py": "print('alpha')\n"})
        target = self.target()
        self.assertIsNone(self.build().integrate(target)["failed"])
        edited = target / "components" / "alpha" / "app.py"
        edited.write_text("print('edited in the target')\n")

        outcome = self.build().integrate(target)
        self.assertEqual("alpha", outcome["failed"])
        self.assertEqual(([], []), (outcome["integrated"], outcome["already_applied"]))
        self.assertIn("components/alpha/app.py", outcome["detail"])
        self.assertIn("integrate into a new target", outcome["detail"])
        self.assertEqual("print('edited in the target')\n", edited.read_text())
        # The advice works: a new target takes alpha.
        self.assertIsNone(self.build().integrate(self.target("integration2"))["failed"])

    def test_a_fresh_target_reports_nothing_already_applied(self):
        self.finished("alpha", {"components/alpha/app.py": "print('alpha')\n"})
        target = self.target()
        outcome = self.build().integrate(target)
        self.assertEqual({"target": str(target), "integrated": ["alpha"], "already_applied": [], "failed": None},
                         outcome)
        self.assertEqual("print('alpha')\n", (target / "components" / "alpha" / "app.py").read_text())

    def test_no_finished_component_still_reports_already_applied(self):
        self.finished("alpha", {"components/alpha/app.py": "print('alpha')\n"})
        build = self.build()
        build.results["alpha"].view = {"done": False}
        outcome = build.integrate(self.target())
        self.assertEqual(([], [], None), (outcome["integrated"], outcome["already_applied"], outcome["failed"]))

    def test_an_ownership_violation_is_still_refused_even_when_already_applied(self):
        self.finished("alpha", {"components/alpha/app.py": "print('alpha')\n", "shared/leak.txt": "leaked\n"})
        target = self.target()
        # Put the same change in the target by hand, so a content check alone would pass.
        (target / "components" / "alpha").mkdir(parents=True)
        (target / "components" / "alpha" / "app.py").write_text("print('alpha')\n")
        (target / "shared").mkdir()
        (target / "shared" / "leak.txt").write_text("leaked\n")

        outcome = self.build().integrate(target)
        self.assertEqual("alpha", outcome["failed"])
        self.assertIn("outside components/alpha/", outcome["detail"])
        self.assertEqual(([], []), (outcome["integrated"], outcome["already_applied"]))


    def test_rerunning_a_component_that_edits_committed_files_does_not_apply_it_twice(self):
        # Both edits apply a second time at an offset in Git's eyes: the new route ends
        # like the route before it, and the edited stanza has an unedited twin.
        routes = ['@app.route("/users")\ndef users():\n    rows = query("users")\n    return jsonify(rows)\n',
                  '@app.route("/items")\ndef items():\n    rows = query("items")\n    return jsonify(rows)\n']
        stanza = "".join(f"k{n}=v\n" for n in range(7))
        self.commit({"components/alpha/app.py": "app = Flask(__name__)\n\n\n" + "\n\n".join(routes),
                     "components/alpha/conf.ini": f"[first]\n{stanza}\n[second]\n{stanza}"})
        order = '@app.route("/orders")\ndef orders():\n    rows = query("orders")\n    return jsonify(rows)\n'
        built = {"components/alpha/app.py": "app = Flask(__name__)\n\n\n" + "\n\n".join([routes[0], order, routes[1]]),
                 "components/alpha/conf.ini": f"[first]\n{stanza.replace('k3=v', 'k3=CHANGED')}\n[second]\n{stanza}"}
        self.finished("alpha", built)
        target = self.target()
        self.assertEqual([], self.build().integrate(target)["already_applied"])

        for attempt in (2, 3):
            outcome = self.build().integrate(target)
            self.assertEqual((["alpha"], ["alpha"], None),
                             (outcome["integrated"], outcome["already_applied"], outcome["failed"]), attempt)
            self.assertEqual(built, self.snapshot(target))

    def test_a_target_already_holding_part_of_a_component_is_refused_untouched(self):
        self.commit({"components/alpha/conf.ini": "[first]\nk=v\n", "components/alpha/app.py": "v0\n"})
        self.finished("alpha", {"components/alpha/conf.ini": "[first]\nk=CHANGED\n",
                                "components/alpha/app.py": "v1\n"})
        target = self.target()
        (target / "components" / "alpha" / "app.py").write_text("v1\n")

        outcome = self.build().integrate(target)
        self.assertEqual("alpha", outcome["failed"])
        self.assertIn("already holds alpha's version of ['components/alpha/app.py']", outcome["detail"])
        self.assertEqual({"components/alpha/app.py": "v1\n", "components/alpha/conf.ini": "[first]\nk=v\n"},
                         self.snapshot(target))

    def test_a_lost_exec_bit_is_not_reported_as_already_applied(self):
        workspace = self.finished("alpha", {"components/alpha/run.sh": "#!/bin/sh\necho alpha\n"})
        (workspace / "components" / "alpha" / "run.sh").chmod(0o755)
        target = self.target()
        self.assertIsNone(self.build().integrate(target)["failed"])
        script = target / "components" / "alpha" / "run.sh"
        self.assertTrue(script.stat().st_mode & 0o100)
        script.chmod(0o644)

        outcome = self.build().integrate(target)
        self.assertEqual(("alpha", []), (outcome["failed"], outcome["already_applied"]))
        self.assertIn("components/alpha/run.sh", outcome["detail"])

    def test_the_apply_whitespace_setting_neither_refuses_nor_rewrites_a_component(self):
        line = "x = 1   \n"  # trailing whitespace, which apply.whitespace=error refuses and =fix strips
        self.finished("alpha", {"components/alpha/app.py": line})
        for setting in ("error", "fix"):
            with self.subTest(setting=setting):
                git(self.repo, "config", "apply.whitespace", setting)
                target = self.target(f"integration-{setting}")
                outcome = self.build().integrate(target)
                self.assertIsNone(outcome["failed"], outcome.get("detail"))
                self.assertEqual(line, (target / "components" / "alpha" / "app.py").read_text())
                self.assertEqual(["alpha"], self.build().integrate(target)["already_applied"])

    def test_a_target_at_an_older_commit_gets_advice_that_works(self):
        older = self.base
        self.commit({"components/alpha/conf.txt": "v0\n"})
        self.finished("alpha", {"components/alpha/conf.txt": "v1\n"})

        outcome = self.build().integrate(self.target("old", older))
        self.assertEqual("alpha", outcome["failed"])
        self.assertIn("differs at ['components/alpha/conf.txt'] from the commit alpha was built on", outcome["detail"])
        self.assertIn("integrate into a new target", outcome["detail"])
        fresh = self.target()
        self.assertIsNone(self.build().integrate(fresh)["failed"])
        self.assertEqual("v1\n", (fresh / "components" / "alpha" / "conf.txt").read_text())

    def test_when_head_changed_the_component_files_a_new_target_is_not_the_advice(self):
        self.commit({"components/alpha/app.py": "v0\n"})
        self.finished("alpha", {"components/alpha/app.py": "v1\n"})
        self.commit({"components/alpha/app.py": "v0b\n"})  # HEAD moves on while alpha builds

        for name in ("integration", "another-new-target"):
            outcome = self.build().integrate(self.target(name))
            self.assertEqual("alpha", outcome["failed"])
            self.assertNotIn("integrate into a new target", outcome["detail"])
            self.assertIn("HEAD changed ['components/alpha/app.py']", outcome["detail"])
            self.assertIn("rebuild", outcome["detail"])

    def test_a_file_moved_into_the_component_directory_is_refused(self):
        self.commit({"shared/lib.py": "shared\n"})
        workspace = self.finished("alpha", {})
        (workspace / "components" / "alpha").mkdir(parents=True)
        (workspace / "shared" / "lib.py").rename(workspace / "components" / "alpha" / "lib.py")
        target = self.target()

        outcome = self.build().integrate(target)
        self.assertEqual("alpha", outcome["failed"])
        self.assertIn("changed files outside components/alpha/: ['shared/lib.py']", outcome["detail"])
        self.assertEqual("shared\n", (target / "shared" / "lib.py").read_text())
        self.assertFalse((target / "components").exists())

    def test_a_non_ascii_file_name_inside_the_component_is_integrated(self):
        self.finished("alpha", {"components/alpha/caf\u00e9.txt": "menu\n"})
        target = self.target()
        outcome = self.build().integrate(target)
        self.assertEqual((["alpha"], None), (outcome["integrated"], outcome["failed"]), outcome.get("detail"))
        self.assertEqual({"components/alpha/caf\u00e9.txt": "menu\n"}, self.snapshot(target))
        self.assertEqual(["alpha"], self.build().integrate(target)["already_applied"])

    def test_a_target_that_is_not_the_top_of_a_worktree_of_the_repository_is_refused(self):
        self.finished("alpha", {"components/alpha/app.py": "print('alpha')\n"})
        subdirectory = self.repo / "out" / "combined"
        subdirectory.mkdir(parents=True)
        other = self.repo.parent / "other"
        other.mkdir()
        git(other, "init", "-q")
        for target in (subdirectory, self.repo / "missing", other):
            with self.subTest(target=target.name):
                with self.assertRaisesRegex(mc.ArchitectureError, "not the top directory of a worktree"):
                    self.build().integrate(target)
        self.assertEqual([], list(subdirectory.iterdir()))
        self.assertFalse((self.repo / "components").exists())
        self.assertEqual([".git"], [path.name for path in other.iterdir()])


if __name__ == "__main__":
    unittest.main()
