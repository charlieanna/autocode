"""Pure policy checks and real kernel/tool boundary conformance (macOS Seatbelt or Linux bubblewrap)."""

import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_tool_containment as containment
import autocode_toolchain as toolchain
from providers import opencode

ROOT = Path(__file__).resolve().parents[1]
# The kernel tests run wherever this host's backend can enforce (#709); elsewhere they skip.
NATIVE_PROBLEM = containment.sandbox_problem()
NATIVE = None if NATIVE_PROBLEM else containment.backend()
# How each backend reports a denied operation. bwrap mounts make a protected write fail
# read-only, a hard link cross-device, and an unmounted path or network absent.
DENIED = {
    containment.SEATBELT: "Operation not permitted|PermissionError|Permission denied",
    containment.BUBBLEWRAP: "Read-only file system|Invalid cross-device link|No such file or directory"
    "|Network is unreachable|Permission denied|Operation not permitted",
}.get(NATIVE, "^$")


class PolicyTests(unittest.TestCase):
    def test_unicode_authority_paths_are_literal_with_quotes_and_backslashes_escaped(self):
        root = Path('/workspace/josé-用户-"quoted"-\\path')
        protected = root / "保護.txt"
        profile = containment.policy(
            root, root / ".autocode/stage/scratch", allow_write=True, protected_paths=[protected]
        )
        quoted = json.dumps(str(root), ensure_ascii=False)
        self.assertIn("(subpath " + quoted + ")", profile)
        self.assertIn("(literal " + json.dumps(str(protected), ensure_ascii=False) + ")", profile)
        self.assertNotIn("\\u", profile)

    def test_deny_default_has_no_whole_home_tmp_or_network_grant(self):
        profile = containment.policy("/workspace", "/workspace/.autocode/stage/scratch")
        self.assertIn("(deny default)", profile)
        self.assertNotIn("(allow default)", profile)
        self.assertNotIn('(subpath "/tmp")', profile)
        self.assertNotIn("(allow network", profile)
        self.assertNotIn('(subpath "/Users")', profile)
        self.assertIn('(literal "/")', profile)

    def test_builder_excludes_runner_and_config_authority(self):
        profile = containment.policy(
            "/workspace", "/workspace/.autocode/stage/scratch", allow_write=True, protected_paths=["/workspace/runtime"]
        )
        for path in (".autocode", ".git", ".opencode", "opencode.json", "runtime"):
            self.assertIn('(require-not (subpath "/workspace/' + path + '"))', profile)

    def test_unscoped_read_or_write_authority_rejected(self):
        for scratch in ("/tmp/escape", "/workspace/.autocode"):
            with self.assertRaises(ValueError):
                containment.policy("/workspace", scratch)
        for root in ("/", str(Path.home())):
            with self.assertRaises(ValueError):
                containment.policy("/workspace", "/workspace/.autocode/stage/scratch", read_roots=[root])

    def test_planning_profile_denies_the_run_store_after_every_read_allowance(self):
        # #1250: a readonly planning shell reads no run's private state, only its own planning/ artifacts.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runs, scratch = root / ".autocode/runs", root / ".autocode/tool-containment-stage/scratch"
            (runs / "own/planning").mkdir(parents=True)
            scratch.mkdir(parents=True)
            reads = containment.private_run_reads(root, runs / "own")
            self.assertEqual(
                {"run_dir": str(runs / "own"), "denied": [str(runs)], "readable": [str(runs / "own/planning")]}, reads
            )
            rule = (
                f'(deny file-read* (require-all (subpath "{runs}") (require-not (subpath "{runs / "own/planning"}"))))'
            )
            ordinary = containment._render_policy(root, scratch).splitlines()
            profile = containment._render_policy(root, scratch, private_reads=reads)
            # The same profile with one more denial, after every read allowance (the last match wins).
            self.assertEqual([*ordinary[:-1], rule, ordinary[-1]], profile.splitlines())
            self.assertTrue(containment._enforces_private_reads(containment.SEATBELT, profile.encode(), reads))
            self.assertFalse(
                containment._enforces_private_reads(containment.SEATBELT, "\n".join(ordinary).encode(), reads)
            )
            # A form-level parse, not a line match: an earlier allowance stays harmless however it is laid out.
            allow = f'(allow file-read* (subpath "{runs}"))'
            earlier = profile.replace("(version 1)\n", "(version 1) " + allow + "\n", 1)
            self.assertTrue(containment._enforces_private_reads(containment.SEATBELT, earlier.encode(), reads))
            # A write grant over the store or an ancestor defeats the denial by letting the
            # shell move or hard-link private state to a readable path (#1250), so an earlier
            # position (where a read allowance would stay harmless) does not make it safe.
            write_store = f'(allow file-write* (subpath "{runs}"))'
            write_ws = f'(allow file-write* (subpath "{root}"))'
            # Write grants that reach the store without naming a path near it: unfiltered, negated,
            # or through a symlink and ".." that a lexical normalisation would cancel.
            (root / "sub").mkdir()
            (root / "sub/link").symlink_to(runs / "own", target_is_directory=True)
            unconfined = {
                "unfiltered write grant": "(allow file-write*)",
                "unfiltered data write grant": "(allow file-write-data)",
                "negated write grant": '(allow file-write* (require-not (literal "/dev/null")))',
                "negated write grant inside require-any": (
                    f'(allow file-write* (require-any (subpath "{scratch}") (require-not (literal "/dev/null"))))'
                ),
                "write grant through a symlink and dot-dot": f'(allow file-write* (subpath "{root}/sub/link/.."))',
            }
            for name, weakened in {
                "a later allow": profile + allow + "\n",
                "a later allow default": profile + "(allow default)\n",
                "an indented later allow": profile + "  " + allow + "\n",
                "a later allow on the last line": profile.rstrip("\n") + " " + allow + "\n",
                "the denial commented out": profile.replace(rule, "; " + rule),
                "the denial inside an allow": profile.replace(rule, "(allow file-read* " + rule + ")"),
                "a redefined denial": "(define deny allow)\n" + profile,
                "an earlier allow default": profile.replace("(version 1)\n", "(version 1) (allow default)\n", 1),
                "an earlier write grant over the store": profile.replace(
                    "(version 1)\n", "(version 1) " + write_store + "\n", 1
                ),
                "an earlier write grant over the workspace": profile.replace(
                    "(version 1)\n", "(version 1) " + write_ws + "\n", 1
                ),
                **{
                    f"an earlier {name}": profile.replace("(version 1)\n", "(version 1) " + grant + "\n", 1)
                    for name, grant in unconfined.items()
                },
                **{
                    f"a later {name} before the denial": profile.replace(rule, grant + "\n" + rule, 1)
                    for name, grant in unconfined.items()
                },
                # Each of the next forms is refused by one check alone. An unknown word, here in an
                # otherwise well-formed later denial, is refused by the vocabulary (SEATBELT_WORDS).
                "an unknown word inside a later deny": profile
                + '(deny file-read* (eval "(allow file-read* (subpath \\"/\\"))"))\n',
                # A rule word inside a later rule: the nested allow is not a top-level form.
                "an allow nested in a later version": profile + '(version 1 (allow file-read* (subpath "/")))\n',
                "an allow nested in a later deny": profile
                + '(deny file-write* (literal "/dev/null") (allow file-read* (subpath "/")))\n',
                # A top-level form that is not a version, allow or deny rule.
                "an allow inside a later non-rule form": profile + '(require-all (allow file-read* (subpath "/")))\n',
                # Each of the next write grants is refused by one check of _write_allow_is_confined()
                # alone. A write grant names write operations only (here it adds a read over scratch).
                # Its filters are literal or subpath only: a regex can match the store without naming
                # it, and this one matches every path. Its path neither holds the store nor lies inside
                # it: inside, the shell can move the run's own state into its readable planning/.
                "an earlier write grant mixed with a read": profile.replace(
                    "(version 1)\n", f'(version 1) (allow file-write* file-read* (subpath "{scratch / "nested"}"))\n', 1
                ),
                "an earlier regex write grant": profile.replace(
                    "(version 1)\n", '(version 1) (allow file-write* (regex "/.*"))\n', 1
                ),
                "an earlier write grant inside the store": profile.replace(
                    "(version 1)\n", f'(version 1) (allow file-write* (subpath "{runs / "own"}"))\n', 1
                ),
            }.items():
                with self.subTest(name=name):
                    self.assertNotEqual(profile, weakened)
                    self.assertFalse(
                        containment._enforces_private_reads(containment.SEATBELT, weakened.encode(), reads)
                    )
            # A write grant confined to scratch is harmless and stays accepted wherever it sits.
            scratch_write = f'(allow file-write* (subpath "{scratch / "nested"}"))'
            benign = profile.replace("(version 1)\n", "(version 1) " + scratch_write + "\n", 1)
            self.assertTrue(containment._enforces_private_reads(containment.SEATBELT, benign.encode(), reads))
            # So is every write grant shape the renderer writes: scratch roots and check outputs.
            outputs = containment._render_policy(
                root,
                scratch,
                write_roots=[scratch / "out"],
                write_files=[scratch.parent / "check.log"],
                private_reads=reads,
            )
            self.assertIn("(allow file-write-data (literal ", outputs)
            self.assertTrue(containment._enforces_private_reads(containment.SEATBELT, outputs.encode(), reads))
            with self.assertRaisesRegex(ValueError, "read-only shell"):
                containment._render_policy(root, scratch, allow_write=True, private_reads=reads)

    def test_planning_profile_refuses_a_path_its_recheck_cannot_parse(self):
        # json.dumps writes a control character as an escape (\t, \n, \u0001) that verify()'s strict
        # parse refuses, so a planning profile refuses such a path when it is rendered (#1250).
        self.assertIsNone(containment._seatbelt_forms('(deny file-read* (subpath "a\\tb"))'))
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            for workspace, run, tool, refused in (
                ('q"uote-back\\slash', "own", "toolchain", None),  # The \" and \\ escapes parse back.
                ("tab\tname", "own", "toolchain", r"tab\\tname"),
                ("plain", "new\nline", "toolchain", r"new\\nline"),
                ("plain-tools", "own", "tool\x01chain", r"tool\\x01chain"),  # A read root outside the store.
            ):
                with self.subTest(workspace=workspace, run=run, tool=tool):
                    root, read_roots = base / workspace, [base / workspace / tool]
                    runs, scratch = root / ".autocode/runs", root / ".autocode/tool-containment-stage/scratch"
                    (runs / run / "planning").mkdir(parents=True)
                    scratch.mkdir(parents=True)
                    read_roots[0].mkdir()
                    reads = containment.private_run_reads(root, runs / run)
                    containment._render_policy(root, scratch, read_roots=read_roots)  # Not parsed again.
                    if refused:
                        with self.assertRaisesRegex(ValueError, "control character in a path: '.*" + refused):
                            containment._render_policy(root, scratch, read_roots=read_roots, private_reads=reads)
                        continue
                    profile = containment._render_policy(root, scratch, read_roots=read_roots, private_reads=reads)
                    self.assertTrue(containment._enforces_private_reads(containment.SEATBELT, profile.encode(), reads))

    def test_private_read_denial_needs_an_existing_unaliased_run_in_the_run_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runs = root / ".autocode/runs"
            for name in ("runs/own/nested", "elsewhere/run", "runs/linked-plan"):
                (root / ".autocode" / name).mkdir(parents=True)
            (runs / "alias").symlink_to(runs / "own", target_is_directory=True)
            (runs / "linked-plan/planning").symlink_to(runs / "own", target_is_directory=True)
            for run in (
                runs,
                runs / "own/nested",
                runs / "missing",
                root / ".autocode/elsewhere/run",
                runs / "alias",
                runs / "linked-plan",
            ):
                with self.subTest(run=run), self.assertRaises(ValueError):
                    containment.private_run_reads(root, run)

    def test_unsupported_platform_is_explicit_prelaunch_failure(self):
        for name in ("linux", "freebsd"):
            with (
                self.subTest(platform=name),
                patch.object(containment.sys, "platform", name),
                patch.object(containment, "BWRAP", "/nonexistent/bwrap"),
            ):
                with self.assertRaisesRegex(RuntimeError, "requires macOS sandbox-exec or Linux bubblewrap"):
                    containment.prepare("/workspace")

    def test_effective_shell_patterns_and_stricter_actions_are_preserved(self):
        rules = [
            {"permission": "*", "pattern": "*", "action": "allow"},
            {"permission": "bash", "pattern": "git *", "action": "ask"},
            {"permission": "bash", "pattern": "git push*", "action": "deny"},
            {"permission": "read", "pattern": "*", "action": "deny"},
        ]
        result = containment.shell_permissions(rules)
        self.assertEqual({"*": "allow", "git *": "ask", "git push*": "deny"}, result["bash"])
        self.assertEqual("deny", result["*"])
        self.assertEqual("deny", result["external_directory"])
        rules += [{"permission": "*", "pattern": "*", "action": "deny"}]
        self.assertEqual(["git *", "git push*", "*"], list(containment.shell_permissions(rules)["bash"]))
        for unsupported in (None, [], [{"permission": "bash", "pattern": "*", "action": "auto"}]):
            with self.assertRaises(RuntimeError):
                containment.shell_permissions(unsupported)

    def test_adapter_opt_in_retains_model_effort_and_command(self):
        with patch.object(opencode.tool_containment, "configure") as configure:
            configure.side_effect = lambda command, env, workspace, **kw: (
                {**env, "AUTOCODE_TOOL_CONTAINMENT": '{"qualified":true}'},
                {"shell": "/owned/shell"},
            )
            command, env, overrides = opencode.launch(
                "sol",
                "/workspace",
                "/run",
                None,
                "test/model",
                "high",
                False,
                env={"PATH": "/bin"},
                containment={"read_roots": ["/runtime"]},
            )
        self.assertEqual("test/model", command[command.index("--model") + 1])
        self.assertEqual("high", command[command.index("--variant") + 1])
        self.assertNotIn("--auto", command)
        self.assertEqual("/owned/shell", overrides["shell"])
        self.assertEqual({"read_roots": ["/runtime"]}, configure.call_args.kwargs["request"])
        self.assertFalse(configure.call_args.kwargs["allow_write"])
        self.assertIn("AUTOCODE_TOOL_CONTAINMENT", env)

    def test_loopback_requests_fail_before_files_or_native_debug_are_created(self):
        authority = {"purpose": "approved-readonly-unittest", "binding_sha256": "a" * 64}
        command = shlex.join([sys.executable, "-m", "unittest", "-v", "test_http"])
        with (
            patch.object(containment.sys, "platform", "darwin"),
            patch.object(containment.subprocess, "run") as launch,
            patch.object(Path, "mkdir") as mkdir,
        ):
            for writable in (False, True):
                for request in (
                    {"loopback_checks": [command]},
                    {"loopback_authority": authority},
                    {"loopback_authority": {}},
                    {"loopback_checks": [command], "loopback_authority": authority},
                ):
                    with self.subTest(writable=writable, request=request):
                        with self.assertRaisesRegex(RuntimeError, "Exact-IP loopback containment is unsupported"):
                            containment.prepare("/workspace", allow_write=writable, **request)
                        with self.assertRaisesRegex(RuntimeError, "runner-mediated approved check"):
                            containment.configure(["opencode"], {}, "/workspace", allow_write=writable, request=request)
            launch.assert_not_called()
            mkdir.assert_not_called()
        containment._reject_loopback_request([], None)
        with self.assertRaises(ValueError):
            containment._reject_loopback_request(command, None)

    def test_recorded_scratch_is_only_the_layout_prepare_creates_in_this_workspace(self):
        # A contained stage captures evidence in its scratch; the launch record says whose it is (#419).
        private = Path("/workspace/.autocode")
        control = "tool-containment-" + "a" * 32
        genuine = private / control / "scratch"
        lookalikes = [
            private / ("tool-containment-" + "A" * 32) / "scratch",
            private / ("tool-containment-" + "a" * 31) / "scratch",
            private / ("tool-containment-" + "a" * 32 + "0") / "scratch",
            private / ("tool-containment-" + "a" * 32 + "/../" + control) / "scratch",
            private / control,
            private / control / "scratch-copy",
            private / control / "scratch" / "nested",
            private / "runs" / "other" / control / "scratch",
            Path("/elsewhere/.autocode") / control / "scratch",
            Path("workspace/.autocode") / control / "scratch",
        ]
        records = [
            {"stage": "terra"},
            {"stage": "sol", "tool_containment": {"scratch": str(genuine)}},
            {"stage": "sol", "tool_containment": {"scratch": str(genuine)}},
            *({"stage": "sol", "tool_containment": {"scratch": str(path)}} for path in lookalikes),
            {"tool_containment": None},
            {"tool_containment": {"scratch": 7}},
            {"tool_containment": "x"},
            "x",
        ]
        self.assertEqual((genuine,), containment.recorded_scratch(records, "/workspace"))
        self.assertEqual((), containment.recorded_scratch(records, "/other"))

    def test_recorded_backend_is_the_latest_contained_launchs_own_manifest(self):
        # The status view names the boundary a launch actually ran in (#709), never this host's.
        legacy = {"stage": "sol", "iteration": 1, "started_at": "t1", "tool_containment": {"scratch": "/s"}}
        linux = {
            "stage": "terra",
            "iteration": 2,
            "started_at": "t2",
            "tool_containment": {"backend": "bubblewrap", "backend_version": "bubblewrap 0.9.0"},
        }
        waived = {"stage": "sol", "iteration": 2, "tool_containment": None, "uncontained_tools": True}
        self.assertIsNone(containment.recorded_backend([]))
        self.assertIsNone(
            containment.recorded_backend([{"stage": "astra_plan"}, waived, "x", {"tool_containment": "x"}])
        )
        # A manifest saved before #709 names no backend: only Seatbelt existed then, as verify() reads it.
        self.assertEqual(
            {"backend": "seatbelt", "backend_version": None, "stage": "sol", "iteration": 1, "started_at": "t1"},
            containment.recorded_backend([legacy]),
        )
        # Later uncontained or planning launches do not hide the last boundary that applied.
        self.assertEqual(
            {
                "backend": "bubblewrap",
                "backend_version": "bubblewrap 0.9.0",
                "stage": "terra",
                "iteration": 2,
                "started_at": "t2",
            },
            containment.recorded_backend([legacy, linux, waived, {"stage": "astra_plan"}]),
        )
        # A name that is not a backend is not repeated as one.
        for odd in (
            {"backend": 7, "backend_version": ["x"]},
            {"backend": ["bubblewrap"], "backend_version": {"v": 1}},
            {"backend": "chroot", "backend_version": None},
        ):
            named = containment.recorded_backend([{"stage": "terra", "tool_containment": odd}])
            self.assertEqual((None, None), (named["backend"], named["backend_version"]))


