"""CODE-owned, opt-in stage-local isolation for one exact saved OpenCode role.

This stages already pinned SDK bytes; it never installs packages, executes a
process, reads credentials, grants image acceptance or changes a saved route or
cap. Inputs are mandatory CODE dependencies. Custom provider/configuration
definitions are held rather than discarded. Readonly planning may bind the
initial saved settings before a product contract exists; a task writer must
additionally have the actual approved contract. Runtime namespace protection,
the actual launch seal and retained native/image verification remain separate.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import uuid
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

# Public facade used by provider launch and native ownership checks.
try:
    from . import autocode_native_locators as native_locators
    from .autocode_kilo_isolation import KiloRoleIsolation as KiloRoleIsolation
except ImportError:
    import autocode_native_locators as native_locators
    from autocode_kilo_isolation import KiloRoleIsolation as KiloRoleIsolation

try:
    from . import autocode_contract_identity as contract
    from . import autocode_native_conformance as conformance
    from . import autocode_native_writer_execution as writers
    from . import autocode_util as util
    from . import autocode_visual_profile as sdk_rules
except ImportError:
    import autocode_contract_identity as contract
    import autocode_native_conformance as conformance
    import autocode_native_writer_execution as writers
    import autocode_util as util
    import autocode_visual_profile as sdk_rules


VERSION = "1.18.33"
ENVIRONMENT = (
    "PATH",
    "LANG",
    "LC_CTYPE",
    "LC_ALL",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "AUTOCODE_OUTPUT_MODE",
    "AUTOCODE_OUTPUT_STORE",
    "AUTOCODE_OUTPUT_ATTEMPT",
    "AUTOCODE_CAPTURE_CONTEXT",
    "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX",
)
DIRECTORIES = {
    "HOME": "home",
    "XDG_CONFIG_HOME": "config",
    "XDG_CACHE_HOME": "cache",
    "XDG_STATE_HOME": "state",
    "OPENCODE_TEST_MANAGED_CONFIG_DIR": "managed",
    "TMPDIR": "tmp",
}
FLAGS = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "npm_config_offline": "true",
}


def _require(value, reason):
    if not value:
        raise ValueError("Native role isolation: " + reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def _path(root, value):
    return sdk_rules._owned(root, value)


def _write(path, raw, pins):
    sdk_rules._write(path, raw, pins)


class NativeRoleIsolation:
    """One concrete parent owner; no readiness boolean or model-created hook."""

    def __init__(
        self,
        state,
        workspace,
        run_dir,
        route_role,
        *,
        sdk_path,
        sdk_manifest_sha256,
        transport_identity_sha256,
        executable_sha256,
    ):
        _require(isinstance(state, dict) and isinstance(state.get("settings"), dict), "missing exact saved settings")
        self._state = state
        self._root = Path(workspace).resolve()
        _require(self._root == Path(state["workspace"]).resolve(), "foreign workspace")
        self._run = _path(self._root, Path(run_dir).absolute())
        _require(
            self._run.is_dir() and self._run.is_relative_to(self._root / ".autocode" / "runs"), "foreign owned run"
        )
        self._settings = deepcopy(state["settings"])
        self._role = route_role
        roles = self._settings.get("roles")
        _require(isinstance(roles, dict) and isinstance(roles.get(route_role), dict), "missing saved role")
        self._route = deepcopy(roles[route_role])
        _require(
            set(self._route) <= {"engine", "provider", "model", "reasoning_effort", "model_pinned"}
            and (self._route.get("engine") or self._settings.get("engine")) == "opencode"
            and self._route.get("provider") in (None, "opencode")
            and self._settings.get("provider") in (None, "opencode")
            and isinstance(self._route.get("model"), str)
            and re.fullmatch(r"[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+", self._route["model"])
            and self._route.get("reasoning_effort") in ("low", "medium", "high", "xhigh", "max")
            and ("model_pinned" not in self._route or type(self._route["model_pinned"]) is bool),
            "unsupported saved route",
        )
        identity = self._settings.get("transport_identities", {}).get(
            "opencode", self._settings.get("transport_identity")
        )
        _require(
            isinstance(identity, dict)
            and set(identity)
            == {"engine", "version", "identity_version", "executable", "config_hashes", "environment_config_hashes"}
            and identity["engine"] == "opencode"
            and identity["version"] == VERSION
            and type(identity["identity_version"]) is int
            and identity["identity_version"] == 2
            and _hash(transport_identity_sha256)
            and util.digest(identity) == transport_identity_sha256,
            "saved transport differs from the required exact input",
        )
        self._identity = deepcopy(identity)
        self._activity = Path(__file__).resolve().parent / "providers" / "opencode_activity.mjs"
        fingerprints = identity["config_hashes"]
        _require(
            isinstance(fingerprints, dict)
            and _hash(fingerprints.get(str(self._activity)))
            and all(name == str(self._activity) or value is None for name, value in fingerprints.items())
            and isinstance(identity["environment_config_hashes"], dict)
            and all(value is None for value in identity["environment_config_hashes"].values()),
            "unsupported custom/global provider configuration; cannot discard saved inputs",
        )
        _require(_hash(executable_sha256) and _hash(sdk_manifest_sha256), "missing required executable or SDK pin")
        _require(
            isinstance(identity["executable"], str) and Path(identity["executable"]).is_absolute(),
            "saved native executable is not absolute",
        )
        self._executable = Path(identity["executable"]).resolve()
        self._executable_hash = executable_sha256
        _require(
            isinstance(sdk_path, str) and sdk_path.startswith(".autocode/") and not Path(sdk_path).is_absolute(),
            "SDK must be explicitly owned under .autocode",
        )
        self._sdk = _path(self._root, sdk_path)
        _require(
            self._sdk.is_dir() and not self._sdk.is_relative_to(self._run),
            "SDK source is missing or inside the current run",
        )
        self._sdk_hash = sdk_manifest_sha256
        self._files = sdk_rules._sdk(self._sdk, sdk_manifest_sha256)
        self._prepared = None
        self._descriptor = None
        self._writer_restriction = None
        self._image_preparation = None
        self._writer_execution = None
        self._check(state)

    def _check(self, state: RunState):
        _require(
            isinstance(state, dict)
            and Path(state["workspace"]).resolve() == self._root
            and state.get("settings") == self._settings,
            "saved route, caps or workspace changed",
        )
        _require(
            self._activity.is_file()
            and not self._activity.is_symlink()
            and stat.S_ISREG(self._activity.stat().st_mode)
            and util.file_hash(self._activity) == self._identity["config_hashes"][str(self._activity)],
            "trusted activity bytes changed",
        )
        _require(
            self._executable.is_absolute()
            and self._executable.is_file()
            and not self._executable.is_symlink()
            and stat.S_ISREG(self._executable.stat().st_mode)
            and util.file_hash(self._executable) == self._executable_hash,
            "approved native executable changed",
        )
        _require(sdk_rules._sdk(self._sdk, self._sdk_hash) == self._files, "approved SDK source changed")
        if self._writer_restriction is not None:
            _require(
                self._measure_writer(state) == self._writer_restriction,
                "current writer restriction or file/directory kind changed",
            )

    def _measure_writer(self, state: RunState):
        # This authenticates a restriction under the approved contract. It does
        # not authenticate causal assignment/prerequisites or permit dispatch.
        _require(self._role == "terra", "only the saved Builder role can receive a task writer restriction")
        _require(contract.approved(state), "task writer requires the approved contract")
        saved = cast(dict, state["goal_contract"])
        task = state.get("current_task")
        _require(
            isinstance(task, dict)
            and isinstance(task.get("id"), str)
            and task["id"]
            and task.get("kind") == "implement"
            and not task.get("milestone_ids")
            and task.get("contract_hash") == saved["hash"]
            and task.get("contract_revision") == saved["revision"]
            and _hash(task.get("source_revision")),
            "missing exact current serial implement task binding",
        )
        assert isinstance(task, dict)  # _require above refused anything else
        milestones = saved["body"].get("milestones")
        _require(isinstance(milestones, list), "missing approved owner milestones")
        owners = [row for row in milestones if isinstance(row, dict) and row.get("id") == task.get("milestone_id")]
        _require(len(owners) == 1, "unknown or duplicate approved task owner")
        owner = owners[0]
        criteria = task.get("criterion_ids", task.get("acceptance_criteria"))
        _require(
            isinstance(criteria, list)
            and criteria
            and all(isinstance(value, str) and value for value in criteria)
            and len(criteria) == len(set(criteria))
            and criteria == owner.get("acceptance_criteria")
            and (
                "criterion_ids" not in task
                or "acceptance_criteria" not in task
                or task["criterion_ids"] == task["acceptance_criteria"]
            ),
            "task criteria differ from complete approved owner CIDs",
        )
        try:
            from . import autocode_tool_containment as containment
        except ImportError:
            import autocode_tool_containment as containment
        paths = containment.writer_footprint(self._root, task.get("affected_paths"))
        approved = containment.writer_footprint(self._root, owner.get("affected_paths"))
        _require(
            all(
                any(Path(value) == Path(parent) or Path(parent) in Path(value).parents for parent in approved)
                for value in paths
            ),
            "task paths expand approved owner restriction",
        )
        kinds = containment._writer_path_kinds(self._root, paths)
        return {
            "task": deepcopy(task),
            "contract_hash": saved["hash"],
            "contract_revision": saved["revision"],
            "owner": deepcopy(owner),
            "write_paths": paths,
            "writer_path_kinds": kinds,
            "causal_assignment": "NOT_AUTHENTICATED",
        }

    def containment_request(self, *, allow_write):
        """Return exact restrictions before any SDK staging/native setup occurs."""
        _require(type(allow_write) is bool, "invalid writer mode")
        self._check(self._state)
        if not allow_write:
            _require(self._writer_restriction is None, "writer owner cannot become readonly")
            return {}
        current = self._measure_writer(self._state)
        if self._writer_restriction is None:
            self._writer_restriction = current
        _require(current == self._writer_restriction, "current writer restriction or file/directory kind changed")
        return {"write_paths": deepcopy(current["write_paths"])}

    def stage(self, *, command, environment, configuration_identity, allow_write):
        """Stage before existing native debug/configure; no native call is added."""
        _require(self._prepared is None and self._descriptor is None, "owner already used")
        self.containment_request(allow_write=allow_write)
        self._check(self._state)
        _require(
            type(allow_write) is bool and (not allow_write or contract.approved(self._state)),
            "task writer requires the approved contract",
        )
        _require(
            configuration_identity
            == {key: self._identity[key] for key in ("config_hashes", "environment_config_hashes")},
            "current configuration differs from the saved transport",
        )
        _require(
            "--session" not in command
            and conformance._option(command, "--model") == self._route["model"]
            and conformance._option(command, "--variant") == self._route["reasoning_effort"]
            and Path(conformance._option(command, "--dir")).resolve() == self._root
            and conformance._option(command, "--format") == "json",
            "foreign, resumed or changed target command",
        )
        executable = shutil.which(command[0], path=environment.get("PATH", ""))
        _require(
            executable and Path(executable).resolve() == self._executable,
            "target executable differs from saved transport",
        )
        agent = conformance._option(command, "--agent")
        _require(
            agent == "autocode_" + self._role
            or re.fullmatch("autocode_" + re.escape(self._role) + r"_plan_[a-f0-9]{32}", agent),
            "foreign named primary role",
        )
        config = sdk_rules._json(environment["OPENCODE_CONFIG_CONTENT"])
        _require(
            isinstance(config, dict)
            and set(config) == {"$schema", "share", "autoupdate", "plugin", "agent"}
            and config["share"] == "disabled"
            and config["autoupdate"] is False
            and config["plugin"] == [self._activity.as_uri()]
            and set(config["agent"]) == {agent},
            "unsupported inherited plugin, agent or provider configuration",
        )
        definition = config["agent"][agent]
        _require(
            isinstance(definition, dict)
            and set(definition) == {"description", "mode", "permission"}
            and definition["mode"] == "primary"
            and isinstance(definition["permission"], dict),
            "unsupported primary role definition",
        )
        _require(
            not any(
                value
                and (
                    key.startswith(("OPENAI_", "CODEX_", "AZURE_OPENAI_", "ZAI_", "XIAOMI_", "ANTHROPIC_"))
                    or re.search(r"(?:API_KEY|BASE_URL|ENDPOINT|ACCESS_TOKEN)$", key)
                )
                for key, value in environment.items()
            ),
            "unsupported custom provider credential/endpoint environment",
        )
        data = environment.get("XDG_DATA_HOME") or (
            str(Path(environment["HOME"]) / ".local" / "share") if environment.get("HOME") else ""
        )
        _require(
            data and Path(data).is_absolute() and ".." not in Path(data).parts,
            "ambiguous existing native auth data location",
        )
        target = _path(self._run, self._run / ("native-role-" + uuid.uuid4().hex))
        target.mkdir(mode=0o700)
        pins = {}
        child = {key: environment[key] for key in ENVIRONMENT if key in environment}
        for key, folder in DIRECTORIES.items():
            path = target / folder
            path.mkdir(mode=0o700)
            child[key] = str(path)
        destination = target / "config" / "opencode"
        for relative, expected in self._files.items():
            raw = (self._sdk / relative).read_bytes()
            _require(_sha(raw) == expected, "SDK changed while staging")
            _write(destination / relative, raw, pins)
        _write(destination / sdk_rules.MANIFEST, (self._sdk / sdk_rules.MANIFEST).read_bytes(), pins)
        _require(sdk_rules._sdk(destination, self._sdk_hash) == self._files, "staged SDK differs from approved source")
        activity = target / "activity.mjs"
        _write(activity, self._activity.read_bytes(), pins)
        empty = target / "empty-config.json"
        _write(empty, b"{}\n", pins)
        final = deepcopy(config)
        final.update(
            snapshot=False,
            model=self._route["model"],
            small_model=self._route["model"],
            enabled_providers=[self._route["model"].split("/")[0]],
            plugin=[activity.as_uri()],
        )
        # OpenCode 1.18.33 otherwise appends a tool-output directory allowance
        # after the named agent's deny-default rules. Its exact-pattern deny
        # suppresses that allowance; no native tool or filesystem grant is added.
        final["permission"] = {"external_directory": {str(Path(data) / "opencode" / "tool-output") + "/*": "deny"}}
        final["agent"][agent]["model"] = self._route["model"]
        final["agent"].update({name: {"disable": True} for name in ("title", "summary", "compaction")})
        child.update(FLAGS)
        child.update(
            XDG_DATA_HOME=data,
            OPENCODE_CONFIG=str(empty),
            OPENCODE_CONFIG_DIR=str(destination),
            OPENCODE_CONFIG_CONTENT=json.dumps(final),
            npm_config_cache=str(target / "cache" / "npm"),
        )
        for key in ("npm_config_userconfig", "npm_config_globalconfig"):
            path = target / (key + ".empty")
            _write(path, b"", pins)
            child[key] = str(path)
        argv = [str(self._executable), *command[1:]]
        self._prepared = {
            "target": target,
            "files": pins,
            "agent": agent,
            "allow_write": allow_write,
            "command": argv,
            "environment": deepcopy(child),
            "config": final,
            "native_data_path_only": data,
        }
        self._check(self._state)
        return argv, child

    def finalize(self, *, command, environment, boundary):
        """Bind actual final native observation; never reuse it after extension."""
        _require(
            self._prepared is not None and self._descriptor is None, "owner is not awaiting final native conformance"
        )
        self._check(self._state)
        prepared = self._prepared
        if prepared["allow_write"]:
            _require(
                boundary.get("write_paths") == self._writer_restriction["write_paths"]
                and boundary.get("writer_path_kinds") == self._writer_restriction["writer_path_kinds"],
                "final target lacks exact pinned task footprint",
            )
        _require(
            command == prepared["command"]
            and boundary.get("allow_write") is prepared["allow_write"]
            and boundary.get("workspace") == str(self._root)
            and boundary.get("tools") == ["bash"],
            "foreign boundary or command",
        )
        expected = prepared["environment"]
        _require(
            {
                key: value
                for key, value in environment.items()
                if key not in ("SHELL", "OPENCODE_CONFIG_CONTENT", *conformance.ENVELOPES)
            }
            == {
                key: value
                for key, value in expected.items()
                if key not in ("SHELL", "OPENCODE_CONFIG_CONTENT", *conformance.ENVELOPES)
            },
            "isolating environment or output cap changed",
        )
        final = sdk_rules._json(environment["OPENCODE_CONFIG_CONTENT"])
        agent = prepared["agent"]
        reference = deepcopy(prepared["config"])
        reference["shell"] = boundary["shell"]
        _require(
            isinstance(final.get("agent"), dict) and isinstance(final["agent"].get(agent), dict),
            "missing final primary role",
        )
        reference["agent"][agent]["permission"] = final["agent"][agent].get("permission")
        _require(
            final == reference and environment.get("SHELL") == boundary["shell"],
            "final configuration has unsupported changes or image extension",
        )
        proof = sdk_rules._json(Path(boundary["conformance"]).read_bytes())
        _require(
            util.file_hash(boundary["conformance"]) == boundary["conformance_sha256"],
            "native conformance bytes changed",
        )
        observed = conformance.verify(proof.get("launch_identity"), command, environment)
        try:
            from . import autocode_tool_containment as containment
        except ImportError:
            import autocode_tool_containment as containment
        _require(
            containment.shell_permissions(observed["effective_permission"]) == final["agent"][agent]["permission"]
            and conformance.non_shell_denied(observed["effective_permission"]),
            "actual native ordered permission differs from final primary role",
        )
        containment.verify(boundary)
        pins = dict(prepared["files"])
        inline = prepared["target"] / "opencode-final.json"
        _write(inline, environment["OPENCODE_CONFIG_CONTENT"].encode(), pins)
        sources = {key: environment[key] for key in DIRECTORIES}
        sources.update(
            OPENCODE_CONFIG=environment["OPENCODE_CONFIG"],
            OPENCODE_CONFIG_DIR=environment["OPENCODE_CONFIG_DIR"],
            inline={"path": str(inline), "sha256": observed["final_inline_sha256"]},
            project_config_disabled=environment["OPENCODE_DISABLE_PROJECT_CONFIG"],
        )
        identity = {
            key: observed[key] for key in ("executable", "executable_sha256", "command_sha256", "environment_sha256")
        }
        manifest = {
            "version": 1,
            "role": self._role,
            "model": self._route["model"],
            "reasoning_effort": self._route["reasoning_effort"],
            "files": pins,
            "child_identity": identity,
            "configuration_sources": sources,
            "flags": deepcopy(FLAGS),
            "native_data_path_only": prepared["native_data_path_only"],
            "sdk_source": str(self._sdk),
            "sdk_manifest_sha256": self._sdk_hash,
            "saved_transport_sha256": util.digest(self._identity),
            "saved_settings_sha256": util.digest(self._settings),
            "conformance": {"path": boundary["conformance"], "sha256": boundary["conformance_sha256"]},
            "writer_restriction": deepcopy(self._writer_restriction),
            "image_acceptance": "NOT_VERIFIED",
        }
        if self._image_preparation is not None:
            manifest["image_review"] = {
                "prompt": deepcopy(self._image_preparation.prompt_binding),
                "schema": deepcopy(self._image_preparation.schema_binding),
            }
        path = prepared["target"] / "isolation.json"
        _write(path, (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode(), {})
        self._descriptor = {
            key: deepcopy(manifest[key])
            for key in (
                "version",
                "role",
                "model",
                "reasoning_effort",
                "files",
                "child_identity",
                "configuration_sources",
                "conformance",
                "writer_restriction",
            )
        }
        self._descriptor.update(manifest=str(path), manifest_sha256=util.file_hash(path))
        if "image_review" in manifest:
            self._descriptor["image_review"] = deepcopy(manifest["image_review"])
        # Finalization is preparation, not permission to dispatch. Locators do
        # not exist yet and must be mandatory at the actual launch gate.
        return self._verify_finalized(
            state=self._state, command=command, environment=environment, boundary=boundary, require_locators=False
        )

    def require_image_profile(self, profile):
        _require(
            self._prepared is not None
            and self._descriptor is None
            and self._role == "sol"
            and self._prepared["allow_write"] is False
            and self._prepared["agent"] == "autocode_sol"
            and sdk_rules._profile(self._state) == profile
            and profile["model"] == self._route["model"]
            and profile["reasoning_effort"] == self._route["reasoning_effort"]
            and profile["sdk_path"] == str(self._sdk.relative_to(self._root))
            and profile["sdk_manifest_sha256"] == self._sdk_hash
            and profile["transport_identity_sha256"] == util.digest(self._identity)
            and profile["executable_sha256"] == self._executable_hash,
            "image extension differs from the exact approved readonly Sol profile",
        )
        self._check(self._state)

    def stage_image(self, preparation, *, command, environment):
        try:
            from . import autocode_native_image as images
        except ImportError:
            import autocode_native_image as images
        _require(
            type(preparation) is images.NativeImagePreparation
            and preparation.isolation is self
            and self._image_preparation is None
            and command == self._prepared["command"]
            and environment == self._prepared["environment"],
            "foreign or repeated same-owner image extension",
        )
        self.require_image_profile(preparation.profile)
        argv, child, pins = preparation.stage(self, command, environment)
        expected = deepcopy(self._prepared["config"])
        expected["plugin"].append(Path(preparation._staged["plugin_path"]).as_uri())
        _require(
            sdk_rules._json(child["OPENCODE_CONFIG_CONTENT"]) == expected
            and {
                key: value
                for key, value in child.items()
                if key not in ("OPENCODE_CONFIG_CONTENT", "AUTOCODE_IMAGE_AUDIT")
            }
            == {key: value for key, value in environment.items() if key != "OPENCODE_CONFIG_CONTENT"}
            and argv
            == [*command, *(value for row in preparation._staged["images"] for value in ("--file", row["path"]))],
            "image extension changed unrelated configuration, environment or command",
        )
        self._prepared.update(command=deepcopy(argv), environment=deepcopy(child), config=expected)
        self._prepared["files"].update(pins)
        self._image_preparation = preparation
        return argv, child

    def render_image_prompt(self, *, command, environment, boundary):
        if self._image_preparation is not None:
            _require(self._descriptor is None, "image prompt cannot extend finalized isolation")
            self._prepared["files"].update(self._image_preparation.render(command, environment, boundary))

    def freeze_image(self, *, command, environment, descriptor):
        if self._image_preparation is not None:
            _require(descriptor == self._descriptor, "foreign final image isolation descriptor")
            self._verify_finalized(
                state=self._state,
                command=command,
                environment=environment,
                boundary=native_locators.expand_boundary(environment["AUTOCODE_TOOL_CONTAINMENT"], descriptor),
                require_locators=True,
            )
            return self._image_preparation.freeze(command, environment, descriptor)

    def image_pins(self, context):
        if self._image_preparation is None:
            _require(context is None, "image context has no original same-owner packet")
            return {}
        return self._image_preparation.pins(context)

    def environment_descriptor(self, environment):
        """Recover no authority: compare the locator to this original owner."""
        _require(self._descriptor is not None, "owner has no finalized native descriptor")
        _require(
            native_locators.descriptor_matches(
                environment.get("AUTOCODE_NATIVE_ROLE_ISOLATION", "null"), self._descriptor
            ),
            "foreign isolation environment locator",
        )
        return deepcopy(self._descriptor)

    def verify_launch(self, *, state: RunState, command, environment, boundary, writer_execution=None):
        """Require real launch locators; missing causal writer integration holds."""
        descriptor = self._verify_finalized(
            state=state, command=command, environment=environment, boundary=boundary, require_locators=True
        )
        if self._prepared["allow_write"]:
            writer_execution = self._writer_execution if writer_execution is None else writer_execution
            _require(
                type(writer_execution) is writers.NativeWriterExecution,
                "causal writer admission is not wired; no provider dispatch is authorized",
            )
            writer_execution.require_isolation(
                self, state=state, command=command, environment=environment, boundary=boundary
            )
        else:
            _require(writer_execution is None, "readonly isolation cannot borrow a writer admission")
        return descriptor

    def _verify_finalized(self, *, state: RunState, command, environment, boundary, require_locators):
        _require(self._descriptor is not None, "owner has no finalized actual conformance")
        self._check(state)
        descriptor = self._descriptor
        path = _path(self._run, descriptor["manifest"])
        _require(
            path.is_file()
            and not path.is_symlink()
            and stat.S_ISREG(path.stat().st_mode)
            and path.stat().st_mode & 0o222 == 0
            and util.file_hash(path) == descriptor["manifest_sha256"],
            "owned isolation manifest changed",
        )
        if require_locators:
            _require(
                "native_role_isolation" in boundary and all(key in environment for key in conformance.ENVELOPES),
                "missing mandatory final native launch locators",
            )
        if "native_role_isolation" in boundary:
            _require(boundary["native_role_isolation"] == descriptor, "foreign isolation boundary locator")
        if "AUTOCODE_NATIVE_ROLE_ISOLATION" in environment:
            _require(
                native_locators.descriptor_matches(environment["AUTOCODE_NATIVE_ROLE_ISOLATION"], descriptor),
                "foreign isolation environment locator",
            )
        if "AUTOCODE_TOOL_CONTAINMENT" in environment:
            _require(
                native_locators.boundary_matches(environment["AUTOCODE_TOOL_CONTAINMENT"], boundary),
                "foreign containment environment locator",
            )
        manifest = sdk_rules._json(path.read_bytes())
        _require(
            all(
                manifest[key] == value
                for key, value in descriptor.items()
                if key not in ("manifest", "manifest_sha256")
            ),
            "owned isolation descriptor differs from actual manifest",
        )
        for name, expected in descriptor["files"].items():
            member = _path(path.parent, name)
            _require(
                member.is_file()
                and not member.is_symlink()
                and stat.S_ISREG(member.stat().st_mode)
                and member.stat().st_mode & 0o222 == 0
                and util.file_hash(member) == expected,
                "owned staged SDK/plugin/config bytes changed",
            )
        _require(
            boundary.get("allow_write") is self._prepared["allow_write"]
            and boundary.get("workspace") == str(self._root)
            and descriptor["conformance"]
            == {"path": boundary["conformance"], "sha256": boundary["conformance_sha256"]},
            "foreign final boundary",
        )
        if self._prepared["agent"] != "autocode_" + self._role:
            # A planning agent's shell reads no run's private state, only this run's planning/ (#1250).
            reads = boundary.get("private_reads")
            _require(
                isinstance(reads, dict) and reads.get("run_dir") == str(self._run),
                "planning shell lacks this run's private run-state read denial",
            )
        _require(not self._prepared["allow_write"] or contract.approved(state), "task writer contract approval changed")
        proof = sdk_rules._json(Path(boundary["conformance"]).read_bytes())
        _require(
            util.file_hash(boundary["conformance"]) == boundary["conformance_sha256"],
            "final native proof bytes changed",
        )
        observed = conformance.verify(proof.get("launch_identity"), command, environment)
        _require(
            descriptor["child_identity"] == {key: observed[key] for key in descriptor["child_identity"]},
            "final child identity changed",
        )
        _require(
            descriptor["configuration_sources"]["inline"]["sha256"] == observed["final_inline_sha256"],
            "final inline bytes changed",
        )
        for key in DIRECTORIES:
            _require(environment.get(key) == descriptor["configuration_sources"][key], "isolated directory changed")
            folder = _path(path.parent, environment[key])
            _require(folder.is_dir() and not folder.is_symlink(), "isolated directory changed kind")
        _require(all(environment.get(key) == value for key, value in FLAGS.items()), "isolation flags changed")
        # No new agent/plugin/config entries may appear in the exclusive staged
        # config tree. SDK content is separately checked against its full manifest.
        destination = _path(path.parent, environment["OPENCODE_CONFIG_DIR"])
        _require(sdk_rules._sdk(destination, self._sdk_hash) == self._files, "staged SDK/config tree changed")
        for folder in (Path(environment["HOME"]), Path(environment["OPENCODE_TEST_MANAGED_CONFIG_DIR"])):
            _require(not any(folder.iterdir()), "unexpected home or managed configuration source")
        return deepcopy(descriptor)
