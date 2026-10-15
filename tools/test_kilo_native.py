"""Focused fake-first checks for the separate readonly native Kilo boundary."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from . import autocode_kilo_isolation as isolation
from . import autocode_kilo_native as rules
from . import autocode_kilo_source_relay as relay
from . import autocode_tool_containment as containment
from . import autocode_util as util
from .providers.command import CommandProvider
from .providers.kilocode import KiloProvider


class KiloNativeTests(unittest.TestCase):
    def fixture(self):
        identity = {
            "engine": "kilocode",
            "identity_version": 1,
            "version": rules.VERSION,
            "executable": "/fixture/kilo",
            "config_path": "/fixture/kilo.toml",
            "config_sha256": "1" * 64,
        }
        profile = {"mode": rules.MODE, "kilo": {"transport_identity_sha256": util.digest(identity)}}
        settings = {
            "engine": "opencode",
            "roles": {
                "sol": {"engine": "opencode", "provider": "kilocode", "model": rules.MODEL, "reasoning_effort": "high"},
                "plan_reviewer": {"provider": "opencode", "model": "mimo/mimo-v2.6-pro", "reasoning_effort": "high"},
            },
            "transport_identities": {"role:sol": {"provider": "kilocode", "identity": identity}},
        }
        return profile, settings

    def test_only_explicit_former_openai_role_selects_kilo(self):
        profile, settings = self.fixture()
        binding, identity = rules.selected(profile, settings, "sol")
        self.assertEqual(identity["engine"], "kilocode")
        self.assertIsNone(rules.selected(profile, settings, "plan_reviewer"))
        settings["roles"]["plan_reviewer"]["provider"] = "kilocode"
        with self.assertRaisesRegex(ValueError, "former OpenAI"):
            rules.selected(profile, settings, "plan_reviewer")

    def test_saved_high_model_and_transport_cannot_be_substituted(self):
        original, settings = self.fixture()
        for key, value in (("reasoning_effort", "max"), ("model", "anthropic/another-model")):
            changed = copy.deepcopy(settings)
            changed["roles"]["sol"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                rules.selected(original, changed, "sol")
        changed = copy.deepcopy(settings)
        changed["transport_identities"]["role:sol"]["identity"]["version"] = "7.8.3"
        with self.assertRaisesRegex(ValueError, "immutable"):
            rules.selected(original, changed, "sol")

    def test_profile_does_not_fall_back_when_review_route_is_missing(self):
        profile, settings = self.fixture()
        settings["roles"]["sol"]["provider"] = "opencode"
        with self.assertRaisesRegex(ValueError, "explicit saved Kilo"):
            rules.selected(profile, settings, "sol")

    def test_only_proven_limit_input_undefined_is_reinstated(self):
        compiled = {"family": "claude-opus", "limit": {"context": 1000000, "output": 128000}}
        observed = {"family": "claude-opus", "limit": {"context": 1000000, "input": relay.UNDEFINED, "output": 128000}}
        self.assertEqual(isolation.validate_model_projection(observed, compiled)["family"], "claude-opus")
        for changed in (
            {**observed, "another": relay.UNDEFINED},
            {"family": "claude-opus", "limit": {"context": 1000000, "input": None, "output": 128000}},
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "catalogue model changed"):
                isolation.validate_model_projection(changed, compiled)

    def test_only_exact_reconciled_build_shape_preserves_original_failure(self):
        result = {
            "kind": isolation.RECONCILED_BUILD_KIND,
            "genuine_compiled_binary": {"sha256": "1" * 64},
            "actual_original_owner_native_wire_model_image_acceptance": None,
            "original_worker_actual_exit": 1,
            "original_build_actual_exit": 0,
            "original_compiled_models_actual_exit": 0,
            "readonly_reconciliation_actual_exit": 0,
        }
        isolation.validate_build_result(result, "1" * 64)
        for key, value in (
            ("original_worker_actual_exit", 0),
            ("readonly_reconciliation_actual_exit", 1),
            ("original_build_actual_exit", False),
        ):
            changed = {**result, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "original worker1"):
                isolation.validate_build_result(changed, "1" * 64)
        with self.assertRaisesRegex(ValueError, "actual full compiled SDK"):
            isolation.validate_build_result({**result, "kind": isolation.RECONCILED_BUILD_KIND + "_READY"}, "1" * 64)

    def test_copied_build_evidence_keeps_the_original_root_peer_join(self):
        source = {"path": "/private/tmp/original/result.json", "sha256": "1" * 64}
        row = {"decision": "ACCEPT_ACTUAL_FULL_SDK_BUILD_ONLY", "errors": [], "result": source}
        proof = {"root": copy.deepcopy(row), "peer": copy.deepcopy(row)}
        copied = {"path": ".autocode/kilo-sdk/build-acceptance/result.json", "sha256": "1" * 64}
        isolation.validate_copied_build_acceptance(proof, copied)
        for changed in ({**source, "path": "/private/tmp/another/result.json"}, {**source, "sha256": "2" * 64}):
            bad = copy.deepcopy(proof)
            bad["peer"]["result"] = changed
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "exact staged copy"):
                isolation.validate_copied_build_acceptance(bad, copied)
        with self.assertRaisesRegex(ValueError, "exact staged copy"):
            isolation.validate_copied_build_acceptance(proof, {**copied, "sha256": "2" * 64})

    def test_configured_facade_cannot_borrow_native_authority(self):
        provider = object.__new__(KiloProvider)
        with self.assertRaisesRegex(ValueError, "exact original Kilo owner"):
            provider.launch(
                "sol", "/fixture", "/fixture/run", None, rules.MODEL, "high", False, isolation=Mock(), containment={}
            )

    def test_ordinary_configured_launch_preserves_its_existing_facade(self):
        provider = object.__new__(KiloProvider)
        with patch.object(CommandProvider, "launch", return_value=("command", "env", "overrides")) as ordinary:
            result = provider.launch("sol", "/fixture", "/fixture/run", None, rules.MODEL, "high", False)
        self.assertEqual(result, ("command", "env", "overrides"))
        ordinary.assert_called_once()

    def test_whole_client_policy_constructs_without_relaxing_shell_scratch_contract(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve()
            target = root / ".autocode/runs/fake/native-kilo-owned"
            target.mkdir(parents=True)
            control = root / ".autocode/tool-containment-fixture"
            control.mkdir()
            (control / "scratch").mkdir()
            environment = {}
            for key, name in (
                ("XDG_DATA_HOME", "data"),
                ("XDG_CACHE_HOME", "cache"),
                ("XDG_STATE_HOME", "state"),
                ("TMPDIR", "tmp"),
            ):
                path = target / name
                path.mkdir()
                environment[key] = str(path)
            boundary = {
                "workspace": str(root),
                "profile": str(control / "policy.sb"),
                "scratch": str(control / "scratch"),
            }
            isolation._relocate_client_runtime(boundary, environment)
            request = {"read_roots": [], "protected_paths": [str(root / ".autocode/runs/fake")]}
            text = isolation.whole_client_policy(
                root, target, boundary, request, environment, "/private/tmp/ac-test/source.sock"
            )
            self.assertIn("(deny default)", text)
            self.assertIn('(remote unix-socket (path-literal "/private/tmp/ac-test/source.sock"))', text)
            self.assertNotIn("remote ip", text)
            network_rules = [line for line in text.splitlines() if "network-outbound" in line]
            self.assertEqual(
                network_rules,
                [
                    "(deny network-outbound (with errno EACCES) (remote tcp))",
                    '(allow network-outbound (remote unix-socket (path-literal "/private/tmp/ac-test/source.sock")))',
                ],
            )
            self.assertIn("(require-not (subpath " + '"' + str(control), text)
            grants = [line for line in text.splitlines() if line.startswith("(allow file-write*")]
            self.assertEqual(
                grants,
                [
                    '(allow file-write* (literal "/dev/null") )',
                    '(allow file-write* (subpath "' + str(control / "scratch") + '"))',
                ],
            )
            for name in ("data", "cache", "state", "tmp", "config", "sdk"):
                self.assertNotIn('(subpath "' + str(target / name) + '")', "\n".join(grants))
            with self.assertRaisesRegex(ValueError, "exact descendants"):
                containment._render_policy(root, target / "tmp", write_roots=[target / "tmp"])
            # The pure converter uses the same renderer with its genuine parent
            # target; tmp is a descendant, never the scratch itself.
            self.assertIn(
                "(deny default)",
                containment._render_policy(root, target, read_workspace=False, write_roots=[target / "tmp"]),
            )

    def test_inherited_shell_preserves_safe_environment_and_refuses_changed_shape(self):
        profile = "/private/tmp/owned/policy.sb"
        raw = (
            '#!/bin/sh -p\nif [ "$#" -ne 2 ] || [ "$1" != "-c" ]; then exit 126; fi\n'
            "exec /usr/bin/env -i HOME=/private/tmp/owned/scratch "
            "AUTOCODE_VERIFICATION_COPY=/private/tmp/owned/copy.json AUTOCODE_CAPTURE_CONTEXT=nonce "
            "/usr/bin/sandbox-exec -f " + profile + ' /bin/bash --noprofile --norc -c "$2"\n'
        ).encode()
        actual = isolation._inherited_shell_body(raw, profile)
        self.assertEqual(actual, raw.replace(("/usr/bin/sandbox-exec -f " + profile + " ").encode(), b""))
        for changed in (
            raw.replace(b"env -i", b"env"),
            raw.replace(b"--norc", b"--rcfile /tmp/rc"),
            raw.replace(b"/owned/policy.sb", b"/foreign/policy.sb"),
            raw + b"exit 0\n",
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                isolation._inherited_shell_body(changed, profile)

    def test_scratch_runtime_mapping_refuses_stage_path_or_alias(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve()
            control = root / ".autocode/tool-containment-owned"
            (control / "scratch").mkdir(parents=True)
            env = {}
            runtime = isolation._relocate_client_runtime(
                {"workspace": str(root), "profile": str(control / "policy.sb"), "scratch": str(control / "scratch")},
                env,
            )
            for key, name in isolation.MUTABLE_DIRECTORIES.items():
                self.assertEqual(env[key], str(runtime / name))
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, "exceeds original scratch"):
                    isolation._require_runtime_mapping({**env, key: str(root)}, runtime)
            original = runtime / "tmp"
            original.rmdir()
            original.symlink_to(runtime / "data")
            with self.assertRaisesRegex(ValueError, "exceeds original scratch"):
                isolation._require_runtime_mapping(env, runtime)

    def test_inherited_policy_preserves_authenticated_verification_input_denials(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve()
            target = root / ".autocode/runs/fake/native-kilo-owned"
            target.mkdir(parents=True)
            control = root / ".autocode/tool-containment-owned"
            tree = control / "scratch/verification"
            (tree / "src").mkdir(parents=True)
            source = tree / "src/input.py"
            source.write_bytes(b"protected copied source")
            dependency = root / "dependency"
            dependency.write_bytes(b"original dependency")
            link = tree / "dependency-link"
            link.symlink_to(dependency)
            manifest = control / "verification-copy.json"
            value = {
                "version": 1,
                "workspace": str(root),
                "tree": str(tree),
                "files": {"src/input.py": util.file_hash(source), "dependency-link": "symlink:" + str(dependency)},
                "directories": ["src"],
            }
            manifest.write_text(json.dumps(value))
            boundary = {
                "workspace": str(root),
                "profile": str(control / "policy.sb"),
                "scratch": str(control / "scratch"),
                "verification_copy": str(manifest),
                "verification_copy_sha256": util.file_hash(manifest),
            }
            env = {}
            isolation._relocate_client_runtime(boundary, env)
            text = isolation.whole_client_policy(
                root,
                target,
                boundary,
                {"read_roots": [], "protected_paths": []},
                env,
                "/private/tmp/ac-test/source.sock",
            )
            for path in (tree, tree / "src", source, link):
                self.assertIn('(deny file-write* (literal "' + str(path) + '"))', text)
            self.assertNotIn('(deny file-write* (literal "' + str(dependency) + '"))', text)
            self.assertIn('(allow file-write* (subpath "' + str(control / "scratch") + '"))', text)
            manifest.write_text(json.dumps({**value, "directories": []}))
            with self.assertRaisesRegex(ValueError, "verification copy input changed"):
                isolation._verification_protected_paths(boundary)

    def test_stage_seal_keeps_external_scratch_runtime_mutable_and_old_stage_data_immutable(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve()
            target = root / ".autocode/runs/fake/native-kilo-owned"
            (target / "data").mkdir(parents=True)
            (target / "sdk").mkdir()
            control = root / ".autocode/tool-containment-owned"
            (control / "scratch").mkdir(parents=True)
            env = {}
            runtime = isolation._relocate_client_runtime(
                {"workspace": str(root), "profile": str(control / "policy.sb"), "scratch": str(control / "scratch")},
                env,
            )
            database = runtime / "data/runtime.db"
            database.write_bytes(b"mutable runtime")
            database.chmod(0o600)
            old_data = target / "data/unused"
            old_data.write_bytes(b"unused initial stage data")
            old_data.chmod(0o600)
            sdk = target / "sdk/client"
            sdk.write_bytes(b"immutable SDK fixture")
            sdk.chmod(0o700)
            journal = target / "source.jsonl"
            journal.write_bytes(b"parent authority")
            journal.chmod(0o400)
            pins = {}
            isolation._freeze_stage_files(target, env, pins, journal=journal, runtime_root=runtime)
            self.assertEqual(set(pins), {str(old_data), str(sdk)})
            self.assertEqual(old_data.stat().st_mode & 0o777, 0o400)
            self.assertEqual(database.stat().st_mode & 0o777, 0o600)
            database.write_bytes(b"runtime may advance")

    def test_parent_precreates_only_the_readonly_kilo_config_directory(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            target = Path(directory).resolve()
            config = target / "config"
            config.mkdir()
            isolation._prepare_config_directory(target, {"XDG_CONFIG_HOME": str(config)})
            self.assertEqual(list(config.iterdir()), [config / "kilo"])
            self.assertEqual((config / "kilo").stat().st_mode & 0o777, 0o700)
            with self.assertRaisesRegex(ValueError, "foreign original Kilo"):
                isolation._prepare_config_directory(target, {"XDG_CONFIG_HOME": str(target)})

    def timezone_fixture(self, root):
        aliases = []
        for index in range(3):
            actual = root / ("os-file-" + str(index))
            actual.write_bytes(b"public timezone fixture")
            actual.chmod(0o644)
            alias = root / ("alias-" + str(index))
            alias.symlink_to(actual)
            aliases.append(str(alias))
        original = Path.lstat

        def root_owned_metadata(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            return SimpleNamespace(st_mode=value.st_mode, st_size=value.st_size, st_nlink=value.st_nlink, st_uid=0)

        return aliases, root_owned_metadata

    def test_timezone_policy_grants_only_pinned_files_and_rejects_changed_bytes(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve()
            aliases, metadata = self.timezone_fixture(root)
            target = root / ".autocode/runs/fake/native-kilo-owned"
            target.mkdir(parents=True)
            control = root / ".autocode/tool-containment-fixture"
            (control / "scratch").mkdir(parents=True)
            env = {}
            for key, name in (
                ("XDG_DATA_HOME", "data"),
                ("XDG_CACHE_HOME", "cache"),
                ("XDG_STATE_HOME", "state"),
                ("TMPDIR", "tmp"),
            ):
                p = target / name
                p.mkdir()
                env[key] = str(p)
            with patch.object(isolation, "TIMEZONE_FILES", tuple(aliases)), patch.object(Path, "lstat", metadata):
                pins = isolation._timezone_inputs()
                boundary = {
                    "workspace": str(root),
                    "profile": str(control / "policy.sb"),
                    "scratch": str(control / "scratch"),
                }
                isolation._relocate_client_runtime(boundary, env)
                text = isolation.whole_client_policy(
                    root,
                    target,
                    boundary,
                    {"read_roots": [], "protected_paths": []},
                    env,
                    "/private/tmp/ac-test/source.sock",
                    os_readonly_inputs=pins,
                )
                lines = [line for line in text.splitlines() if any(row["path"] in line for row in pins)]
                self.assertEqual(lines, ['(allow file-read-data (literal "' + row["path"] + '"))' for row in pins])
                self.assertNotIn('(subpath "/private/var/db', text)
                self.assertNotIn("mach-lookup", text)
                self.assertNotIn('file-write* (subpath "' + str(target / "config"), text)
                Path(pins[0]["path"]).write_bytes(b"changed source input")
                with self.assertRaisesRegex(ValueError, "timezone input bytes"):
                    isolation._require_timezone_inputs(pins)
                owner = object.__new__(isolation.KiloRoleIsolation)
                owner._settings, owner._root, owner._native = {}, root, Mock()
                owner._prepared = {"os_readonly_inputs": pins}
                with self.assertRaisesRegex(ValueError, "timezone input bytes"):
                    owner._check({"settings": {}, "workspace": str(root)})

    def test_timezone_alias_retarget_or_input_set_change_refuses_existing_pins(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve()
            aliases, metadata = self.timezone_fixture(root)
            with patch.object(isolation, "TIMEZONE_FILES", tuple(aliases)), patch.object(Path, "lstat", metadata):
                pins = isolation._timezone_inputs()
                with self.assertRaisesRegex(ValueError, "timezone input set"):
                    isolation._require_timezone_inputs(pins[:-1])
                replacement = root / "replacement"
                replacement.write_bytes(Path(pins[0]["path"]).read_bytes())
                replacement.chmod(0o644)
                alias = Path(aliases[0])
                alias.unlink()
                alias.symlink_to(replacement)
                with self.assertRaisesRegex(ValueError, "timezone input bytes"):
                    isolation._require_timezone_inputs(pins)

    def test_model_free_policy_grants_only_its_exact_stdio_files(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve()
            target = root / ".autocode/runs/fake/native-kilo-owned"
            target.mkdir(parents=True)
            scratch = target / "tmp"
            scratch.mkdir()
            profile = target / "derivation.sb"
            profile.write_text(
                containment._render_policy(
                    root, target, read_workspace=False, read_roots=[target], write_roots=[scratch]
                )
            )
            receipt = target / "command.json"
            # Stop before process creation while exercising the actual policy
            # construction and output acquisition used by the supervised call.
            with patch.object(
                isolation.supervision, "launch", side_effect=RuntimeError("controlled prelaunch refusal")
            ):
                with self.assertRaisesRegex(RuntimeError, "controlled prelaunch"):
                    isolation._run(
                        ["/usr/bin/sandbox-exec", "-f", str(profile), "/fixture/compiler"],
                        environment={},
                        cwd=root,
                        timeout=30,
                        receipt=receipt,
                    )
            effective = Path(str(receipt) + ".execution.sb").read_text()
            grant = next(line for line in effective.splitlines() if line.startswith("(allow file-write-data"))
            self.assertIn(str(receipt) + ".stdout", grant)
            self.assertIn(str(receipt) + ".stderr", grant)
            self.assertNotIn("subpath", grant)
            for authority in (target / "source.jsonl", target / "prompt.txt", target / "sdk"):
                self.assertNotIn(str(authority), grant)

    def test_stage_seal_preserves_runtime_database_modes_and_pins_authority(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            target = Path(directory).resolve()
            environment = {}
            mutable_files = []
            for key, name in (
                ("XDG_DATA_HOME", "data"),
                ("XDG_CACHE_HOME", "cache"),
                ("XDG_STATE_HOME", "state"),
                ("TMPDIR", "tmp"),
            ):
                folder = target / name
                folder.mkdir()
                environment[key] = str(folder)
                path = folder / "runtime.db"
                path.write_bytes(b"runtime fixture")
                path.chmod(0o600)
                mutable_files.append(path)
            sdk = target / "sdk"
            sdk.mkdir()
            authority = sdk / "client"
            authority.write_bytes(b"compiled fixture")
            authority.chmod(0o700)
            policy = target / "derivation.sb"
            policy.write_text("(deny default)\n")
            journal = target / "source.jsonl"
            journal.write_bytes(b"parent fixture")
            journal.chmod(0o400)
            pins = {}
            isolation._freeze_stage_files(target, environment, pins, journal=journal)
            self.assertEqual(set(pins), {str(authority), str(policy)})
            self.assertEqual(authority.stat().st_mode & 0o777, 0o500)
            self.assertEqual(policy.stat().st_mode & 0o777, 0o400)
            for path in mutable_files:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                path.write_bytes(b"runtime may advance")
            self.assertEqual(journal.stat().st_mode & 0o777, 0o400)

    def test_pure_policy_is_pinned_before_seal_and_cannot_change_at_use(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory).resolve()
            target = root / ".autocode/runs/fake/native-kilo-owned"
            sdk = target / "sdk"
            sdk.mkdir(parents=True)
            scratch = target / "tmp"
            scratch.mkdir()
            for name in ("bun", "decoder.mjs"):
                (sdk / name).write_bytes(b"controlled fixture; not executed")
            owner = object.__new__(isolation.KiloRoleIsolation)
            owner._root = root
            owner._hooks = {"derivation": {"runtime": "bun"}}
            control = root / ".autocode/tool-containment-owned"
            (control / "scratch").mkdir(parents=True)
            environment = {}
            runtime = isolation._relocate_client_runtime(
                {"workspace": str(root), "profile": str(control / "policy.sb"), "scratch": str(control / "scratch")},
                environment,
            )
            owner._prepared = {
                "target": target,
                "sdk": sdk,
                "files": {},
                "environment": environment,
                "runtime_root": runtime,
            }
            aliases, metadata = self.timezone_fixture(root)
            with patch.object(isolation, "TIMEZONE_FILES", tuple(aliases)), patch.object(Path, "lstat", metadata):
                owner._prepared["os_readonly_inputs"] = isolation._timezone_inputs()
                owner._prepare_derivation_policy()
                policy = target / "derivation.sb"
                self.assertEqual(owner._prepared["files"][str(policy)], util.file_hash(policy))
                text = policy.read_text()
                self.assertIn("(deny process-fork)", text)
                self.assertNotIn("allow network", text)
                pins = owner._prepared["os_readonly_inputs"]
                lines = [line for line in text.splitlines() if any(row["path"] in line for row in pins)]
                self.assertEqual(lines, ['(allow file-read-data (literal "' + row["path"] + '"))' for row in pins])
                original = Path(pins[0]["path"]).read_bytes()
                Path(pins[0]["path"]).write_bytes(b"changed OS input")
                with patch.object(isolation, "_run") as run:
                    with self.assertRaisesRegex(ValueError, "timezone input bytes"):
                        owner._pure("decoder.mjs", [], b"{}")
                    run.assert_not_called()
                Path(pins[0]["path"]).write_bytes(original)
                policy.chmod(0o600)
                policy.write_text(text + "(allow network-outbound)\n")
                with patch.object(isolation, "_run") as run:
                    with self.assertRaisesRegex(ValueError, "sealed pure SDK policy changed"):
                        owner._pure("decoder.mjs", [], b"{}")
                    run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
