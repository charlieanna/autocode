"""One operator-approved, prebootstrapped native visual-review launch profile.

The approved body.constraints contains exactly one VISUAL_REVIEW_PROFILE=<JSON>.
Its fields are the exact keys checked by _profile below. sdk_path names an owned
.autocode directory with package.json, package-lock.json, node_modules, and a
read-only sdk-manifest.json: {"version":1,"files":{"relative/path":"sha256"}}.
The manifest covers every package/lock/dependency file, including internal file
links by their contents. Preparation materializes links, never copies credentials,
installs packages, reads global configuration, or grants visual acceptance.

The controller must perform its existing saved-transport drift check first, then
consume BOTH returned command/environment and bind child_identity immediately
before Popen. Native request/finish receipts remain mandatory after execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_contract_identity as contract
    from . import autocode_tool_containment as containment
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as contract
    import autocode_tool_containment as containment
    import autocode_util as util


MARKER = "VISUAL_REVIEW_PROFILE="
VERSION = "1.18.33"
MAX_REQUESTS = 8
MANIFEST = "sdk-manifest.json"
IMAGE_BOUNDS = {"width": 1536, "height": 1536, "pixels": 1536 * 1536, "bytes": 8 * 1024 * 1024}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, "Duplicate JSON key: " + key)
            result[key] = value
        return result

    return json.loads(
        raw, object_pairs_hook=unique, parse_constant=lambda value: _require(False, "Invalid JSON constant: " + value)
    )


def _sha(value):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def _no_credentials(value):
    if isinstance(value, dict):
        for key, item in value.items():
            _require(
                re.sub("[^a-z]", "", key.lower())
                not in {
                    "auth",
                    "authorization",
                    "apikey",
                    "token",
                    "accesstoken",
                    "refreshtoken",
                    "password",
                    "secret",
                    "credentials",
                    "credential",
                    "baseurl",
                    "endpoint",
                },
                "Credential/endpoint settings are unsupported",
            )
            _no_credentials(item)
    elif isinstance(value, list):
        for item in value:
            _no_credentials(item)


def _owned(root, value):
    path = Path(value)
    if str(path).startswith("/var/") and str(path.resolve()) == "/private" + str(path):
        path = path.resolve()
    _require(".." not in path.parts, "Visual profile paths must not traverse parents")
    path = path if path.is_absolute() else root / path
    _require(path.is_relative_to(root) and path != root, "Visual profile path is outside its owned root")
    _require(
        not any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(root)),
        "Visual profile authority must not be symlinked",
    )
    _require(path.resolve().is_relative_to(root), "Visual profile path escapes its owned root")
    return path


def _profile(state):
    _require(contract.approved(state), "Visual profile requires authenticated operator contract approval")
    constraints = state["goal_contract"]["body"].get("constraints")
    _require(
        isinstance(constraints, list) and all(isinstance(row, str) for row in constraints),
        "Invalid approved constraints",
    )
    rows = [row[len(MARKER) :] for row in constraints if row.startswith(MARKER)]
    _require(len(rows) == 1, "Exactly one approved VISUAL_REVIEW_PROFILE constraint is required")
    value = _json(rows[0])
    keys = {
        "version",
        "mode",
        "opencode_version",
        "model",
        "reasoning_effort",
        "sdk_path",
        "sdk_manifest_sha256",
        "transport_identity_sha256",
        "executable_sha256",
        "max_requests",
        "image_limits",
    }
    _require(isinstance(value, dict) and set(value) == keys, "Unsupported visual profile fields")
    _require(
        type(value["version"]) is int
        and value["version"] == 1
        and value["mode"] == "isolated-builtin-openai"
        and value["opencode_version"] == VERSION,
        "Only isolated built-in OpenAI on OpenCode 1.18.33 is supported",
    )
    _require(
        isinstance(value["model"], str)
        and re.fullmatch(r"openai/[a-zA-Z0-9._-]+", value["model"])
        and value["reasoning_effort"] in ("low", "medium", "high", "xhigh", "max"),
        "Unsupported visual model or effort",
    )
    _require(
        all(_sha(value[key]) for key in ("sdk_manifest_sha256", "transport_identity_sha256", "executable_sha256")),
        "Visual profile needs exact SDK, transport and executable hashes",
    )
    _require(
        isinstance(value["sdk_path"], str)
        and value["sdk_path"].startswith(".autocode/")
        and not Path(value["sdk_path"]).is_absolute(),
        "SDK must be workspace-owned under .autocode",
    )
    _require(
        type(value["max_requests"]) is int and 0 < value["max_requests"] <= MAX_REQUESTS,
        "Visual max_requests must be an approved integer between 1 and 8",
    )
    limits = value["image_limits"]
    _require(
        isinstance(limits, dict)
        and set(limits) == set(IMAGE_BOUNDS)
        and all(type(limits[key]) is int and 0 < limits[key] <= ceiling for key, ceiling in IMAGE_BOUNDS.items()),
        "Unsupported byte-preserving image bounds",
    )
    return value


def _sdk(root, expected):
    """Read only the owned SDK package tree, checking names before opening files."""
    manifest = _owned(root, MANIFEST)
    _require(manifest.is_file() and manifest.stat().st_mode & 0o222 == 0, "Missing read-only prebootstrap SDK manifest")
    _require(util.file_hash(manifest) == expected, "Approved SDK manifest changed; reapproval required")
    saved = _json(manifest.read_bytes())
    _require(
        isinstance(saved, dict)
        and set(saved) == {"version", "files"}
        and type(saved["version"]) is int
        and saved["version"] == 1
        and isinstance(saved["files"], dict)
        and saved["files"],
        "Invalid SDK dependency hash manifest",
    )
    actual = {}
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            path = Path(parent) / name
            _require(
                not path.is_symlink() and (path == root / "node_modules" or path.is_relative_to(root / "node_modules")),
                "SDK contains an unsupported directory or directory link",
            )
        for name in files:
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            if relative in (MANIFEST, ".gitignore"):
                continue
            _require(
                relative in ("package.json", "package-lock.json") or relative.startswith("node_modules/"),
                "SDK contains a non-package file",
            )
            _require(
                name not in ("auth.json", ".npmrc", "id_rsa", "id_ed25519") and not name.startswith(".env"),
                "SDK contains a forbidden credential/config filename",
            )
            _require(relative in saved["files"] and _sha(saved["files"][relative]), "Unpinned SDK file: " + relative)
            _require(path.resolve().is_relative_to(root) and path.is_file(), "SDK link escapes the owned package tree")
            resolved = path.resolve()
            _require(
                resolved.name not in ("auth.json", ".npmrc", "id_rsa", "id_ed25519")
                and not resolved.name.startswith(".env")
                and (
                    resolved.is_relative_to(root / "node_modules")
                    or resolved in (root / "package.json", root / "package-lock.json")
                ),
                "SDK link targets a non-package or credential file",
            )
            _require(stat.S_ISREG(path.stat().st_mode), "SDK contains a nonregular file")
            actual[relative] = util.file_hash(path)
    _require(actual == saved["files"], "Prebootstrapped SDK dependency bytes changed or are missing")
    package = _json((root / "package.json").read_bytes())
    _require(
        isinstance(package, dict)
        and set(package) <= {"name", "private", "dependencies"}
        and package.get("dependencies") == {"@opencode-ai/plugin": VERSION},
        "SDK must pin exactly @opencode-ai/plugin 1.18.33",
    )
    lock = _json((root / "package-lock.json").read_bytes())
    _require(lock.get("lockfileVersion") == 3 and isinstance(lock.get("packages"), dict), "SDK needs a v3 package lock")
    packages = lock["packages"]
    _require(
        packages.get("", {}).get("dependencies") == package["dependencies"], "SDK lock root does not match its package"
    )
    for name in ("plugin", "sdk"):
        key = "node_modules/@opencode-ai/" + name
        pinned = packages.get(key, {})
        installed = _json((root / key / "package.json").read_bytes())
        _require(
            pinned.get("version") == VERSION
            and installed.get("version") == VERSION
            and installed.get("name") == "@opencode-ai/" + name
            and pinned.get("resolved", "").startswith("https://registry.npmjs.org/")
            and pinned.get("integrity", "").startswith("sha512-")
            and key + "/dist/index.js" in actual,
            "Missing or unpinned native SDK package: " + name,
        )
    for key, entry in packages.items():
        if key:
            _require(
                key.startswith("node_modules/")
                and ".." not in Path(key).parts
                and isinstance(entry, dict)
                and not entry.get("link")
                and isinstance(entry.get("version"), str)
                and entry.get("resolved", "").startswith("https://registry.npmjs.org/")
                and entry.get("integrity", "").startswith("sha512-"),
                "Unsupported or unpinned SDK lock dependency",
            )
            installed_path = key + "/package.json"
            _require(installed_path in actual or entry.get("optional") is True, "Missing locked SDK dependency: " + key)
            if installed_path in actual:
                installed = _json((root / installed_path).read_bytes())
                _require(
                    installed.get("version") == entry["version"], "Installed SDK dependency version differs from lock"
                )
    for relative in actual:
        if relative.endswith("/package.json"):
            key = relative[: -len("/package.json")]
            if re.fullmatch(r"node_modules/(?:@[^/]+/)?[^/]+(?:/node_modules/(?:@[^/]+/)?[^/]+)*", key):
                _require(key in packages, "Installed SDK dependency is absent from lock: " + key)
    return actual


def _write(path, data, pins):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    path.chmod(0o400)
    pins[str(path)] = util.file_hash(path)


def authority(state, worker, workspace, run_dir):
    """Return a concrete launch callback. All refusals occur inside prepare's gate.

    Creation is side-effect-free; the callback rechecks approval and source bytes.
    No truth-valued hook, saved readiness hint, or profile alone accepts images.
    """

    def launch(*, command, env, plugin_path, directory, images, reviewer):
        profile = _profile(state)
        root = Path(workspace).resolve()
        _require(root == Path(state["workspace"]).resolve(), "Visual profile workspace changed")
        run = _owned(root, Path(run_dir).absolute())
        _require(run.is_relative_to(root / ".autocode" / "runs"), "Visual profile needs the actual owned run")
        target = _owned(run, Path(directory).absolute())
        _require(target.is_dir(), "Visual profile stage directory is missing")
        sdk = _owned(root, profile["sdk_path"])
        _require(sdk.is_dir() and not sdk.is_relative_to(run), "Missing prebootstrapped SDK outside the current run")
        settings = state["settings"]
        _no_credentials(settings)
        route = settings["roles"]["sol"]
        _require(
            set(route) <= {"engine", "provider", "model", "reasoning_effort"}
            and (route.get("engine") or settings.get("engine")) == "opencode"
            and route.get("provider") in (None, "opencode")
            and settings.get("provider") in (None, "opencode")
            and route.get("model") == profile["model"]
            and route.get("reasoning_effort") == profile["reasoning_effort"],
            "Approved visual model/effort differs from the saved built-in Sol route",
        )
        _require(
            worker.get("engine") == worker.get("provider") == "opencode"
            and worker.get("configured") is False
            and worker.get("role") == "sol"
            and worker.get("model") == profile["model"]
            and not worker.get("provider_session")
            and worker.get("planning") is False,
            "Visual profile requires the original fresh built-in Sol worker",
        )
        identity = settings.get("transport_identities", {}).get("opencode", settings.get("transport_identity"))
        _require(
            isinstance(identity, dict)
            and set(identity)
            == {"engine", "version", "identity_version", "executable", "config_hashes", "environment_config_hashes"}
            and identity.get("engine") == "opencode"
            and identity.get("version") == VERSION
            and identity.get("identity_version") == 2
            and util.digest(identity) == profile["transport_identity_sha256"],
            "Saved transport identity differs from the approved visual profile",
        )
        _require(
            isinstance(identity.get("config_hashes"), dict)
            and identity["config_hashes"]
            and all(value is None for value in identity["config_hashes"].values())
            and isinstance(identity.get("environment_config_hashes"), dict)
            and all(value is None for value in identity["environment_config_hashes"].values()),
            "Existing custom/global configuration cannot be preserved by this isolated profile",
        )
        base = worker["command"]
        _require(
            isinstance(base, list) and command[: len(base)] == base and len(base) >= 2 and base[1] == "run",
            "Visual command differs from the actual worker",
        )
        args = {}
        _require(len(base[2:]) % 2 == 0, "Unsupported visual command arguments")
        for key, value in zip(base[2::2], base[3::2], strict=False):
            _require(
                key in ("--dir", "--format", "--agent", "--model", "--title", "--variant") and key not in args,
                "Unsupported, resumed or duplicate visual command option",
            )
            args[key] = value
        _require(
            args.get("--model") == profile["model"]
            and args.get("--variant") == profile["reasoning_effort"]
            and args.get("--format") == "json"
            and Path(args.get("--dir", "")).resolve() == root
            and isinstance(args.get("--agent"), str)
            and args["--agent"].startswith("autocode_sol"),
            "Visual command must use the exact saved model, effort, agent and workspace",
        )
        agent = args["--agent"]
        _require(reviewer == {"provider": "opencode", "model": profile["model"]}, "Unexpected visual reviewer")
        _require(
            command[len(base) :] == [value for row in images for value in ("--file", row["path"])] and images,
            "Visual attachments do not match the parent-prepared image list",
        )
        executable = shutil.which(base[0], path=env.get("PATH", ""))
        _require(
            executable
            and Path(executable).resolve() == Path(identity["executable"]).resolve()
            and util.file_hash(executable) == profile["executable_sha256"],
            "Approved OpenCode executable changed",
        )
        boundary = containment.verify(worker.get("tool_containment"))
        _require(
            boundary.get("allow_write") is False
            and Path(boundary["workspace"]).resolve() == root
            and boundary.get("tools") == ["bash"]
            and _json(env.get("AUTOCODE_TOOL_CONTAINMENT", "{}")) == boundary,
            "Visual profile requires an unchanged read-only contained shell",
        )
        _require(
            not any(key.startswith(("OPENAI_", "CODEX_", "AZURE_OPENAI_")) for key in env)
            and not any(
                env.get(key)
                for key in (
                    "OPENCODE_CONFIG",
                    "OPENCODE_CONFIG_DIR",
                    "OPENCODE_PERMISSION",
                    "OPENCODE_PURE",
                    "OPENCODE_DISABLE_DEFAULT_PLUGINS",
                )
            ),
            "Custom credential, endpoint or configuration environment is unsupported",
        )
        config = _json(env["OPENCODE_CONFIG_CONTENT"])
        original_config = _json(worker["environment"]["OPENCODE_CONFIG_CONTENT"])
        _require(
            isinstance(original_config, dict) and not original_config.get("plugin"),
            "Existing custom plugins cannot be preserved by this isolated profile",
        )
        expected_config = {**original_config, "plugin": [Path(plugin_path).as_uri()]}
        _require(config == expected_config, "Parent-prepared configuration differs from the actual contained worker")
        ignored = {"OPENCODE_CONFIG_CONTENT", "AUTOCODE_IMAGE_AUDIT"}
        _require(
            {key: value for key, value in env.items() if key not in ignored}
            == {key: value for key, value in worker["environment"].items() if key not in ignored},
            "Parent-prepared environment differs from the actual contained worker",
        )
        _require(
            isinstance(config, dict)
            and set(config) <= {"$schema", "share", "autoupdate", "shell", "agent", "plugin", "permission"}
            and config.get("shell") == boundary["shell"]
            and env.get("SHELL") == boundary["shell"],
            "Custom provider/configuration or changed containment shell is unsupported",
        )
        agents = config.get("agent")
        _require(isinstance(agents, dict) and set(agents) == {agent}, "Custom agent definitions cannot be preserved")
        definition = agents[agent]
        _require(
            isinstance(definition, dict)
            and set(definition) <= {"description", "mode", "model", "permission"}
            and definition.get("mode") == "primary"
            and definition.get("model") == profile["model"],
            "Custom agent model/options cannot be preserved",
        )
        policy = definition.get("permission")
        _require(
            isinstance(policy, dict)
            and set(policy) == {"*", "bash", "external_directory"}
            and policy["*"] == policy["external_directory"] == "deny"
            and isinstance(policy["bash"], dict)
            and policy["bash"]
            and all(
                isinstance(pattern, str) and action in ("allow", "ask", "deny")
                for pattern, action in policy["bash"].items()
            ),
            "Missing effective contained Bash denies/asks",
        )
        if "permission" in config:
            _require(config["permission"] == "deny", "Unsupported global inline permission policy")
        plugin = _owned(target, Path(plugin_path).absolute())
        helper = Path(__file__).with_name("autocode_image_delivery.mjs")
        _require(
            util.file_hash(plugin) == util.file_hash(helper), "Prepared image plugin differs from the runner helper"
        )
        _require(config.get("plugin") == [plugin.as_uri()], "Unexpected inherited plugin or plugin order")
        audit = _json(env["AUTOCODE_IMAGE_AUDIT"])
        _require(
            isinstance(audit, dict)
            and set(audit) <= {"path", "attempt_id", "binding_sha256", "max_requests", "reviewer"}
            and isinstance(audit.get("attempt_id"), str)
            and audit["attempt_id"]
            and _sha(audit.get("binding_sha256")),
            "Invalid parent image-audit binding",
        )
        audit_path = _owned(target, audit["path"])
        _require(not audit_path.exists(), "Image audit destination has already been used")
        _require(
            "max_requests" not in audit
            or (type(audit["max_requests"]) is int and 0 < audit["max_requests"] <= profile["max_requests"]),
            "Image audit cap exceeds approved profile",
        )
        approved_reviewer = {"provider": "opencode", "model": profile["model"], "agent": agent}
        _require(
            "reviewer" not in audit or audit["reviewer"] == approved_reviewer,
            "Image audit reviewer differs from actual stage",
        )
        audit.update(max_requests=profile["max_requests"], reviewer=approved_reviewer)
        pins = {str(plugin): util.file_hash(plugin)}
        for row in images:
            path = _owned(target, row["path"])
            _require(
                row.get("mime") == "image/png"
                and util.file_hash(path) == row["sha256"]
                and path.stat().st_size == row["bytes"]
                and all(type(row[key]) is int and row[key] > 0 for key in ("width", "height", "bytes"))
                and all(row[key] <= profile["image_limits"][key] for key in ("width", "height", "bytes"))
                and row["width"] * row["height"] <= profile["image_limits"]["pixels"],
                "Image exceeds approved no-transform bounds",
            )
            pins[str(path)] = row["sha256"]
        files = _sdk(sdk, profile["sdk_manifest_sha256"])
        original_home = env.get("HOME")
        data = env.get("XDG_DATA_HOME") or (str(Path(original_home) / ".local" / "share") if original_home else "")
        _require(
            data and Path(data).is_absolute() and ".." not in Path(data).parts,
            "Native data location must be unambiguous",
        )
        child = {
            key: env[key]
            for key in (
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
            )
            if key in env
        }
        for key, folder in (
            ("HOME", "home"),
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_CACHE_HOME", "cache"),
            ("XDG_STATE_HOME", "state"),
            ("OPENCODE_TEST_MANAGED_CONFIG_DIR", "managed"),
            ("TMPDIR", "tmp"),
        ):
            path = target / folder
            path.mkdir(mode=0o700)
            child[key] = str(path)
        destination = target / "config" / "opencode"
        for relative, expected in files.items():
            content = (sdk / relative).read_bytes()
            _require(hashlib.sha256(content).hexdigest() == expected, "SDK changed while staging")
            _write(destination / relative, content, pins)
        _write(destination / MANIFEST, (sdk / MANIFEST).read_bytes(), pins)
        _require(
            _sdk(sdk, profile["sdk_manifest_sha256"]) == files
            and _sdk(destination, profile["sdk_manifest_sha256"]) == files,
            "SDK changed during stage-local copy",
        )
        final = deepcopy(config)
        final.update(
            share="disabled",
            autoupdate=False,
            snapshot=False,
            model=profile["model"],
            small_model=profile["model"],
            enabled_providers=["openai"],
            plugin=[plugin.as_uri()],
        )
        final["agent"].update({name: {"disable": True} for name in ("title", "summary", "compaction")})
        child.update(
            XDG_DATA_HOME=data,
            OPENCODE_CONFIG_CONTENT=json.dumps(final),
            OPENCODE_DISABLE_AUTOUPDATE="1",
            OPENCODE_DISABLE_MODELS_FETCH="1",
            OPENCODE_DISABLE_PROJECT_CONFIG="1",
            AUTOCODE_TOOL_CONTAINMENT=env["AUTOCODE_TOOL_CONTAINMENT"],
            SHELL=boundary["shell"],
            AUTOCODE_IMAGE_AUDIT=json.dumps(audit),
            npm_config_offline="true",
            npm_config_cache=str(target / "cache" / "npm"),
        )
        for key in ("npm_config_userconfig", "npm_config_globalconfig"):
            path = target / (key + ".empty")
            _write(path, b"", pins)
            child[key] = str(path)
        argv = list(command)
        child_identity = {
            "executable": str(Path(executable).resolve()),
            "executable_sha256": profile["executable_sha256"],
            "command_sha256": util.digest(argv),
            "environment_sha256": util.digest(child),
        }
        proof = {
            "version": 1,
            "profile": profile,
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": state["goal_contract"]["revision"],
            "child_identity": child_identity,
            "original_transport": identity,
            "permission": policy,
            "bash_rule_order": list(policy["bash"].items()),
            "globally_wider_permissions": False,
            "native_data_path_only": data,
            "configuration_sources": {
                "inline_sha256": util.digest(final),
                "xdg_config": child["XDG_CONFIG_HOME"],
                "home": child["HOME"],
                "managed": child["OPENCODE_TEST_MANAGED_CONFIG_DIR"],
                "project_config_disabled": True,
            },
            "sdk_source": str(sdk),
            "sdk_manifest_sha256": profile["sdk_manifest_sha256"],
            "containment": boundary,
            "max_requests": audit["max_requests"],
            "reviewer": approved_reviewer,
            "visual_acceptance": "NOT_VERIFIED",
        }
        for name, value in (("profile.json", proof), ("audit-options.json", audit)):
            _write(target / name, (json.dumps(value, sort_keys=True) + "\n").encode(), pins)
        # Bash wildcard permissions are ordered. Retain the actual inline bytes,
        # not a sorted JSON reconstruction that changes last-match semantics.
        _write(target / "opencode-final.json", (child["OPENCODE_CONFIG_CONTENT"] + "\n").encode(), pins)
        for name in ("shell", "profile", "conformance"):
            _write(target / ("containment-" + name), Path(boundary[name]).read_bytes(), pins)
        containment.verify(boundary)
        _write(target / "visual-profile.py", Path(__file__).read_bytes(), pins)
        _require(profile == _profile(state), "Approved profile changed during preparation")
        return {
            "command": argv,
            "environment": child,
            "evidence_hashes": pins,
            "image_limits": deepcopy(profile["image_limits"]),
            "child_identity": child_identity,
        }

    return launch
