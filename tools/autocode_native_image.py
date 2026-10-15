"""Stage a visual packet on its original isolated native role before sealing.

This does not capture images, authorize a launch or accept a review. The actual
native caller supplies original capture authority; the original isolation owner
and held launch authenticate staged inputs and the readonly prompt descriptor.
"""

import fcntl
import hashlib
import json
import os
import stat
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_native_locators as native_locators
    from . import autocode_util as util
    from . import autocode_visual_profile as profile
    from . import autocode_visual_runtime as visual
except ImportError:
    import autocode_native_locators as native_locators
    import autocode_util as util
    import autocode_visual_profile as profile
    import autocode_visual_runtime as visual


def _require(value, reason):
    if not value:
        raise ValueError("Native image preparation: " + reason)


class NativeImagePreparation:
    """One packet bound to the exact original isolation and native caller."""

    def __init__(
        self,
        *,
        native,
        isolation,
        state,
        workspace,
        run_dir,
        base,
        snapshot,
        prompt,
        schema,
        events,
        adapter,
        render_prompt,
        regression_proof_current=False,
        regression_handoff=None,
    ):
        authority = getattr(native, "image_capture_authority", None)
        _require(
            callable(authority) and getattr(authority, "__self__", None) is native, "missing original capture method"
        )
        self.native, self.isolation, self.state = native, isolation, state
        self.root, self.run, self.base = Path(workspace).resolve(), Path(run_dir).resolve(), Path(base)
        _require(
            native.workspace.resolve() == self.root and native.run_dir.resolve() == self.run,
            "foreign original capture caller",
        )
        self.profile = profile._profile(state)
        _require(self.profile == native.intent, "reviewed profile differs from original operator intent")
        self.snapshot, self.raw_prompt = deepcopy(snapshot), prompt
        self.schema, self.events, self.adapter = Path(schema), Path(events), adapter
        self._schema_bytes = self.schema.read_bytes()
        _require(callable(render_prompt), "missing runner prompt renderer")
        self.render_prompt = render_prompt
        self.regression_proof_current, self.regression_handoff = regression_proof_current, deepcopy(regression_handoff)
        self._staged = self._context = None
        self._source_binding = util.digest(
            {"settings": state["settings"], "goal": state["goal_contract"], "task": state["current_task"]}
        )

    def require_caller(self, caller_type):
        authority = self.native.image_capture_authority
        _require(
            type(self.native) is caller_type
            and authority.__self__ is self.native
            and authority.__func__ is caller_type.image_capture_authority,
            "missing concrete original capture caller",
        )

    def _check(self):
        _require(
            profile._profile(self.state) == self.profile == self.native.intent
            and self._source_binding
            == util.digest(
                {
                    "settings": self.state["settings"],
                    "goal": self.state["goal_contract"],
                    "task": self.state["current_task"],
                }
            ),
            "profile, saved settings or task changed",
        )
        self.native.require_initial_input(self.state)
        _require(self.schema.read_bytes() == self._schema_bytes, "original review schema changed")

    def stage(self, owner, command, environment):
        _require(owner is self.isolation and self._staged is None, "foreign or repeated isolation staging")
        self._check()
        owner.require_image_profile(self.profile)
        target = owner._prepared["target"] / "visual"

        def qualify(**value):
            argv, child = value["command"], value["env"]
            _require(
                argv == [*command, *(item for row in value["images"] for item in ("--file", row["path"]))],
                "changed image command",
            )
            options = json.loads(child["AUTOCODE_IMAGE_AUDIT"])
            options["max_requests"] = self.profile["max_requests"]
            child["AUTOCODE_IMAGE_AUDIT"] = json.dumps(options)
            return {
                "command": argv,
                "environment": child,
                "evidence_hashes": dict(owner._prepared["files"]),
                "image_limits": deepcopy(self.profile["image_limits"]),
            }

        context, argv, child, prompt = visual._prepare(
            self.state,
            "sol",
            self.root,
            self.run,
            self.base,
            command,
            environment,
            self.raw_prompt,
            launch_authority=qualify,
            current_snapshot=self.snapshot,
            scope_authority=self.native.context.factory.scope_authority,
            staging_directory=target,
            capture_authority=self.native.image_capture_authority,
        )
        _require(context and context.get("status") == "STAGED", (context or {}).get("reason", "no staged packet"))
        _require(not Path(context["audit_path"]).exists(), "audit was used during image staging")
        self._staged = context
        self._prompt = prompt
        return argv, child, {str(path): util.file_hash(path) for path in target.iterdir() if path.is_file()}

    def render(self, command, environment, boundary):
        _require(self._staged is not None and self._context is None, "packet is not awaiting final configuration")
        self._check()
        prompt = self.render_prompt(
            self._prompt,
            schema=util.read_object(self.schema),
            events=self.events,
            adapter=self.adapter,
            boundary=boundary,
            regression_proof_current=self.regression_proof_current,
            regression_handoff=self.regression_handoff,
        )
        target = Path(self._staged["manifest_path"]).parent
        pins = dict(
            [
                visual._write(target / "final.prompt.md", prompt.encode()),
                visual._write(target / "schema.json", self._schema_bytes),
            ]
        )
        self.prompt_path = target / "final.prompt.md"
        self.prompt = prompt
        self.prompt_binding = {"path": str(self.prompt_path), "sha256": pins[str(self.prompt_path)]}
        self.schema_binding = {"path": str(target / "schema.json"), "sha256": pins[str(target / "schema.json")]}
        return pins

    def freeze(self, command, environment, descriptor):
        _require(
            self._staged is not None and self._context is None and hasattr(self, "prompt_binding"),
            "packet lacks the final sealed prompt",
        )
        self._check()
        _require(not Path(self._staged["audit_path"]).exists(), "native setup used the image audit")
        _require(
            command == self._staged["command"]
            and json.loads(environment["AUTOCODE_IMAGE_AUDIT"]) == self._staged["audit_options"]
            and descriptor.get("image_review") == {"prompt": self.prompt_binding, "schema": self.schema_binding},
            "final image attachments, audit or prompt binding changed",
        )
        context = {
            key: deepcopy(value)
            for key, value in self._staged.items()
            if key not in ("launch_manifest", "launch_sha256")
        }
        context = {
            **context,
            "status": "READY",
            "command": deepcopy(command),
            "environment_sha256": util.digest(environment),
            "config_sha256": util.digest(
                json.loads(
                    environment[
                        "KILO_CONFIG_CONTENT" if descriptor.get("backend") == "kilocode" else "OPENCODE_CONFIG_CONTENT"
                    ]
                )
            ),
            "native_child_identity": deepcopy(descriptor["child_identity"]),
            "native_isolation": deepcopy(descriptor),
            "child_identity": {**descriptor["child_identity"], "environment_sha256": util.digest(environment)},
            "prompt": deepcopy(self.prompt_binding),
            "schema": deepcopy(self.schema_binding),
        }
        context["pins"].update(descriptor["files"])
        context["pins"][descriptor["manifest"]] = descriptor["manifest_sha256"]
        boundary = native_locators.expand_boundary(environment["AUTOCODE_TOOL_CONTAINMENT"], descriptor)
        context["pins"].update(
            {boundary[key]: boundary[key + "_sha256"] for key in ("profile", "shell", "conformance")}
        )
        context["pins"][str(self.schema)] = hashlib.sha256(self._schema_bytes).hexdigest()
        path, sha = visual._write(
            Path(self._staged["manifest_path"]).parent / "ready.json",
            (json.dumps(context, sort_keys=True) + "\n").encode(),
        )
        context.update(launch_manifest=path, launch_sha256=sha)
        self._context = context
        return deepcopy(context)

    def context(self):
        _require(self._context is not None, "image packet is not finalized")
        return deepcopy(self._context)

    def pins(self, context):
        _require(context == self._context and self._context is not None, "record differs from original image packet")
        self._check()
        return {**context["pins"], context["launch_manifest"]: context["launch_sha256"]}


