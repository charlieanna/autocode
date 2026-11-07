"""Focused Linux fake-test container command and refusal policy."""

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_linux_fake.py"
SPEC = importlib.util.spec_from_file_location("run_linux_fake", SCRIPT)
assert SPEC and SPEC.loader
linux_fake = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = linux_fake
SPEC.loader.exec_module(linux_fake)


class LinuxFakeContainerTests(unittest.TestCase):
    def parse_error(self, *arguments):
        with contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as stopped:
            linux_fake.parse_selection(list(arguments), repo=ROOT)
        self.assertEqual(2, stopped.exception.code)
        return stderr.getvalue()

    def test_requires_exactly_one_narrow_selector_mode(self):
        self.assertIn("required", self.parse_error())
        self.assertIn(
            "not allowed",
            self.parse_error("--unittest", "tests.test_job_route", "--scenario", "bugfix-go-interval-boundary"),
        )
        for selector in (
            "tests",
            "tests.*",
            "discover",
            "tests/test_job_route.py",
            "tests.test_job_route,tests.test_goals",
        ):
            with self.subTest(selector=selector):
                self.assertIn("discovery and wildcards are refused", self.parse_error("--unittest", selector))
        for scenario in ("*", "all", "bugfix-go-interval-boundary,feature-go-first-package"):
            with self.subTest(scenario=scenario):
                self.assertIn("explicit lowercase catalog ID", self.parse_error("--scenario", scenario))
        self.assertIn("unknown scenario ID", self.parse_error("--scenario", "not-a-real-scenario"))

    def test_refuses_fake_override_and_all_live_provider_flags(self):
        self.assertIn("automatic", self.parse_error("--scenario", "bugfix-go-interval-boundary", "--fake"))
        for flag in linux_fake.LIVE_FLAGS:
            arguments = ["--unittest", "tests.test_job_route", flag]
            if flag in ("--profile", "--provider"):
                arguments.append("anything")
            with self.subTest(flag=flag):
                self.assertIn("live-provider and model-spend flags", self.parse_error(*arguments))

    def test_exact_selected_commands_have_no_live_or_spend_toggle(self):
        unit = linux_fake.selected_command(linux_fake.Selection("unittest", "tests.test_job_route.One.test_case"))
        self.assertEqual(
            [
                "/opt/autocode-venv/bin/python",
                "-m",
                "unittest",
                "tests.test_job_route.One.test_case",
            ],
            unit,
        )
        scenario = linux_fake.selected_command(linux_fake.Selection("scenario", "bugfix-go-interval-boundary"))
        self.assertEqual(
            [
                "/opt/autocode-venv/bin/python",
                "scenarios/run.py",
                "run",
                "bugfix-go-interval-boundary",
                "--fake",
            ],
            scenario,
        )
        for command in (unit, scenario):
            self.assertNotIn("--profile", command)
            self.assertNotIn("--provider", command)
            self.assertNotIn("--i-authorize-live-model-spend", command)

    def test_docker_command_uses_only_the_checkout_mount_and_required_boundary_settings(self):
        command = linux_fake.docker_run_prefix(ROOT)
        self.assertIn("--read-only", command)
        user = command.index("--user")
        uid, gid = linux_fake.host_identity()
        self.assertEqual(f"{uid}:{gid}", command[user + 1])
        self.assertNotEqual("0:0", command[user + 1])
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--security-opt=no-new-privileges:true", command)
        self.assertIn("--security-opt=seccomp=unconfined", command)
        # #2373: only the OUTER container's system-path masks are removed, so the inner
        # bwrap sandbox can mount its own fresh /proc in its new PID namespace (Docker's
        # default masks make the kernel refuse that mount); every inner boundary setting
        # above and below is unchanged, and no broader mount or live flag is accepted.
        self.assertIn("--security-opt=systempaths=unconfined", command)
        self.assertIn("--pid=host", command)
        self.assertIn("--network=none", command)
        self.assertNotIn("--privileged", command)
        self.assertFalse(any(argument.startswith("--cap-add") for argument in command))
        self.assertFalse(any(argument.startswith("-v") or argument.startswith("--volume") for argument in command))

        mounts = [command[index + 1] for index, argument in enumerate(command) if argument == "--mount"]
        self.assertEqual([f"type=bind,source={ROOT.resolve()},target=/workspace"], mounts)
        joined = " ".join(command).lower()
        for credential_path in (".ssh", ".aws", ".config", "credentials", "docker.sock"):
            self.assertNotIn(credential_path, joined)
        self.assertNotIn("target=/home", joined)

    def test_build_context_cannot_bake_the_checkout_or_credentials(self):
        command = linux_fake.docker_build_command(ROOT)
        self.assertEqual(str(ROOT / "scripts"), command[-1])
        self.assertEqual(str(ROOT / "scripts" / "linux-fake.Dockerfile"), command[4])
        dockerfile = (ROOT / "scripts" / "linux-fake.Dockerfile").read_text()
        instructions = [line.split(maxsplit=1)[0].upper() for line in dockerfile.splitlines() if line.strip()]
        self.assertNotIn("COPY", instructions)
        self.assertNotIn("ADD", instructions)
        self.assertIn("FROM ubuntu:24.04@sha256:", dockerfile)
        self.assertIn("ARG GO_VERSION=1.25.5", dockerfile)
        # Go's archives are checked against pinned hashes: go.dev's .sha256 URLs answer with HTML (#1878).
        self.assertIn(
            "ARG GO_SHA256_AMD64=9e9b755d63b36acf30c12a9a3fc379243714c1c6d3dd72861da637f336ebb35b", dockerfile
        )
        self.assertIn(
            "ARG GO_SHA256_ARM64=b00b694903d126c588c378e72d3545549935d3982635ba3f7a964c9fa23fe3b9", dockerfile
        )
        self.assertNotIn("${go_archive}.sha256", dockerfile)
        self.assertIn("ARG NODE_VERSION=22.18.0", dockerfile)
        self.assertIn("python3.11", dockerfile)


if __name__ == "__main__":
    unittest.main()