class BubblewrapArgumentTests(unittest.TestCase):
    """The Linux backend's mount plan (#709), checked on any host without running bwrap."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve() / "project"
        (self.root / ".autocode/tool-containment-stage/scratch").mkdir(parents=True)
        (self.root / ".git").mkdir()
        (self.root / "runtime").mkdir()
        self.scratch = self.root / ".autocode/tool-containment-stage/scratch"

    def mounts(self, **authority):
        arguments, watched = containment.bubblewrap_arguments(self.root, self.scratch, **authority)
        flags = ("--bind", "--ro-bind", "--ro-bind-try")
        found = [
            (arguments[i], arguments[i + 1], arguments[i + 2]) for i in range(len(arguments)) if arguments[i] in flags
        ]
        return arguments, found, watched

    def test_isolates_namespaces_capabilities_network_and_leaves_root_read_only(self):
        arguments, mounts, _ = self.mounts()
        for flag in ("--unshare-all", "--die-with-parent", "--new-session"):
            self.assertIn(flag, arguments)
        cap = arguments.index("--cap-drop")
        self.assertEqual(["--cap-drop", "ALL"], arguments[cap : cap + 2])
        self.assertNotIn("--share-net", arguments)
        self.assertEqual(["--remount-ro", "/"], arguments[-2:])
        targets = {target for _, _, target in mounts}
        for absent in ("/", "/tmp", "/home", "/root", "/run", "/var", str(Path.home())):
            self.assertNotIn(absent, targets)

    def test_reader_gets_one_read_only_workspace_mount_and_only_its_own_scratch_writable(self):
        _, mounts, watched = self.mounts(protected_paths=[self.root / "runtime"])
        writable = [target for flag, _, target in mounts if flag == "--bind"]
        self.assertEqual([str(self.scratch)], writable)
        inside = [mount for mount in mounts if Path(mount[2]).is_relative_to(self.root)]
        # The read-only workspace already covers every protected path except inside the scratch.
        expected = [("--ro-bind", str(self.root), str(self.root)), ("--bind", str(self.scratch), str(self.scratch))]
        self.assertEqual(expected, inside)
        self.assertEqual([], watched)

    def test_builder_writes_workspace_but_runner_authority_is_remounted_read_only_after_it(self):
        _, mounts, watched = self.mounts(allow_write=True, protected_paths=[self.root / "runtime"])
        order = [target for _, _, target in mounts]
        self.assertIn(("--bind", str(self.root), str(self.root)), mounts)
        for name in (".autocode", ".git", "runtime"):
            path = str(self.root / name)
            self.assertIn(("--ro-bind", path, path), mounts)
            self.assertGreater(order.index(path), order.index(str(self.root)))
        # The stage scratch is writable again inside the read-only .autocode.
        self.assertGreater(order.index(str(self.scratch)), order.index(str(self.root / ".autocode")))
        # Missing protected names cannot be mount points without bwrap creating them in the
        # real workspace: they are watched by the launch's shell instead.
        self.assertEqual({self.root / name for name in (".opencode", "opencode.json", "opencode.jsonc")}, set(watched))
        for path in watched:
            self.assertNotIn(str(path), order)
            self.assertFalse(path.exists())

    def test_planning_reader_hides_the_run_store_except_its_own_planning_artifacts(self):
        # #1250: the store is an empty read-only tmpfs; only this run's planning/ is bound back.
        runs, own = self.root / ".autocode/runs", self.root / ".autocode/runs/own"
        (own / "planning").mkdir(parents=True)
        (runs / "sibling").mkdir()
        (runs / "sibling/.env").write_text("SIBLING=secret\n")
        (own / "planning/.env").write_text("PLAN=secret\n")
        reads = containment.private_run_reads(self.root, own)
        arguments, mounts, watched = self.mounts(protected_paths=[own], private_reads=reads)
        hidden = arguments.index("--tmpfs")
        self.assertEqual(["--tmpfs", str(runs)], arguments[hidden : hidden + 2])
        shown = arguments.index(str(own / "planning"))
        self.assertEqual(
            ["--ro-bind-try", str(own / "planning"), str(own / "planning")], arguments[shown - 1 : shown + 2]
        )
        masked = arguments.index(str(own / "planning/.env"))
        self.assertEqual(["--ro-bind", "/dev/null", str(own / "planning/.env")], arguments[masked - 2 : masked + 1])
        self.assertLess(hidden, shown)
        self.assertLess(shown, masked)
        self.assertNotIn(str(runs / "sibling/.env"), arguments)  # Masking it would create its name in the tmpfs.
        self.assertEqual(["--remount-ro", str(runs), "--remount-ro", "/"], arguments[-4:])
        self.assertEqual([str(self.scratch)], [target for flag, _, target in mounts if flag == "--bind"])
        self.assertIn(("--ro-bind", str(self.root), str(self.root)), mounts)
        self.assertEqual([], watched)

        def encoded(values):
            return b"".join(os.fsencode(value) + b"\0" for value in values)

        self.assertTrue(containment._enforces_private_reads(containment.BUBBLEWRAP, encoded(arguments), reads))
        ordinary = self.mounts(protected_paths=[own])[0]
        self.assertNotIn("--tmpfs", ordinary)
        self.assertFalse(containment._enforces_private_reads(containment.BUBBLEWRAP, encoded(ordinary), reads))
        (self.root / "alias").symlink_to(own, target_is_directory=True)
        (self.root / "sub").mkdir()
        (self.root / "sub/link").symlink_to(own, target_is_directory=True)
        for shown_again in (
            ["--ro-bind", str(self.root), str(self.root)],
            ["--ro-bind", str(runs / "sibling"), str(self.scratch / "sibling")],
            # Sources are compared as the kernel opens them, and a directory holding the store
            # may only be mounted onto itself, before the tmpfs.
            ["--ro-bind", str(self.root), str(self.scratch / "mirror")],
            ["--ro-bind", str(own / "planning/../state.json"), str(self.scratch / "state.json")],
            ["--ro-bind", str(self.root / "alias"), str(self.scratch / "alias")],
            # A symlink followed by "..": lexically <root>/sub, but the kernel opens the link
            # target's parent, the store, so the source is judged exactly as written.
            ["--ro-bind", str(self.root / "sub/link/.."), str(self.scratch / "peek")],
            ["--ro-bind", ".autocode/runs/own", str(self.scratch / "relative")],
            # A per-process /proc or /dev/fd source resolves to a different path in bwrap than
            # in this verifier (#1250), so it cannot be judged here and is refused outright.
            ["--ro-bind", "/proc/self/cwd", str(self.scratch / "proc")],
            ["--ro-bind", "/dev/fd/3", str(self.scratch / "fd")],
        ):
            tampered = [*arguments[:-2], *shown_again, *arguments[-2:]]
            with self.subTest(shown_again=shown_again):
                self.assertFalse(containment._enforces_private_reads(containment.BUBBLEWRAP, encoded(tampered), reads))
        # The /proc and /dev/fd refusal alone: realpath leaves these sources as written, so the
        # canonical check admits them. Bare /proc reaches every host process's cwd and root, and a
        # pid with no process now (so realpath cannot resolve it) may have one when bwrap opens it.
        for source in ("/proc", "/proc/2147483647/cwd"):
            linked = [*arguments[:-2], "--ro-bind", source, str(self.scratch / "link"), *arguments[-2:]]
            with self.subTest(magic_link=source):
                self.assertEqual(source, os.path.realpath(source))
                self.assertFalse(containment._enforces_private_reads(containment.BUBBLEWRAP, encoded(linked), reads))
        # On Linux /dev/fd is a link to /proc/self/fd, so the canonical check refuses its entries
        # too. On a host where it is not a link, realpath leaves them as written: model that host.
        real = os.path.realpath

        def unlinked(path):
            return path if path == "/dev/fd" or path.startswith("/dev/fd/") else real(path)

        with patch("os.path.realpath", unlinked):
            for source in ("/dev/fd", "/dev/fd/3"):
                linked = [*arguments[:-2], "--ro-bind", source, str(self.scratch / "link"), *arguments[-2:]]
                with self.subTest(magic_link=source, host="/dev/fd not a link"):
                    self.assertEqual(source, os.path.realpath(source))
                    self.assertFalse(
                        containment._enforces_private_reads(containment.BUBBLEWRAP, encoded(linked), reads)
                    )
        # A writable bind of the store, or of a directory holding it, lets the shell rename the
        # store out from under the tmpfs and read it in the next sandbox (#1250). Each bind below
        # passes every other check, as its read-only form shows: a self-bind of an ancestor before
        # the tmpfs, and one of this run's own planning/ after it. Only the writable-bind check
        # refuses the writable form.
        for name, (position, source) in {
            "holding the store": (hidden, str(self.root / ".autocode")),
            "inside the store": (len(arguments) - 2, str(own / "planning")),
        }.items():
            for flag, enforced in (("--ro-bind", True), ("--bind", False)):
                bound = [*arguments[:position], flag, source, source, *arguments[position:]]
                with self.subTest(self_bind=name, flag=flag):
                    self.assertIs(
                        enforced, containment._enforces_private_reads(containment.BUBBLEWRAP, encoded(bound), reads)
                    )
        # The tmpfs stays, but it is not sealed read-only after it: the seal requirement alone refuses it.
        seal = ["--remount-ro", str(runs)]
        self.assertEqual(seal, arguments[-4:-2])
        for name, unsealed in {
            "the seal dropped": [*arguments[:-4], *arguments[-2:]],
            "the seal moved before the tmpfs": [*arguments[:hidden], *seal, *arguments[hidden:-4], *arguments[-2:]],
        }.items():
            with self.subTest(name=name):
                self.assertEqual(["--tmpfs", str(runs)], unsealed[unsealed.index("--tmpfs") :][:2])
                self.assertFalse(containment._enforces_private_reads(containment.BUBBLEWRAP, encoded(unsealed), reads))
        # Replacing the read-only workspace mount with a writable one is refused the same way.
        writable_ws = list(arguments)
        for i, value in enumerate(writable_ws):
            if value == "--ro-bind" and writable_ws[i + 1 : i + 3] == [str(self.root), str(self.root)]:
                writable_ws[i] = "--bind"
        self.assertIn("--bind", writable_ws)
        self.assertFalse(containment._enforces_private_reads(containment.BUBBLEWRAP, encoded(writable_ws), reads))
        with self.assertRaisesRegex(ValueError, "read-only shell"):
            containment.bubblewrap_arguments(self.root, self.scratch, allow_write=True, private_reads=reads)

    def test_directories_holding_a_nested_protected_path_are_bound_onto_themselves(self):
        nested = self.root / "vendor/autocode/tools"
        nested.mkdir(parents=True)
        _, mounts, _ = self.mounts(allow_write=True, protected_paths=[nested])
        order = [target for _, _, target in mounts]
        # Writable, but mount points: a Builder cannot rename them to recreate the protected name.
        for ancestor in (self.root / "vendor", self.root / "vendor/autocode"):
            self.assertIn(("--bind", str(ancestor), str(ancestor)), mounts)
            self.assertLess(order.index(str(ancestor)), order.index(str(nested)))
        self.assertIn(("--ro-bind", str(nested), str(nested)), mounts)
        # A reader's workspace is read-only already: no extra mounts.
        _, reader, _ = self.mounts(protected_paths=[nested])
        self.assertNotIn(str(self.root / "vendor"), [target for _, _, target in reader])

    def test_verification_copy_is_one_read_only_mount_inside_the_writable_scratch(self):
        tree = self.scratch / "verification"
        (tree / "src").mkdir(parents=True)
        (tree / "src/app.py").write_text("VALUE = 1\n")
        (tree / "linked").symlink_to(self.root / "runtime", target_is_directory=True)
        protected = [tree, *tree.rglob("*")]
        _, mounts, watched = self.mounts(protected_paths=protected)
        under = [mount for mount in mounts if Path(mount[2]).is_relative_to(tree)]
        self.assertEqual([("--ro-bind", str(tree), str(tree))], under)
        self.assertEqual([], watched)

    def test_external_read_roots_are_mounted_but_the_system_runtime_only_once(self):
        external = self.root.parent / "toolchain"
        external.mkdir()
        system = Path("/usr/lib").resolve()
        _, mounts, _ = self.mounts(read_roots=[external, system])
        self.assertIn(("--ro-bind-try", str(external), str(external)), mounts)
        self.assertNotIn(str(system), [target for flag, _, target in mounts if flag == "--ro-bind-try"])

    def test_toolchain_links_are_recreated_only_where_no_mount_shows_them(self):
        links = [
            ["/opt/toolchain/bin/npm", "../lib/node_modules/npm/bin/npm-cli.js"],
            [str(self.root / "bin/tool"), "real-tool"],  # the workspace mount shows it
            ["/usr/bin/python3", "python3.13"],  # so does the system runtime
        ]
        arguments, _, _ = self.mounts(links=links)
        created = [arguments[i + 1 : i + 3] for i, value in enumerate(arguments) if value == "--symlink"]
        self.assertIn(["../lib/node_modules/npm/bin/npm-cli.js", "/opt/toolchain/bin/npm"], created)
        self.assertNotIn(str(self.root / "bin/tool"), [path for _, path in created])
        self.assertNotIn("/usr/bin/python3", [path for _, path in created])

    def test_secret_files_are_masked_and_other_launches_are_not_searched(self):
        (self.root / "src").mkdir()
        (self.root / "src/.env.local").write_text("TOKEN=secret\n")
        (self.root / "auth.json").write_text("{}")
        (self.root / "environment.py").write_text("")
        other = self.root / (".autocode/tool-containment-" + "0" * 32)
        other.mkdir()
        (other / ".env").write_text("other launch\n")
        own = self.root / ".autocode/tool-containment-stage"
        (own / ".env").write_text("this launch's copy\n")
        _, mounts, _ = self.mounts()
        masked = {target for flag, source, target in mounts if source == "/dev/null"}
        self.assertEqual({str(self.root / "src/.env.local"), str(self.root / "auth.json"), str(own / ".env")}, masked)

    def test_symlinked_protected_path_is_refused_in_writable_space(self):
        link = self.root / "linked-runtime"
        link.symlink_to(self.root / "runtime")
        self.mounts(protected_paths=[link])  # read-only stages: the whole workspace is read-only
        with self.assertRaisesRegex(ValueError, "cannot protect a symlink"):
            self.mounts(allow_write=True, protected_paths=[link])

    def test_scoped_writer_mounts_only_existing_literal_files_and_preserves_parent(self):
        source = self.root / "src"
        source.mkdir()
        allowed = source / "app.py"
        allowed.write_text("VALUE = 1\n")
        _, mounts, _ = self.mounts(
            allow_write=True,
            write_paths=["src/app.py"],
            writer_path_kinds={"src/app.py": "file"},
        )
        self.assertIn(("--ro-bind", str(self.root), str(self.root)), mounts)
        self.assertIn(("--bind", str(allowed), str(allowed)), mounts)
        self.assertNotIn(("--bind", str(source), str(source)), mounts)
        self.assertNotIn(("--bind", str(self.root), str(self.root)), mounts)

    def test_scoped_writer_refuses_missing_paths_and_changed_pinned_kind(self):
        for name, kind in (("missing.py", "file"), ("runtime", "file")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.mounts(allow_write=True, write_paths=[name], writer_path_kinds={name: kind})
        with self.assertRaisesRegex(ValueError, "Read-only stages"):
            self.mounts(write_paths=["runtime"], writer_path_kinds={"runtime": "directory"})

    def test_unscoped_workspace_or_read_root_is_refused(self):
        for root in ("/", str(Path.home())):
            with self.assertRaises(ValueError):
                containment.bubblewrap_arguments(root, Path(root) / ".autocode/s/scratch")
        with self.assertRaises(ValueError):
            self.mounts(read_roots=["/"])


class KernelReceiptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()
        self.control = self.workspace / ".autocode" / ("tool-containment-" + "a" * 32)
        self.scratch = self.control / "scratch"
        self.scratch.mkdir(parents=True)
        for name in ("shell", "policy.sb"):
            (self.control / name).write_text("runner-owned fixture\n")
        self.boundary = {
            "profile": str(self.control / "policy.sb"),
            "shell": str(self.control / "shell"),
            "scratch": str(self.scratch),
        }
        self.command = ["opencode", "run", "--agent", "autocode_designer", "--model", "test/model"]
        self.environment = {
            "PATH": "/bin",
            "OPENCODE_CONFIG_CONTENT": json.dumps({"agent": {"autocode_designer": {"permission": {"bash": "allow"}}}}),
        }
        self.agent = {
            "permission": [{"permission": "bash", "pattern": "*", "action": "allow"}],
            "tools": {"bash": True, "read": False},
        }

    def configure(self, change=lambda response: response, *, scratch=True, sentinel=True, request=None, private=None):
        import autocode_toolchain

        def native(argv, **kwargs):
            if argv[-1] == "--version":
                return SimpleNamespace(returncode=0, stdout=containment.SUPPORTED_VERSION, stderr="")
            response = self.agent
            if "--params" in argv:
                params = json.loads(argv[-1])
                response = {"input": params, "result": {"metadata": {"exit": 0}, "output": "Operation not permitted"}}
                if scratch:
                    (self.scratch / "conformance-capture").touch()
                if not sentinel:
                    (self.control / "conformance-forbidden").write_text("changed\n")
                response = change(response)
                if private is not None and "-private-read-sentinel" in params["command"]:
                    response = private(params, response)
            return SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr="")

        with (
            patch.object(containment.sys, "platform", "darwin"),
            patch.object(containment.shutil, "which", return_value="/owned/opencode"),
            patch.object(containment.subprocess, "run", side_effect=native) as run,
            patch.object(containment, "prepare", return_value=self.boundary),
            patch.object(autocode_toolchain, "discover", return_value={"read_roots": [], "probes": []}),
            patch.object(containment, "verify") as verify,
        ):
            try:
                return containment.configure(
                    self.command, self.environment, self.workspace, allow_write=False, request=request or {}
                )
            finally:
                self.native_calls = run.call_args_list
                self.verification_calls = verify.call_count

    def failure(self, predicate, change=lambda response: response, **kwargs):
        with self.assertRaisesRegex(RuntimeError, predicate):
            self.configure(change, **kwargs)
        self.assertEqual(0, self.verification_calls)
        self.assertFalse((self.control / "conformance.json").exists())
        receipt = json.loads((self.control / "conformance-failure.json").read_text())
        self.assertEqual("rejected", receipt["status"])
        self.assertIn(predicate, receipt["failed_predicates"])
        self.assertFalse(receipt["checks"][predicate])
        self.assertEqual(0o400, (self.control / "conformance-failure.json").stat().st_mode & 0o777)
        self.assertLess(len(json.dumps(receipt)), 3000)
        return receipt

    def test_success_retains_only_real_proof_and_original_limits(self):
        self.configure()
        self.assertEqual(1, self.verification_calls)
        self.assertTrue((self.control / "conformance.json").is_file())
        self.assertFalse((self.control / "conformance-failure.json").exists())
        self.assertEqual([15, 60, 60, 60], [call.kwargs["timeout"] for call in self.native_calls])
        self.assertEqual(10000, json.loads(self.native_calls[-1].args[0][-1])["timeout"])

    def test_each_kernel_predicate_is_required_even_when_other_checks_pass(self):
        cases = [
            ("input_command_match", lambda r: {**r, "input": {"command": "different"}}, {}),
            ("metadata_exit_int", lambda r: {**r, "result": {**r["result"], "metadata": {"exit": False}}}, {}),
            ("exit_zero", lambda r: {**r, "result": {**r["result"], "metadata": {"exit": 7}}}, {}),
            ("scratch_created", lambda r: r, {"scratch": False}),
            ("sentinel_untouched", lambda r: r, {"sentinel": False}),
        ]
        for predicate, change, kwargs in cases:
            with self.subTest(predicate=predicate):
                self.setUp()
                self.failure(predicate, change, **kwargs)

    def test_malformed_native_objects_fail_closed_without_attribute_or_type_errors(self):
        cases = [
            ("response_object", lambda r: []),
            ("input_object", lambda r: {**r, "input": "not an object"}),
            ("result_object", lambda r: {**r, "result": None}),
            ("metadata_object", lambda r: {**r, "result": {**r["result"], "metadata": []}}),
        ]
        for predicate, change in cases:
            with self.subTest(predicate=predicate):
                self.setUp()
                self.failure(predicate, change)

    def test_missing_or_malformed_display_output_does_not_replace_direct_evidence(self):
        for output, kind, omit in (
            ("", "string", False),
            (None, "null", False),
            ({"secret": "SECRET"}, "object", False),
            (None, "null", True),
        ):
            with self.subTest(kind=kind, omit=omit):
                self.setUp()
                self.configure(
                    lambda r: {
                        **r,
                        "result": {
                            **{key: value for key, value in r["result"].items() if key != "output"},
                            **({} if omit else {"output": output}),
                        },
                    }
                )
                self.assertEqual(1, self.verification_calls)
                self.assertFalse((self.control / "conformance-failure.json").exists())
                proof_text = (self.control / "conformance.json").read_text()
                self.assertNotIn("SECRET", proof_text)
                receipt = json.loads(proof_text)["native_receipt"]
                self.assertEqual("passed", receipt["status"])
                self.assertEqual([], receipt["failed_predicates"])
                self.assertFalse(receipt["checks"]["denial_present"])
                self.assertEqual(kind == "string", receipt["checks"]["output_string"])
                self.assertEqual(kind, receipt["types"]["output"])

    def test_adversarial_text_and_extra_keys_never_enter_receipt_or_exception(self):
        secret = "SECRET-https://user:password@private.invalid/token"

        def malicious(response):
            return {
                "input": {"command": secret + "\ud800"},
                "result": {"metadata": {"exit": 2**100, "credentials": secret}, "output": secret},
                secret: secret,
            }

        with self.assertRaises(RuntimeError) as caught:
            self.configure(malicious)
        receipt_text = (self.control / "conformance-failure.json").read_text()
        self.assertNotIn(secret, receipt_text + str(caught.exception))
        receipt = json.loads(receipt_text)
        self.assertIsNone(receipt["exit"])
        self.assertEqual("integer", receipt["types"]["exit"])
        self.assertEqual(64, len(receipt["observed_command_sha256"]))

    def test_rejected_command_hashing_has_a_fixed_allocation_bound(self):
        original = containment.hashlib.sha256

        def bounded_hash(data):
            self.assertLessEqual(len(data), 4 * 16384)
            return original(data)

        for length in (16384, 16385):
            with self.subTest(length=length):
                self.setUp()
                command = "\U0001f642" * length
                with patch.object(containment.hashlib, "sha256", side_effect=bounded_hash):
                    receipt = self.failure("input_command_match", lambda r: {**r, "input": {"command": command}})
                if length == 16384:
                    self.assertEqual(64, len(receipt["observed_command_sha256"]))
                else:
                    self.assertIsNone(receipt["observed_command_sha256"])

    def test_unreadable_sentinel_is_a_rejected_predicate_not_raw_io_error(self):
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path.name == "conformance-forbidden":
                raise OSError("SECRET credential-bearing filesystem error")
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            receipt = self.failure("sentinel_untouched")
        self.assertTrue(receipt["sentinel_io_error"])

    def test_diagnostic_write_failure_still_rejects_without_raw_error(self):
        original = os.open

        def open_file(path, *args, **kwargs):
            if Path(path).name == "conformance-failure.json":
                raise OSError("SECRET credentials")
            return original(path, *args, **kwargs)

        with patch.object(containment.os, "open", side_effect=open_file):
            with self.assertRaisesRegex(RuntimeError, "scratch_created; diagnostic unavailable") as caught:
                self.configure(scratch=False)
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertFalse((self.control / "conformance.json").exists())
        self.assertEqual(0, self.verification_calls)

    def test_unreadable_scratch_is_a_rejected_predicate(self):
        original = Path.is_file

        def is_file(path):
            if path.name == "conformance-capture":
                raise OSError("SECRET filesystem error")
            return original(path)

        with patch.object(Path, "is_file", is_file):
            receipt = self.failure("scratch_created")
        self.assertTrue(receipt["scratch_io_error"])

    def test_missing_fields_and_string_exit_are_not_success(self):
        for change in (
            lambda r: {},
            lambda r: {"input": {}, "result": {}},
            lambda r: {**r, "result": {**r["result"], "metadata": {"exit": "0"}}},
        ):
            with self.subTest(change=change):
                self.setUp()
                self.failure("metadata_exit_int", change)

    def test_existing_diagnostic_symlink_is_never_followed(self):
        outside = self.workspace / "unrelated"
        outside.write_text("untouched\n")
        (self.control / "conformance-failure.json").symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, "diagnostic unavailable"):
            self.configure(scratch=False)
        self.assertEqual("untouched\n", outside.read_text())
        self.assertFalse((self.control / "conformance.json").exists())

    def private_run(self):
        """A planning run whose prepared boundary carries the private-read denial (#1250), and its sentinel."""
        run = self.workspace / ".autocode/runs/own"
        (run / "planning").mkdir(parents=True)
        (run / "state.json").write_text('{"private":"own run state"}\n')
        self.boundary["private_reads"] = containment.private_run_reads(self.workspace, run)
        return run, run / (self.control.name + "-private-read-sentinel")

    def test_private_read_probe_reads_a_sentinel_in_this_runs_own_directory(self):
        run, sentinel = self.private_run()
        seen = []

        def observe(params, response):
            seen.append(sentinel.read_text())  # It exists while the actual bash is asked to read it.
            return response

        self.configure(request={"private_run": str(run)}, private=observe)
        planning = str(run / "planning")
        command = "! /bin/cat " + shlex.quote(str(sentinel)) + " && /bin/ls " + shlex.quote(planning) + " > /dev/null"
        self.assertEqual(
            {
                **self.boundary["private_reads"],
                "listed": [planning],
                "command": command,
                "tool": "bash",
                "exit": 0,
                "output": "Operation not permitted",
            },
            json.loads((self.control / "conformance.json").read_text())["private_reads"],
        )
        self.assertRegex(seen[0], r"^[0-9a-f]{32}\n$")
        self.assertFalse(sentinel.exists())

    def test_private_read_probe_refuses_any_sign_that_the_bash_read_run_state(self):
        real_run = subprocess.run  # configure() patches subprocess.run.

        def uncontained(params, sentinel):
            ran = real_run(["/bin/sh", "-c", params["command"]], capture_output=True, text=True, timeout=10)
            return ran.returncode, ran.stdout + ran.stderr

        def rewritten(params, sentinel):
            sentinel.write_text("changed\n")
            return 0, "Permission denied"

        cases = {
            "an uncontained shell reads it": uncontained,
            "the token appears beside a denial": lambda params, sentinel: (
                0,
                "Permission denied\n" + sentinel.read_text(),
            ),
            "no denial is reported": lambda params, sentinel: (0, ""),
            "the denial does not exit 0": lambda params, sentinel: (1, "Permission denied"),
            "the sentinel is changed": rewritten,
        }
        for name, answer in cases.items():
            with self.subTest(name=name):
                self.setUp()
                run, sentinel = self.private_run()

                def private(params, response, answer=answer, sentinel=sentinel):
                    exit, output = answer(params, sentinel)
                    return {**response, "result": {"metadata": {"exit": exit}, "output": output}}

                with self.assertRaisesRegex(RuntimeError, "did not deny reading private run state"):
                    self.configure(request={"private_run": str(run)}, private=private)
                self.assertFalse(sentinel.exists())
                self.assertEqual(0, self.verification_calls)
                self.assertFalse((self.control / "conformance.json").exists())


class AvailabilityTests(unittest.TestCase):
    """The cheap run-setup check (#413): platform, sandbox-exec, OpenCode version; no conformance."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.bin = Path(directory.name).resolve()
        # Simulated macOS anywhere (CI is Linux): any existing file stands in for sandbox-exec.
        self.mac = SimpleNamespace(platform="darwin")
        self.sandbox = patch.object(containment, "SANDBOX_EXEC", sys.executable)

    def opencode(self, version, code=0):
        client = self.bin / "opencode"
        client.write_text(f"#!/bin/sh\necho {version}\nexit {code}\n")
        client.chmod(0o755)
        return {"PATH": str(self.bin)}

    def test_other_platform_is_named(self):
        with patch.object(containment, "sys", SimpleNamespace(platform="freebsd")):
            reason = containment.unavailable(environment=self.opencode(containment.SUPPORTED_VERSION))
        self.assertIn("requires macOS sandbox-exec or Linux bubblewrap", reason)
        self.assertIn("freebsd", reason)

    def test_linux_without_bubblewrap_is_named(self):
        with (
            patch.object(containment, "sys", SimpleNamespace(platform="linux")),
            patch.object(containment, "BWRAP", str(self.bin / "no-bwrap")),
        ):
            reason = containment.unavailable(environment=self.opencode(containment.SUPPORTED_VERSION))
        self.assertIn("requires macOS sandbox-exec", reason)
        self.assertIn("this machine is linux without " + str(self.bin / "no-bwrap"), reason)

    def test_linux_bubblewrap_without_namespaces_is_named(self):
        # Installed but unusable (user namespaces disabled): refused at setup, not at the first launch.
        bwrap = self.bin / "bwrap"
        bwrap.write_text("#!/bin/sh\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n")
        bwrap.chmod(0o755)
        with (
            patch.object(containment, "sys", SimpleNamespace(platform="linux")),
            patch.object(containment, "BWRAP", str(bwrap)),
        ):
            reason = containment.unavailable(environment=self.opencode(containment.SUPPORTED_VERSION))
        self.assertIn("cannot create unprivileged namespaces", reason)
        self.assertIn("setting up uid map: Permission denied", reason)
        # Named the way every refusal names the host (test_containment_policy_cli matches it).
        self.assertIn("this machine is linux", reason)

    def test_linux_with_working_bubblewrap_checks_the_opencode_version(self):
        bwrap = self.bin / "bwrap"
        bwrap.write_text("#!/bin/sh\nexit 0\n")
        bwrap.chmod(0o755)
        with (
            patch.object(containment, "sys", SimpleNamespace(platform="linux")),
            patch.object(containment, "BWRAP", str(bwrap)),
        ):
            self.assertIn(
                "this machine has OpenCode 1.18.34", containment.unavailable(environment=self.opencode("1.18.34"))
            )
            self.assertIsNone(containment.unavailable(self.bin, self.opencode(containment.SUPPORTED_VERSION)))

    def test_missing_sandbox_exec_is_named(self):
        with (
            patch.object(containment, "sys", self.mac),
            patch.object(containment, "SANDBOX_EXEC", str(self.bin / "no-sandbox-exec")),
        ):
            self.assertIn("sandbox-exec, which is missing", containment.unavailable(environment=self.opencode("x")))

    def test_other_or_unreadable_opencode_version_is_named(self):
        with patch.object(containment, "sys", self.mac), self.sandbox:
            reason = containment.unavailable(environment=self.opencode("1.18.34"))
            self.assertIn("only for OpenCode " + containment.SUPPORTED_VERSION, reason)
            self.assertIn("this machine has OpenCode 1.18.34", reason)
            failed = containment.unavailable(environment=self.opencode(containment.SUPPORTED_VERSION, code=3))
            self.assertIn("unknown (exit 3)", failed)
            self.assertIn(
                "cannot find the opencode executable",
                containment.unavailable(environment={"PATH": str(self.bin / "empty")}),
            )

    def test_qualified_setup_is_available_without_running_conformance(self):
        with (
            patch.object(containment, "sys", self.mac),
            self.sandbox,
            patch.object(containment, "configure") as configure,
            patch.object(containment, "prepare") as prepare,
        ):
            self.assertIsNone(containment.unavailable(self.bin, self.opencode(containment.SUPPORTED_VERSION)))
        configure.assert_not_called()
        prepare.assert_not_called()


@unittest.skipUnless(NATIVE, f"requires a real kernel sandbox: {NATIVE_PROBLEM}")
class UnicodeKernelTests(unittest.TestCase):
    def test_unicode_workspaces_allow_source_io_and_keep_protected_paths_denied(self):
        parent = ROOT / ".autocode"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="unicode-containment-", dir=parent) as temporary:
            for name in ("josé", "用户", '用户-"quoted"-\\path'):
                with self.subTest(name=name):
                    root = Path(temporary) / name
                    root.mkdir()
                    source = root / "源.txt"
                    source.write_text("source sentinel\n")
                    protected = root / "保護.txt"
                    protected.write_text("protected sentinel\n")
                    boundary = containment.prepare(root, allow_write=True, protected_paths=[protected])

                    def run(command, shell=boundary["shell"], workspace=root):
                        return subprocess.run(
                            [shell, "-c", command], cwd=workspace, capture_output=True, text=True, timeout=10
                        )

                    read = run(shlex.join(["/bin/cat", str(source)]))
                    self.assertEqual(0, read.returncode, read.stderr)
                    self.assertEqual("source sentinel\n", read.stdout)
                    written = root / "出力.txt"
                    write = run("printf output > " + shlex.quote(str(written)))
                    self.assertEqual(0, write.returncode, write.stderr)
                    self.assertEqual("output", written.read_text())
                    denied = run("printf changed > " + shlex.quote(str(protected)))
                    self.assertNotEqual(0, denied.returncode)
                    # Seatbelt's exact errno text, as before #709; bwrap's mount denial otherwise.
                    seatbelt = NATIVE == containment.SEATBELT
                    self.assertRegex(denied.stderr, "Operation not permitted" if seatbelt else DENIED)
                    self.assertEqual("protected sentinel\n", protected.read_text())


@unittest.skipUnless(NATIVE, f"requires a real kernel sandbox: {NATIVE_PROBLEM}")
class KernelTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".autocode"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="containment-tests-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.workspace = self.home / "project"
        self.workspace.mkdir()
        self.source = self.workspace / "source.py"
        self.source.write_text("VALUE = 42\n")
        self.outside = self.home / "forbidden.txt"
        self.outside.write_text("forbidden sentinel\n")
        self.runtime = self.workspace / "runtime"
        self.runtime.mkdir()
        self.code = self.runtime / "runner.py"
        self.code.write_text("runner authority\n")
        private = self.workspace / ".autocode"
        private.mkdir()
        self.state = private / "state.json"
        self.state.write_text('{"owned":"runner"}\n')
        self.events = private / "live.jsonl"
        self.events.write_text('{"type":"step_start"}\n')
        self.receipt = private / "accepted.json"
        self.receipt.write_text('{"accepted":true}\n')
        self.roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), (ROOT / "tools").resolve()]
        self.env = {**os.environ, "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin:/opt/homebrew/bin"}
        self.before = {
            p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (self.source, self.outside, self.code, self.state, self.events, self.receipt)
        }
        self.boundary = containment.prepare(
            self.workspace, read_roots=self.roots, protected_paths=[self.runtime], environment=self.env
        )

    def run_shell(self, command, boundary=None):
        result = subprocess.run(
            [(boundary or self.boundary)["shell"], "-c", command],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result

    def assert_denied(self, command, boundary=None):
        result = self.run_shell(command, boundary)
        self.assertNotEqual(0, result.returncode, (command, result.stdout, result.stderr))
        self.assertRegex(result.stderr, DENIED)

    def test_external_and_readonly_writes_are_kernel_denied(self):
        escape = self.home / "outside-created"
        commands = [
            shlex.join(["mkdir", str(escape)]),
            shlex.join(["cp", str(self.source), str(self.outside)]),
            "python -c "
            + shlex.quote("from pathlib import Path; Path(" + repr(str(self.outside)) + ').write_text("bad")'),
            ": > " + shlex.quote(str(self.outside)),
            "true && (: > " + shlex.quote(str(self.source)) + ")",
        ]
        for path in (self.state, self.events, self.receipt, self.code):
            commands.append(": > " + shlex.quote(str(path)))
        for command in commands:
            with self.subTest(command=command):
                self.assert_denied(command)
        self.assertFalse(escape.exists())
        self.assertEqual(self.before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.before})

    def test_status_view_names_the_boundary_recorded_for_a_contained_launch(self):
        # #709: a real launch's boundary blocks a planted write outside the workspace, and the status
        # view names the backend recorded for that launch. The view reads the stage record built from
        # the launch's manifest; it does not observe the denial itself.
        import autocode_provider_launch as provider_launch
        import autocode_run_view as run_view

        writer = containment.prepare(self.workspace, allow_write=True, read_roots=self.roots, environment=self.env)
        escape = self.home / "planted-escape"
        self.assert_denied(": > " + shlex.quote(str(escape)), writer)
        self.assertFalse(escape.exists())
        record = {
            "stage": "terra",
            "iteration": 1,
            "started_at": "2026-10-15T00:00:00+00:00",
            **provider_launch.stage_record({"engine": "opencode", "tool_containment": writer}),
        }
        named = run_view.view({"status": "RUNNING", "stages": [record]})["tool_containment_backend"]
        self.assertEqual(NATIVE, named["backend"])
        self.assertEqual(containment.backend_version(NATIVE), named["backend_version"])
        self.assertEqual(("terra", 1), (named["stage"], named["iteration"]))

    def test_symlink_escape_denied_even_from_writable_scratch(self):
        link = Path(self.boundary["scratch"]) / "escape"
        link.symlink_to(self.outside)
        self.assert_denied(": > " + shlex.quote(str(link)))
        self.assertEqual(self.before[self.outside], hashlib.sha256(self.outside.read_bytes()).hexdigest())

    def test_legitimate_reads_tests_and_stage_capture_work_without_client_secrets(self):
        test = self.workspace / "test_source.py"
        test.write_text(
            "import unittest\nfrom source import VALUE\n"
            "class SourceTest(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(VALUE, 42)\n"
        )
        result = self.run_shell("python -m unittest -v test_source")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)
        output = Path(self.boundary["scratch"]) / "capture.json"
        command = "python -c " + shlex.quote(
            "import json,os,pathlib,sys; "
            'assert pathlib.Path("source.py").read_text() == "VALUE = 42\\n"; '
            'assert "CONTAINMENT_TEST_SECRET" not in os.environ; '
            'assert os.environ.get("PYTHONNOUSERSITE") == "1" and sys.flags.no_user_site == 1; '
            "pathlib.Path(" + repr(str(output)) + ').write_text(json.dumps({"exit_code": 0}))'
        )
        with patch.dict(os.environ, {"CONTAINMENT_TEST_SECRET": "must-not-reach-tool"}):
            result = self.run_shell(command)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"exit_code": 0}, json.loads(output.read_text()))
        self.assertFalse((self.workspace / "__pycache__").exists())

    def test_public_capture_cli_uses_only_owned_stage_scratch(self):
        if type(self) is KernelTests:
            tools_ready = toolchain.discover(self.workspace, ["python -m autocode_cli capture"], self.env)
            self.boundary = containment.prepare(
                self.workspace,
                read_roots=[*self.roots, *tools_ready["read_roots"]],
                protected_paths=[self.runtime],
                environment=self.env,
            )
        output = Path(self.boundary["scratch"]) / "approved-capture.json"
        command = shlex.join(
            [
                "python",
                "-m",
                "autocode_cli",
                "capture",
                "--output",
                str(output),
                "--",
                "python",
                "-c",
                'print("actual captured command")',
            ]
        )
        result = self.run_shell(command)
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(output.read_text())
        self.assertEqual(0, receipt["exit_code"])
        self.assertEqual("actual captured command\n", Path(receipt["full_output"]).read_text())
        self.assertTrue(Path(receipt["full_output"]).is_relative_to(self.boundary["scratch"]))

    def test_builder_may_edit_app_not_state_runtime_or_other_stage(self):
        writer = containment.prepare(
            self.workspace,
            allow_write=True,
            read_roots=self.roots,
            protected_paths=[self.runtime],
            environment=self.env,
        )
        result = self.run_shell(
            "python -c " + shlex.quote('from pathlib import Path; Path("source.py").write_text("VALUE = 43\\n")'),
            writer,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        for path in (
            self.state,
            self.events,
            self.receipt,
            self.code,
            Path(self.boundary["scratch"]) / "other-stage.json",
        ):
            with self.subTest(path=path):
                self.assert_denied(": > " + shlex.quote(str(path)), writer)

    def test_outside_read_and_network_are_denied(self):
        self.assert_denied(
            "python -c "
            + shlex.quote("from pathlib import Path; print(Path(" + repr(str(self.outside)) + ").read_text())")
        )
        # A namespaced loopback (bubblewrap) refuses; it has no route anywhere else.
        address = "127.0.0.1" if NATIVE == containment.SEATBELT else "192.0.2.1"
        self.assert_denied("python -c " + shlex.quote(f'import socket; socket.socket().connect(("{address}", 9))'))

    def test_host_loopback_listener_is_unreachable(self):
        # A real listener, so a refusal shows the shell's network is not the host's.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            command = "python -c " + shlex.quote(f'import socket; socket.socket().connect(("127.0.0.1", {port}))')
            result = self.run_shell(command)
        self.assertNotEqual(0, result.returncode, result.stderr)
        self.assertRegex(result.stderr, DENIED + "|Connection refused")

    def test_hardlink_cannot_turn_runner_evidence_into_writable_scratch(self):
        link = Path(self.boundary["scratch"]) / "linked-state"
        command = shlex.join(["ln", str(self.state), str(link)]) + " && : > " + shlex.quote(str(link))
        self.assert_denied(command)
        self.assertEqual(self.before[self.state], hashlib.sha256(self.state.read_bytes()).hexdigest())

    def test_wrapper_ignores_poisoned_shell_startup_before_sandbox(self):
        marker = self.home / "startup-escaped"
        startup = self.home / "poison.sh"
        startup.write_text(": > " + shlex.quote(str(marker)) + "\n")
        env = {
            **os.environ,
            "BASH_ENV": str(startup),
            "ENV": str(startup),
            "SHELLOPTS": "xtrace",
            "PS4": "$(: > " + shlex.quote(str(marker)) + ")",
        }
        result = subprocess.run(
            [self.boundary["shell"], "-c", "exit 0"],
            cwd=self.workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(marker.exists())

    def test_manifest_verification_rejects_changed_files_and_symlink_ancestors(self):
        control = Path(self.boundary["profile"]).parent
        proof = control / "conformance.json"
        proof.write_text("{}")  # Integrity-only fixture, not native qualification.
        boundary = {**self.boundary, "native_version": containment.SUPPORTED_VERSION, "conformance": str(proof)}
        for name in ("profile", "shell", "conformance"):
            boundary[name + "_sha256"] = hashlib.sha256(Path(boundary[name]).read_bytes()).hexdigest()
        containment.verify(boundary)
        for key, value in (
            ("loopback_checks", ["claimed network permission"]),
            ("loopback_checks", []),
            ("loopback_authority", None),
            ("loopback_profile", ""),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(RuntimeError, "authority changed"):
                    containment.verify({**boundary, key: value})
        proof.write_text('{"changed":true}')
        with self.assertRaisesRegex(RuntimeError, "authority changed"):
            containment.verify(boundary)
        proof.write_text("{}")
        old = control.with_name(control.name + "-moved")
        control.rename(old)
        control.symlink_to(old, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "authority changed"):
            containment.verify(boundary)

    def planning_run_store(self):
        """This run, a sibling and a custom-named run, each with private state; and this run's planning/."""
        runs = self.workspace / ".autocode" / "runs"
        self.run_dir = runs / "20261015-own-run"
        private = {
            self.run_dir / "state.json": '{"private":"own run state"}\n',
            self.run_dir / "events.jsonl": '{"private":"own run events"}\n',
            runs / "20261014-sibling-run" / "state.json": '{"private":"sibling run state"}\n',
            runs / "custom-name" / "state.json": '{"private":"custom run state"}\n',
        }
        for path, text in private.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        artifact = self.run_dir / "planning" / "plan-1.json"
        artifact.parent.mkdir()
        artifact.write_text('{"predecessor":"approved plan"}\n')
        # The reader's verification copy starts from HEAD (NativeToolTests has committed one already).
        git = ["/usr/bin/git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        if not (self.workspace / ".git").exists():
            for args in (["init", "-q"], ["add", "source.py"], ["commit", "-qm", "fixture"]):
                subprocess.run([*git, *args], check=True)
        return runs, private, artifact

    def native_planning_boundary(self):
        """configure() for the request a native-isolated planning launch makes (#1250).

        The request comes from provider_launch.prepare. OpenCode is faked: its model-free debug
        calls answer with a readonly agent, and each bash-tool call runs through the real shell.
        """
        import autocode_native_role_isolation as isolation
        import autocode_provider_launch as provider_launch

        owner = object.__new__(isolation.NativeRoleIsolation)
        owner.containment_request = lambda **kwargs: {}
        descriptor = {"manifest": "/controlled/native-manifest.json", "manifest_sha256": "a" * 64}
        owner._descriptor = descriptor  # Controlled loader result; no native authority.
        requests = []

        def launch(*args, **kwargs):
            requests.append(kwargs["containment"])
            located = json.dumps({"native_role_isolation": descriptor})
            environment = {
                "AUTOCODE_NATIVE_ROLE_ISOLATION": json.dumps(descriptor),
                "AUTOCODE_TOOL_CONTAINMENT": located,
            }
            return ["opencode"], environment, {}

        adapter = SimpleNamespace(CONFIGURED=False, NAME="opencode", launch=launch, transport_version=lambda _: None)
        provider_launch.prepare(
            engine="opencode",
            adapter=adapter,
            role="glm",
            route_role="glm",
            workspace=self.workspace,
            run_dir=self.run_dir,
            session=None,
            model="controlled/model",
            effort="high",
            allow_write=False,
            planning=True,
            report=self.run_dir / "report.json",
            schema=self.run_dir / "schema.json",
            prompt_file=self.run_dir / "prompt.md",
            sandbox="read-only",
            transport_args=[],
            chatgpt=False,
            provider="opencode",
            settings={},
            native_role_isolation=owner,
            native_environment={"PATH": self.env["PATH"]},
        )
        fake = self.home / "fake-opencode" / "opencode"
        fake.parent.mkdir()
        fake.write_text("#!/bin/sh\nexit 99\n")
        fake.chmod(0o755)
        agent = "autocode_glm_plan_" + "0" * 32
        environment = {
            **self.env,
            "PATH": str(fake.parent) + ":" + self.env["PATH"],
            "OPENCODE_CONFIG_CONTENT": json.dumps({"agent": {agent: {"permission": {"bash": "allow"}}}}),
        }
        readonly_agent = {
            "tools": {"bash": True, "read": False, "edit": False},
            "permission": [
                {"permission": "*", "pattern": "*", "action": "deny"},
                {"permission": "bash", "pattern": "*", "action": "allow"},
            ],
        }
        real_run = subprocess.run

        def fake_opencode(argv, **kwargs):
            if argv[0] != str(fake):
                return real_run(argv, **kwargs)
            if argv[1:] == ["--version"]:
                return subprocess.CompletedProcess(argv, 0, containment.SUPPORTED_VERSION + "\n", "")
            if "--tool" not in argv:
                return subprocess.CompletedProcess(argv, 0, json.dumps(readonly_agent), "")
            params = json.loads(argv[argv.index("--params") + 1])
            shell = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])["shell"]
            ran = real_run([shell, "-c", params["command"]], cwd=params["workdir"], capture_output=True, text=True)
            output = {"metadata": {"exit": ran.returncode}, "output": ran.stdout + ran.stderr}
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"input": {"command": params["command"]}, "result": output}), ""
            )

        with (
            patch.object(containment.subprocess, "run", side_effect=fake_opencode),
            patch.object(toolchain, "discover", return_value={"read_roots": [], "probes": []}),
        ):
            _, boundary = containment.configure(
                ["opencode", "run", "--agent", agent, "--model", "controlled/model"],
                environment,
                self.workspace,
                allow_write=False,
                request=requests[0],
            )
        return boundary

    def test_native_planning_shell_cannot_read_private_run_state(self):
        # #1250: own, sibling and custom-named runs' state is unreadable through every route tried;
        # source, the verification copy and this run's planning/ artifacts stay readable.
        runs, private, artifact = self.planning_run_store()
        before = {path: path.read_bytes() for path in [*private, artifact]}
        boundary = self.native_planning_boundary()
        scratch = Path(boundary["scratch"])
        for number, path in enumerate(private):
            alias = scratch / f"alias-{number}"
            for command in (
                shlex.join(["cat", str(path)]),
                shlex.join(["cat", os.path.relpath(path, self.workspace)]),
                "python -c " + shlex.quote(f"print(open({str(path)!r}).read())"),
                shlex.join(["cp", str(path), str(scratch / f"copy-{number}")]),
                shlex.join(["ln", str(path), str(scratch / f"link-{number}")]),
                shlex.join(["ln", "-s", str(path), str(alias)]) + " && " + shlex.join(["cat", str(alias)]),
            ):
                with self.subTest(command=command):
                    result = self.run_shell(command, boundary)
                    self.assertNotEqual(0, result.returncode, (command, result.stdout))
                    self.assertNotIn('"private"', result.stdout + result.stderr)
        listing = self.run_shell("ls -aR .autocode/runs; grep -rh private .autocode/runs", boundary)
        for name in ("20261014-sibling-run", "custom-name", "state.json", "events.jsonl", '"private"'):
            self.assertNotIn(name, listing.stdout + listing.stderr)
        self.assertEqual("VALUE = 42\n", self.run_shell("cat source.py", boundary).stdout)
        read = self.run_shell(shlex.join(["cat", str(artifact)]), boundary)
        self.assertEqual((0, '{"predecessor":"approved plan"}\n'), (read.returncode, read.stdout), read.stderr)
        self.assertIn("plan-1.json", self.run_shell(shlex.join(["ls", str(artifact.parent)]), boundary).stdout)
        copy = scratch / "verification" / "source.py"
        self.assertEqual("VALUE = 42\n", self.run_shell(shlex.join(["cat", str(copy)]), boundary).stdout)
        self.assertEqual(0, self.run_shell(": > " + shlex.quote(str(scratch / "note")), boundary).returncode)
        for path in (self.run_dir / "state.json", artifact, runs / "planted-run"):
            self.assert_denied(": > " + shlex.quote(str(path)), boundary)
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertFalse((runs / "planted-run").exists())
        # The manifest names the denial, the actual-shell proof is retained, and its sentinel is gone.
        reads = {"run_dir": str(self.run_dir), "denied": [str(runs)], "readable": [str(artifact.parent)]}
        self.assertEqual(reads, boundary["private_reads"])
        proof = json.loads(Path(boundary["conformance"]).read_text())["private_reads"]
        self.assertEqual(
            {**reads, "listed": [str(artifact.parent)], "exit": 0}, {k: proof[k] for k in (*reads, "listed", "exit")}
        )
        self.assertEqual([], [path.name for path in self.run_dir.iterdir() if "sentinel" in path.name])
        containment.verify(boundary)

    def test_native_planning_boundary_verification_refuses_a_weakened_denial(self):
        runs, _, _ = self.planning_run_store()
        boundary = self.native_planning_boundary()
        reads = boundary["private_reads"]
        (runs / "sibling").mkdir()
        for changed in (
            {**boundary, "private_reads": {**reads, "denied": []}},
            {**boundary, "private_reads": {**reads, "readable": [str(runs)]}},
            {**boundary, "private_reads": containment.private_run_reads(self.workspace, runs / "sibling")},
            {**boundary, "allow_write": True},
        ):
            with (
                self.subTest(changed=changed),
                self.assertRaisesRegex(RuntimeError, "Private run-state read denial changed"),
            ):
                containment.verify(changed)
        # Profile bytes that let the shell read this run's state.json, even when pinned again, are refused.
        profile = Path(boundary["profile"])
        original = profile.read_bytes()
        state, scratch = self.run_dir / "state.json", Path(boundary["scratch"])
        if NATIVE == containment.SEATBELT:
            allow = "  (allow file-read* (subpath " + json.dumps(str(runs), ensure_ascii=False) + "))\n"
            weakened = {
                "without the denial": (
                    original.replace(containment._private_read_rules(reads)[0].encode() + b"\n", b""),
                    state,
                ),
                "an indented later allow": (original + allow.encode(), state),
            }
        else:
            values = original.split(b"\0")[:-1]
            hidden = values.index(b"--tmpfs")

            def arguments(changed):
                return b"".join(value + b"\0" for value in changed)

            def bound(source, target):
                return arguments([*values[:-2], b"--ro-bind", os.fsencode(source), os.fsencode(target), *values[-2:]])

            # Outside the workspace, so the verification copy's source is unchanged.
            (self.home / "elsewhere").mkdir()
            (self.home / "elsewhere/link").symlink_to(self.run_dir, target_is_directory=True)
            weakened = {
                "without the tmpfs": (arguments(values[:hidden] + values[hidden + 2 : -4] + values[-2:]), state),
                "the workspace bound elsewhere": (
                    bound(self.workspace, scratch / "mirror"),
                    scratch / "mirror" / state.relative_to(self.workspace),
                ),
                "a dot-dot source": (
                    bound(self.run_dir / "planning/../state.json", scratch / "dotdot"),
                    scratch / "dotdot",
                ),
                # Lexically <home>/elsewhere; the kernel opens the link target's parent, the store.
                "a symlink and dot-dot source": (
                    bound(self.home / "elsewhere/link/..", scratch / "peek"),
                    scratch / "peek" / self.run_dir.name / "state.json",
                ),
            }
        profile.chmod(0o600)
        for name, (changed, shown) in weakened.items():
            with self.subTest(name=name):
                profile.write_bytes(changed)
                repinned = {**boundary, "profile_sha256": hashlib.sha256(changed).hexdigest()}
                self.assertIn('"private"', self.run_shell(shlex.join(["cat", str(shown)]), repinned).stdout)
                with self.assertRaisesRegex(RuntimeError, "Private run-state read denial changed"):
                    containment.verify(repinned)
        profile.write_bytes(original)
        if NATIVE == containment.BUBBLEWRAP:
            # A writable bind of the workspace lets the shell rename the store out from under
            # the tmpfs: a fresh sandbox then has an empty tmpfs over the new, empty runs
            # directory, and the moved store is plainly readable (#1250). verify() must refuse
            # the re-pinned profile; before this fix its parser never looked at writability.
            control = Path(boundary["profile"]).parent.name
            marker = b"\x00".join(os.fsencode(str(self.workspace)) for _ in range(2))
            writable = original.replace(b"--ro-bind\x00" + marker + b"\x00", b"--bind\x00" + marker + b"\x00", 1)
            self.assertNotEqual(original, writable)
            profile.write_bytes(writable)
            repinned = {**boundary, "profile_sha256": hashlib.sha256(writable).hexdigest()}
            move = (
                shlex.join(["mv", ".autocode", ".moved"])
                + " && "
                + shlex.join(["mkdir", ".autocode"])
                + " && "
                + shlex.join(["mv", ".moved/" + control, ".autocode/"])
            )
            self.assertEqual(0, self.run_shell(move, repinned).returncode)
            moved_state = (
                self.workspace / ".moved" / self.run_dir.relative_to(self.workspace / ".autocode") / "state.json"
            )
            self.assertIn('"private"', self.run_shell(shlex.join(["cat", str(moved_state)]), repinned).stdout)
            # Restore the store from the test process. The fresh sandbox's --tmpfs made bwrap
            # create an empty runs/ mount point through the writable bind, so each child is
            # renamed onto its name, replacing that empty directory (moving it there would nest
            # the store inside it, and verify() would then refuse for the missing run instead).
            restored = self.workspace / ".moved"
            for child in restored.iterdir():
                child.replace(self.workspace / ".autocode" / child.name)
            restored.rmdir()
            self.assertEqual('{"private":"own run state"}\n', state.read_text())
            # The restored store passes verify() with the original profile, so only the parse
            # of the writable re-pin can refuse it.
            profile.write_bytes(original)
            containment.verify(boundary)
            profile.write_bytes(writable)
            with self.assertRaisesRegex(RuntimeError, "Private run-state read denial changed"):
                containment.verify(repinned)
            profile.write_bytes(original)
        # So is a conformance proof without the actual-shell denial.
        proof = Path(boundary["conformance"])
        retained = json.loads(proof.read_text())
        del retained["private_reads"]
        proof.write_text(json.dumps(retained))
        repinned = {**boundary, "conformance_sha256": hashlib.sha256(proof.read_bytes()).hexdigest()}
        with self.assertRaisesRegex(RuntimeError, "Private run-state read denial changed"):
            containment.verify(repinned)