def require_prompt_fd(binding, stream):
    """Check the actual readonly stdin without changing its shared file offset."""
    _require(isinstance(binding, dict) and set(binding) == {"path", "sha256"}, "missing exact prompt binding")
    path = Path(binding["path"])
    _require(path.is_file() and not any(part.is_symlink() for part in (path, *path.parents)), "prompt pathname changed")
    fd = stream.fileno()  # Pipes, DEVNULL and integer sentinels are never accepted.
    actual, expected = os.fstat(fd), path.stat()
    _require(
        stat.S_ISREG(actual.st_mode)
        and actual.st_mode & 0o222 == 0
        and (actual.st_dev, actual.st_ino, actual.st_size) == (expected.st_dev, expected.st_ino, expected.st_size)
        and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY,
        "stdin is not the original readonly prompt descriptor",
    )
    _require(os.lseek(fd, 0, os.SEEK_CUR) == 0, "stdin prompt has already been consumed")
    digest = hashlib.sha256()
    offset = 0
    while offset < actual.st_size:
        raw = os.pread(fd, min(65536, actual.st_size - offset), offset)
        _require(raw, "prompt descriptor truncated")
        digest.update(raw)
        offset += len(raw)
    final = os.fstat(fd)
    _require(
        (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns)
        == (actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns)
        and digest.hexdigest() == binding["sha256"] == util.file_hash(path),
        "prompt bytes changed",
    )
