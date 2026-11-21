"""Kilo's whole-client Seatbelt policy and the verification copy it must keep from being written (#3812)."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_kilo_isolation as isolation
import autocode_tool_containment as containment
import autocode_util as util
import autocode_verification_copy as copies

SEATBELT = sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file()


class ClientPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.target = self.root / ".autocode/runs/fake/native-kilo-owned"
        self.target.mkdir(parents=True)
        self.control = self.root / ".autocode/tool-containment-owned"
        self.scratch = self.control / "scratch"
        self.tree = self.scratch / "verification"
        (self.tree / "src").mkdir(parents=True)
        self.source = self.tree / "src/input.py"
        self.source.write_bytes(b"protected copied source\n")
        self.grant = '(allow file-write* (subpath "' + str(self.scratch) + '"))'

    def boundary(self, **manifest):
        value = {
            "version": 1,
            "workspace": str(self.root),
            "tree": str(self.tree),
            "files": {"src/input.py": util.file_hash(self.source)},
            "directories": ["src"],
            **manifest,
        }
        path = self.control / "verification-copy.json"
        path.write_text(json.dumps(value))
        return {
            "workspace": str(self.root),
            "profile": str(self.control / "policy.sb"),
            "scratch": str(self.scratch),
            "verification_copy": str(path),
            "verification_copy_sha256": util.file_hash(path),
        }

    def policy(self, boundary):
        environment = {}
        if not (self.scratch / "kilo-runtime").exists():
            isolation._relocate_client_runtime(boundary, environment)
        else:
            for key, name in isolation.MUTABLE_DIRECTORIES.items():
                environment[key] = str(self.scratch / "kilo-runtime" / name)
        request = {"read_roots": [], "protected_paths": []}
        return isolation.whole_client_policy(
            self.root, self.target, boundary, request, environment, str(self.root / "source.sock")
        )

    def literal(self, path):
        return '(deny file-write* (literal "' + str(path) + '"))'

    def test_a_sealed_copy_is_denied_by_one_rule_written_after_the_scratch_grant(self):
        boundary = self.boundary(seal=copies.SEAL)
        lines = self.policy(boundary).splitlines()
        rule = containment.sealed_rule(self.tree)
        self.assertEqual([self.literal(self.tree), rule], isolation._verification_denials(boundary))
        # Seatbelt takes the last matching rule and the copy lives in the granted scratch, so a denial
        # written before the grant is undone by it.
        self.assertEqual(1, lines.count(self.grant))
        for denial in (self.literal(self.tree), rule):
            self.assertEqual(1, lines.count(denial))
            self.assertGreater(lines.index(denial), lines.index(self.grant))
        # One rule for the whole copy: no line names a path inside it.
        self.assertEqual([], [line for line in lines if str(self.tree) + "/" in line])
        self.assertTrue(isolation._verification_inputs_denied("\n".join(lines), boundary))

    def test_an_unsealed_copy_keeps_a_denial_for_each_path_after_the_scratch_grant(self):
        boundary = self.boundary()
        lines = self.policy(boundary).splitlines()
        expected = [self.literal(path) for path in (self.tree, self.tree / "src", self.source)]
        self.assertEqual(expected, isolation._verification_denials(boundary))
        for denial in expected:
            self.assertGreater(lines.index(denial), lines.index(self.grant))
        self.assertNotIn("file-mode", "\n".join(lines))

    def test_a_policy_whose_denials_precede_the_grant_or_are_missing_is_refused(self):
        boundary = self.boundary(seal=copies.SEAL)
        text = self.policy(boundary)
        rule = containment.sealed_rule(self.tree)
        moved = [line for line in text.splitlines() if line != rule]
        moved.insert(moved.index(self.grant), rule)
        for name, weakened in {
            "the rule before the grant": "\n".join(moved),
            "the rule missing": text.replace(rule + "\n", ""),
            "a later grant over the scratch": text + self.grant + "\n",
        }.items():
            with self.subTest(name):
                self.assertFalse(isolation._verification_inputs_denied(weakened, boundary))
        self.assertTrue(isolation._verification_inputs_denied(text, boundary))

    def test_the_run_denial_leaves_the_handoffs_context_artifacts_readable(self):
        # #3907: a reviewer's handoff names a context artifact in its run and tells it to read it;
        # the client policy denies the rest of .autocode, sibling runs and this run's state included.
        lines = self.policy(self.boundary()).splitlines()
        run = self.target.parent
        denials = [line for line in lines if line.startswith('(deny file-read* (require-all (subpath "')]
        self.assertEqual(1, len(denials))
        self.assertTrue(
            denials[0].startswith('(deny file-read* (require-all (subpath "' + str(self.root / ".autocode"))
        )
        exceptions = ['(require-not (subpath "' + str(path) + '"))' for path in (self.target, self.control)]
        self.assertIn('(require-not (subpath "' + str(run / "context") + '"))', denials[0])
        for exception in exceptions:
            self.assertIn(exception, denials[0])
        # Nothing else in the run, nor the run itself, is excepted.
        self.assertEqual(3, denials[0].count("(require-not"))
        self.assertNotIn('(subpath "' + str(run) + '"))', denials[0])

    def test_a_manifest_with_another_seal_is_refused(self):
        boundary = self.boundary(seal="unknown")
        with self.assertRaisesRegex(ValueError, "verification copy seal changed"):
            isolation._verification_denials(boundary)

    def test_a_boundary_without_a_copy_adds_no_denial(self):
        boundary = self.boundary()
        del boundary["verification_copy"], boundary["verification_copy_sha256"]
        self.assertEqual([], isolation._verification_denials(boundary))
        self.assertEqual([], isolation._verification_protected_paths(boundary))

    def test_native_configuration_uses_discovered_roots_and_links(self):
        owner = isolation.KiloRoleIsolation.__new__(isolation.KiloRoleIsolation)
        owner._root = self.root
        owner._prepared = {"target": self.target}
        request = {"read_roots": ["/existing"], "protected_paths": [], "tool_commands": ["python -V"]}
        selected = {"read_roots": ["/runtime"], "links": [["/alias", "/runtime"]]}
        with (
            patch.object(isolation.toolchain, "discover", return_value=selected) as discover,
            patch.object(containment, "prepare", side_effect=RuntimeError("observed")) as prepare,
            self.assertRaisesRegex(RuntimeError, "observed"),
        ):
            owner._configure([], {"PATH": "/bin"}, request)
        discover.assert_called_once_with(self.root, ["python -V"], {"PATH": "/bin"})
        self.assertEqual(["/existing", "/runtime"], prepare.call_args.kwargs["read_roots"])
        self.assertEqual(selected["links"], prepare.call_args.kwargs["links"])
        self.assertEqual(["/existing"], request["read_roots"])

    def test_native_client_receives_the_same_discovered_roots_as_the_shell(self):
        owner = isolation.KiloRoleIsolation.__new__(isolation.KiloRoleIsolation)
        owner._root = self.root
        owner._prepared = {
            "target": self.target,
            "agent": "fixture",
            "files": {},
            "prompt_file": None,
            "os_readonly_inputs": (),
        }
        owner._source = SimpleNamespace(socket_path=self.root / "source.sock")
        owner._image_preparation = None
        shell = self.control / "shell"
        shell.write_text("fixture")
        fake_boundary = {"shell": str(shell), "profile": str(self.control / "policy.sb")}
        selected = {"read_roots": ["/runtime"], "links": [["/alias", "/runtime"]]}
        request = {"read_roots": ["/existing"], "protected_paths": []}
        child = {"PATH": "/bin", isolation.CONFIG: json.dumps({"agent": {"fixture": {}}})}
        with (
            patch.object(isolation.toolchain, "discover", return_value=selected),
            patch.object(containment, "prepare", return_value=fake_boundary) as prepare,
            patch.object(isolation, "_relocate_client_runtime", return_value=self.scratch),
            patch.object(owner, "_prepare_derivation_policy"),
            patch.object(isolation, "_inherited_shell_body", return_value=b"fixture"),
            patch.object(isolation, "_write"),
            patch.object(isolation, "whole_client_policy", side_effect=RuntimeError("observed")) as client,
            self.assertRaisesRegex(RuntimeError, "observed"),
        ):
            owner._configure([], child, request)
        client_request = client.call_args.args[3]
        self.assertEqual(prepare.call_args.kwargs["read_roots"], client_request["read_roots"])
        self.assertEqual(selected["links"], client_request["runtime_links"])


@unittest.skipUnless(SEATBELT, "requires real macOS Seatbelt enforcement")
class ClientPolicyKernelTests(unittest.TestCase):
    """What the kernel does under the client policy: Kilo's own Bash child runs under this profile alone."""

    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".autocode"
        parent.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="kilo-isolation-tests-", dir=parent)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve() / "project"
        (self.root / "pkg").mkdir(parents=True)
        (self.root / "pkg/module.py").write_text("VALUE = 1\n")
        (self.root / "alias.py").symlink_to("pkg/module.py")
        git = ["/usr/bin/git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        for arguments in (["init", "-q"], ["add", "."], ["commit", "-qm", "fixture"]):
            subprocess.run([*git, *arguments], check=True, capture_output=True)
        roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()]
        environment = {**os.environ, "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin"}
        request = {"read_roots": roots, "protected_paths": [], "runtime_links": []}
        if hasattr(isolation, "runtime_request"):
            request = isolation.runtime_request(self.root, request, environment)
        self.boundary = containment.prepare(
            self.root,
            read_roots=request["read_roots"],
            links=request["runtime_links"],
            environment=environment,
            verification_copy=True,
        )
        self.scratch = Path(self.boundary["scratch"])
        self.tree = self.scratch / "verification"
        target = self.root / ".autocode/runs/fake/native-kilo-owned"
        target.mkdir(parents=True)
        child = {}
        isolation._relocate_client_runtime(self.boundary, child)
        text = isolation.whole_client_policy(
            self.root, target, self.boundary, request, child, str(self.root / "source.sock")
        )
        self.profile = target / "client.sb"
        self.profile.write_text(text)

    def run_under_policy(self, command):
        return subprocess.run(
            ["/usr/bin/sandbox-exec", "-f", str(self.profile), "/bin/sh", "-c", command],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_python_can_load_its_runtime_dependencies_in_client_policy(self):
        result = self.run_under_policy(shlex.join([sys.executable, "-c", "import ssl; print('ready')"]))
        self.assertEqual((0, "ready\n"), (result.returncode, result.stdout), result.stderr)

    def test_the_client_policy_refuses_every_change_to_a_copied_input_and_allows_new_outputs(self):
        module, package, alias = self.tree / "pkg/module.py", self.tree / "pkg", self.tree / "alias.py"
        quoted = {name: shlex.quote(str(path)) for name, path in (("m", module), ("p", package), ("a", alias))}
        new = shlex.quote(str(self.tree / "pkg/new.py"))
        changes = [
            "echo changed >> " + quoted["m"],
            ": > " + quoted["m"],
            "mv " + quoted["m"] + " " + quoted["m"] + ".moved",
            "mv " + quoted["p"] + " " + quoted["p"] + ".moved",
            "mv " + quoted["a"] + " " + quoted["a"] + ".moved",
            "chmod 600 " + quoted["m"],
            "touch " + quoted["m"],
            "echo other > " + new + ".tmp && mv " + new + ".tmp " + quoted["m"],
            "mv " + shlex.quote(str(self.tree)) + " " + shlex.quote(str(self.scratch / "moved")),
        ]
        for command in changes:
            with self.subTest(command=command):
                result = self.run_under_policy(command)
                self.assertNotEqual(0, result.returncode, result.stdout)
                self.assertIn("Operation not permitted", result.stderr)
        self.assertEqual("VALUE = 1\n", module.read_text())
        self.assertEqual("pkg/module.py", os.readlink(alias))
        # The copy's manifest still describes it: nothing a capture reads has changed.
        copies.execution(self.boundary["verification_copy"], self.boundary["verification_copy_sha256"], self.root)
        # A check may still make, change and move its own outputs beside the sources.
        for command in (
            "echo built > " + new,
            "echo more >> " + new,
            "mv " + new + " " + new + ".renamed",
            "mkdir " + shlex.quote(str(self.tree / "build")),
            ": > " + shlex.quote(str(self.scratch / "note")),
        ):
            with self.subTest(command=command):
                result = self.run_under_policy(command)
                self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