@unittest.skipUnless(
    NATIVE == containment.BUBBLEWRAP, f"requires working Linux bubblewrap: {NATIVE_PROBLEM or sys.platform}"
)
class BubblewrapKernelTests(unittest.TestCase):
    """What only the Linux backend needs to prove: identity, home, secrets and mount points."""

    setUp = KernelTests.setUp
    run_shell = KernelTests.run_shell
    assert_denied = KernelTests.assert_denied

    def test_manifest_names_the_backend_and_its_version(self):
        self.assertEqual(containment.BUBBLEWRAP, self.boundary["backend"])
        self.assertRegex(self.boundary["backend_version"], r"^bubblewrap \d")
        self.assertEqual("policy.bwrap", Path(self.boundary["profile"]).name)

    def test_planted_escape_to_home_and_tmp_is_blocked(self):
        name = "autocode-containment-escape-" + Path(self.temp.name).name
        private = Path.home() / (name + "-private")
        private.write_text("home secret\n")
        for path in (private, Path.home() / name, Path("/tmp") / name):
            self.addCleanup(path.unlink, missing_ok=True)
        for target in (Path.home() / name, Path("/tmp") / name):
            with self.subTest(target=target):
                self.assert_denied(": > " + shlex.quote(str(target)))
                self.assertFalse(target.exists())
        # Home appears only as the path to a mounted authority (CI checks out under it), never its files.
        read = self.run_shell(shlex.join(["cat", str(private)]))
        self.assertNotEqual(0, read.returncode)
        self.assertNotIn("home secret", read.stdout)
        self.assertNotIn(private.name, self.run_shell(shlex.join(["ls", "-a", str(Path.home())])).stdout)

    def test_secret_file_read_is_denied(self):
        secret = self.workspace / ".env"
        secret.write_text("TOKEN=must-not-reach-tool\n")
        boundary = containment.prepare(self.workspace, read_roots=self.roots, environment=self.env)
        result = self.run_shell(shlex.join(["cat", str(secret)]), boundary)
        self.assertNotIn("must-not-reach-tool", result.stdout + result.stderr)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Permission denied", result.stderr)
        self.assertEqual("TOKEN=must-not-reach-tool\n", secret.read_text())

    def test_builder_creates_new_files_without_creating_protected_mount_points(self):
        writer = containment.prepare(self.workspace, allow_write=True, read_roots=self.roots, environment=self.env)
        result = self.run_shell("mkdir -p src && printf new > src/app.py && printf top > README.md", writer)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("new", (self.workspace / "src/app.py").read_text())
        for name in (".git", ".opencode", "opencode.json", "opencode.jsonc"):
            self.assertFalse((self.workspace / name).exists(), name)

    def test_builder_cannot_move_a_nested_protected_path_away_and_recreate_it(self):
        # Seatbelt denies the protected name; a mount follows its directory when that is renamed.
        nested = self.workspace / "vendor/tool/runtime"
        nested.mkdir(parents=True)
        (nested / "runner.py").write_text("runner authority\n")
        writer = containment.prepare(
            self.workspace, allow_write=True, read_roots=self.roots, protected_paths=[nested], environment=self.env
        )
        moved = self.run_shell(
            "mv vendor vendor-old && mkdir -p vendor/tool/runtime && printf forged > vendor/tool/runtime/runner.py",
            writer,
        )
        self.assertNotEqual(0, moved.returncode, moved.stderr)
        self.assertIn("Device or resource busy", moved.stderr)
        self.assertEqual("runner authority\n", (nested / "runner.py").read_text())
        self.assertFalse((self.workspace / "vendor-old").exists())
        # The directories holding it stay writable.
        wrote = self.run_shell("printf note > vendor/tool/note.txt", writer)
        self.assertEqual(0, wrote.returncode, wrote.stderr)
        self.assertEqual("note", (self.workspace / "vendor/tool/note.txt").read_text())

    def test_writer_that_creates_a_watched_protected_name_runs_no_further_command(self):
        # bwrap cannot mount over a name that does not exist; Seatbelt denies creating it.
        writer = containment.prepare(self.workspace, allow_write=True, read_roots=self.roots, environment=self.env)
        planted = self.workspace / "opencode.json"
        self.assertIn(str(planted), writer["watched_paths"])
        created = self.run_shell("printf '{}' > opencode.json", writer)
        self.assertEqual(126, created.returncode, created.stderr)
        self.assertIn("protected path " + str(planted), created.stderr)
        later = self.run_shell("printf later > later.txt", writer)
        self.assertEqual(126, later.returncode, later.stderr)
        self.assertFalse((self.workspace / "later.txt").exists())

    def test_reader_verification_copy_is_readable_but_not_writable(self):
        git = ["/usr/bin/git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "add", "source.py"], check=True)
        subprocess.run([*git, "commit", "-qm", "fixture"], check=True)
        reader = containment.prepare(
            self.workspace,
            read_roots=self.roots,
            environment=self.env,
            verification_copy=True,
            source_paths=["source.py"],
        )
        copy = Path(reader["scratch"]) / "verification" / "source.py"
        self.assertEqual("VALUE = 42\n", self.run_shell(shlex.join(["cat", str(copy)]), reader).stdout)
        self.assert_denied(": > " + shlex.quote(str(copy)), reader)
        self.assert_denied(": > " + shlex.quote(str(copy.parent / "new.py")), reader)
        self.assertEqual(0, self.run_shell(": > " + shlex.quote(reader["scratch"] + "/note"), reader).returncode)
        # The whole copy, Git objects included, is one read-only mount.
        arguments = Path(reader["profile"]).read_bytes().split(b"\0")
        self.assertEqual(2, sum(value.startswith(os.fsencode(copy.parent)) for value in arguments))
        self.assertEqual("VALUE = 42\n", copy.read_text())


@unittest.skipUnless(
    sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(), "requires real macOS Seatbelt enforcement"
)
class LoopbackLimitTests(unittest.TestCase):
    setUp = KernelTests.setUp
    run_shell = KernelTests.run_shell

    def test_default_policy_denies_ephemeral_http_listener(self):
        (self.workspace / "test_http.py").write_text(
            "import http.server, unittest\n"
            "class HTTP(unittest.TestCase):\n"
            " def test_listener(self):\n"
            '  with http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler): pass\n'
        )
        result = self.run_shell(shlex.join([sys.executable, "-m", "unittest", "-v", "test_http"]))
        self.assertEqual(1, result.returncode)
        self.assertIn("PermissionError: [Errno 1] Operation not permitted", result.stderr)
        self.assertEqual(self.before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.before})

    def test_seatbelt_localhost_is_not_an_exact_loopback_ip_filter(self):
        import ipaddress

        import psutil

        addresses = [
            a.address
            for values in psutil.net_if_addrs().values()
            for a in values
            if a.family == socket.AF_INET and not ipaddress.ip_address(a.address).is_loopback
        ]
        if not addresses:
            self.skipTest("No owned non-loopback interface available to test Seatbelt address matching")
        with socket.socket() as listener:
            listener.bind((addresses[0], 0))
            listener.listen()
            listener.settimeout(2)
            code = (
                "import socket; s=socket.socket(); s.settimeout(1); s.connect("
                + repr(listener.getsockname())
                + '); print("connected-owned-nonloopback")'
            )
            denied = self.run_shell(shlex.join([sys.executable, "-c", code]))
            self.assertEqual(1, denied.returncode)
            self.assertIn("Operation not permitted", denied.stderr)
            profile = containment.policy(self.workspace, self.boundary["scratch"], read_roots=self.roots)
            # This is a rejected candidate profile, NOT a production capability.
            candidate = profile + '(allow network-outbound (remote tcp "localhost:*"))\n'
            result = subprocess.run(
                ["/usr/bin/sandbox-exec", "-p", candidate, sys.executable, "-B", "-c", code],
                cwd=self.workspace,
                env={"PYTHONNOUSERSITE": "1"},
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("connected-owned-nonloopback\n", result.stdout)
            accepted, _ = listener.accept()
            accepted.close()

    def test_literal_loopback_ip_is_not_supported_by_sbpl_network_filter(self):
        for host in ("127.0.0.1:*", "[::1]:*"):
            with self.subTest(host=host):
                profile = containment.policy(self.workspace, self.boundary["scratch"], read_roots=self.roots)
                profile += "(allow network-outbound (remote tcp " + json.dumps(host) + "))"
                result = subprocess.run(
                    ["/usr/bin/sandbox-exec", "-p", profile, "/usr/bin/true"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("host must be * or localhost", result.stderr)


@unittest.skipUnless(
    os.environ.get("AUTOCODE_NATIVE_CONTAINMENT_TEST") == "1", "opt-in model-free OpenCode debug conformance"
)
class NativeToolTests(KernelTests):
    def setUp(self):
        super().setUp()
        from autocode_preflight_worker import execute

        # The pinned OpenCode wherever it is installed (Homebrew is on the fixture PATH already).
        located = shutil.which("opencode")
        if located:
            self.env["PATH"] = str(Path(located).parent) + ":" + self.env["PATH"]
        # A task workspace is a Git checkout: the toolchain probe runs `git status` and the
        # reader's verification copy starts from HEAD, so the test it runs exists before launch.
        (self.workspace / "test_source.py").write_text(
            "import unittest\nfrom source import VALUE\n"
            "class SourceTest(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(VALUE, 42)\n"
        )
        git = ["/usr/bin/git", "-C", str(self.workspace), "-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        for args in (["init", "-q"], ["add", "source.py", "test_source.py"], ["commit", "-qm", "fixture"]):
            subprocess.run([*git, *args], check=True)

        self.execute = execute
        command, env, _ = opencode.launch(
            "investigate_stuck",
            self.workspace,
            self.workspace / ".autocode" / "run",
            None,
            "zai-coding-plan/glm-5.3",
            "high",
            False,
            env=self.env,
            containment={"read_roots": [str(p) for p in self.roots], "protected_paths": [str(self.runtime)]},
        )
        self.worker = {
            "command": command,
            "environment": env,
            "engine": "opencode",
            "configured": False,
            "model": "zai-coding-plan/glm-5.3",
        }
        self.boundary = json.loads(env["AUTOCODE_TOOL_CONTAINMENT"])
        self.counter = 0

    def test_final_boundary_in_one_native_call(self):
        """Small retained native gate, including shell/env startup hardening."""
        targets = [self.source, self.outside, self.state, self.events, self.receipt, self.code]
        escape = Path(self.boundary["scratch"]) / "escape-final"
        escape.symlink_to(self.outside)
        programs = [
            ["mkdir", str(self.home / "outside-final")],
            ["cp", str(self.source), str(self.outside)],
            *[
                ["python", "-c", "from pathlib import Path; Path(" + repr(str(p)) + ').write_text("bad")']
                for p in [*targets, escape]
            ],
            ["bash", "-c", "true && (: > " + shlex.quote(str(self.outside)) + ")"],
        ]
        capture = Path(self.boundary["scratch"]) / "final-capture.json"
        # Seatbelt's exact errno text, as before #709; bwrap's mount denial otherwise.
        denied = "Operation not permitted" if NATIVE == containment.SEATBELT else DENIED
        code = (
            "import subprocess,json,pathlib,re; results=[]\n"
            "for argv in " + repr(programs) + ":\n"
            " r=subprocess.run(argv,capture_output=True,text=True)\n"
            " assert r.returncode != 0 and re.search(" + repr(denied) + ", r.stderr), (argv,r)\n"
            ' results.append({"argv":argv,"exit":r.returncode,"stderr":r.stderr})\n'
            'subprocess.run(["python","-m","unittest","-v","test_source"],check=True)\n'
            "subprocess.run("
            + repr(
                [
                    "python",
                    "-m",
                    "autocode_cli",
                    "capture",
                    "--output",
                    str(capture),
                    "--",
                    "python",
                    "-c",
                    'print("approved final capture")',
                ]
            )
            + ",check=True)\n"
            "print(json.dumps(results))\n"
        )
        result = self.run_shell("python -c " + shlex.quote(code))
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual(self.before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.before})
        self.assertEqual(0, json.loads(capture.read_text())["exit_code"])
        if os.environ.get("AUTOCODE_CONTAINMENT_RETAIN_PROOF") == "1":
            destination = (
                ROOT / ".autocode" / ("native-containment-proof-" + Path(self.boundary["profile"]).parent.name)
            )
            destination.mkdir()
            # Copy only this runner-owned fixture's proof, never auth/config data.
            import shutil

            shutil.copytree(self.home, destination / "fixture")
            (destination / "result.json").write_text(
                json.dumps(
                    {
                        "native_version": containment.SUPPORTED_VERSION,
                        "passed": True,
                        "command": result.args,
                        "output": result.stdout,
                        "source_and_forbidden_hashes_unchanged": True,
                        "before": {str(p): value for p, value in self.before.items()},
                        "runtime_sha256": hashlib.sha256(Path(containment.__file__).read_bytes()).hexdigest(),
                    },
                    indent=2,
                )
            )
            print("RETAINED_NATIVE_PROOF=" + str(destination))

    def run_shell(self, command, boundary=None):
        if boundary is not None:
            # Builder's kernel policy has separate direct-process coverage.
            return super().run_shell(command, boundary)
        self.counter += 1
        output = self.home / ("native-" + str(self.counter) + ".json")
        argv = ["bash", "-c", command]
        result, text = self.execute(self.worker, argv, self.workspace, output, timeout=60)
        raw = json.loads(output.read_text())
        self.assertEqual(shlex.join(argv), raw["input"]["command"])
        self.assertIs(type(raw["result"]["metadata"]["exit"]), int)
        print(
            json.dumps(
                {"native_command": shlex.join(argv), "exit": result["exit_code"], "output": text}, sort_keys=True
            )
        )
        return subprocess.CompletedProcess(argv, result["exit_code"], text, text)


if __name__ == "__main__":
    unittest.main()
