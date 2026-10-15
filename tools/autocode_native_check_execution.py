"""Original-owner, model-free checks for the first approved validate task.

No controller route, provider identity, private state reader, or second process
owner is installed. The explicit verification caller supplies the original
context and parent sink. Unsupported setup remains held before a target starts.
"""

import json  # noqa: F401 - preserve existing import behavior
import os
import re  # noqa: F401 - preserve existing import behavior
import shlex
import sys
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_command_supervision as supervision
    from . import autocode_go_tests as go_tests
    from . import autocode_native_check_runtime as check_runtime
    from . import autocode_native_launch as native_launch
    from . import autocode_native_parent as parent
    from . import autocode_node_tests as node_tests
    from . import autocode_python_tests as python_tests
    from . import autocode_runner_check as runner_check
    from . import autocode_source_scope as source_scope
    from . import autocode_tool_containment as containment
    from . import autocode_util as util
    from . import autocode_verification_copy as copies
    from . import autocode_verification_plan as plans
    from . import autocode_visual_protection as protection
    from . import autocode_vitest_tests as vitest_tests
except ImportError:
    import autocode_command_supervision as supervision
    import autocode_go_tests as go_tests
    import autocode_native_check_runtime as check_runtime
    import autocode_native_launch as native_launch
    import autocode_native_parent as parent
    import autocode_node_tests as node_tests
    import autocode_python_tests as python_tests
    import autocode_runner_check as runner_check
    import autocode_source_scope as source_scope
    import autocode_tool_containment as containment
    import autocode_util as util
    import autocode_verification_copy as copies
    import autocode_verification_plan as plans
    import autocode_visual_protection as protection
    import autocode_vitest_tests as vitest_tests


def _require(value, reason):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native check: " + reason)


class NativeCheckExecution:
    @protection._technical_hold
    def __init__(self, context, state, *, persist, review_record=None):
        _require(type(context) is native_launch.NativeRunContext, "original context required")
        sink = getattr(persist, "__self__", None)
        _require(
            type(sink) is parent.ParentSink
            and getattr(persist, "__func__", None) is parent.ParentSink.write
            and sink.factory is context.factory,
            "original concrete parent sink required",
        )
        self.context, self.state, self.sink, self.persist = context, state, sink, persist
        self.owner = context.factory.protection
        self.workspace, self.run = self.owner.workspace, self.owner.run
        self.review_record = deepcopy(review_record)
        self.scope = None
        self._produced = {}
        self._serial = 0
        self._binding = protection._bytes(self._fresh()["binding"])

    def _fresh(self):
        self.sink.require_ready()
        current = source_scope.snapshot(self.workspace, self.state)
        value = self.owner.readonly_task_authority(self.state, snapshot=current, review_record=self.review_record)
        if hasattr(self, "_binding"):
            _require(protection._bytes(value["binding"]) == self._binding, "original assignment/source changed")
        return value

    def require_replay(self, workspace, run_dir, state, record, checks, *, execution_identity=None):
        facts = self._fresh()
        _require(
            isinstance(record, dict) and record.get("source_revision") == facts["binding"]["source"]["revision"],
            "replay summary differs from the original protected current source",
        )
        _require(
            Path(workspace).resolve() == self.workspace and Path(run_dir).resolve() == self.run and state is self.state,
            "foreign verification caller state/run",
        )
        # The later Sol obligation and cache are intentionally not enabled by 5A.
        _require(execution_identity is None, "native execution-identity probes/reuse are not enabled")
        allowed = plans.approved_commands(state)
        _require(
            allowed and all(check.get("command") in allowed for check in checks),
            "first native validation requires exact approved readonly checks",
        )

    @contextmanager
    def copy(
        self,
        workspace,
        output,
        *,
        command,
        patch=None,
        tests=(),
        files=None,
        links=None,
        source_paths=(),
        dependencies_from=None,
        generated_record=None,
        generated_unrecorded=False,
        ignored_inputs=None,
    ):
        facts = self._fresh()
        _require(
            self.scope is None and Path(workspace).resolve() == self.workspace, "overlapping or foreign native copy"
        )
        _require(
            isinstance(command, str) and command in plans.approved_commands(self.state),
            "check is not an original approved executable command",
        )
        _require(
            not patch
            and not tests
            and not files
            and not links
            and generated_record is None
            and not generated_unrecorded
            and ignored_inputs is None
            and (dependencies_from is None or Path(dependencies_from).resolve() == self.workspace),
            "unsupported overlay/dependency/generated/probe input; no target launched",
        )
        selected = facts["binding"]["source"].get("source_paths", [])
        _require(not source_paths or list(source_paths) == selected, "foreign selected source inventory")
        _require(
            sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(),
            "native readonly checks need the existing macOS kernel sandbox",
        )
        _require(
            not any(
                (self.workspace / name).exists() or (self.workspace / name).is_symlink()
                for name in ("node_modules", ".venv", "venv", "vendor")
            ),
            "task dependency copies need explicit authenticated support; no target launched",
        )
        _require(
            not any(
                identity.startswith(("symlink:", "submodule:", "uninitialized-submodule"))
                for identity in facts["binding"]["source"]["files"].values()
            ),
            "source links/submodules need explicit authenticated support",
        )
        output = self.owner._safe(output)
        _require(output.is_relative_to(self.run) and not output.exists(), "fresh owned check output required")
        output.mkdir(parents=True)
        control = self.workspace / ".autocode" / ("tool-containment-" + uuid.uuid4().hex)
        control.mkdir(mode=0o700)
        scratch = control / "scratch"
        scratch.mkdir(mode=0o700)
        writable = scratch / "writable"
        writable.mkdir(mode=0o700)
        (scratch / "native-git-empty").mkdir(mode=0o500)
        tree = scratch / "verification"
        scope = {
            "control": control,
            "scratch": scratch,
            "tree": tree,
            "output": output,
            "copy": None,
            "command": command,
            "actual_command": None,
            "setup_index": 0,
            "slots": {},
            "result": None,
        }
        self.scope = scope
        try:
            descriptor = copies.create(self.workspace, scratch, source_paths=selected, setup_executor=self._setup)
            scope["copy"] = descriptor
            expected_tree, _identity = copies.execution(descriptor["manifest"], descriptor["sha256"], self.workspace)
            _require(expected_tree == tree and scope["setup_index"] == 5, "incomplete original clean copy setup")
            self._fresh()
            actual = command
            for name in dict.fromkeys((str(self.workspace.resolve()), str(self.workspace))):
                actual = actual.replace(name, str(tree))
            scope["actual_command"] = actual
            yield tree
            self._fresh()
            copies.execution(descriptor["manifest"], descriptor["sha256"], self.workspace)
        finally:
            # Inputs and failures remain retained. Removing them would destroy
            # the same journal's later original-byte replay.
            self.scope = None

    def _setup(self, command, *, cwd, env, timeout):
        scope = self.scope
        _require(scope is not None and scope["copy"] is None and Path(cwd) == scope["tree"], "foreign copy setup")
        args = copies.GIT_SETUP_ARGUMENTS
        index = scope["setup_index"]
        _require(index < len(args) and command == ["/usr/bin/git", *args[index]], "unapproved copy setup argv")
        expected_env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(scope["scratch"]),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }
        _require(env == expected_env, "copy setup environment changed")
        native = copies.native_git_setup(scope["scratch"])
        result = self._execute(
            shlex.join(native["commands"][index]),
            scope["output"] / f"setup-{index + 1}.log",
            timeout=timeout,
            setup=True,
            environment=native["environment"],
        )
        scope["setup_index"] += 1
        return result

    def result_path(self, tree, output):
        scope = self.scope
        _require(scope is not None and Path(tree) == scope["tree"], "suite has no original copy scope")
        output = Path(output)
        _require(output.parent == scope["output"] and output.name not in scope["slots"], "foreign/repeated result slot")
        scratch = scope["scratch"] / "writable" / output.name
        scope["slots"][output.name] = (scratch, output)
        return scratch

    def instrument(self, framework, command, xml, tree):
        scope = self.scope
        _require(
            scope is not None
            and command == scope["actual_command"]
            and Path(tree) == scope["tree"]
            and any(Path(xml) == pair[0] for pair in scope["slots"].values()),
            "foreign suite instrumentation",
        )
        # Use the existing lower runner parsers; the upper verify caller also
        # compares this with its unchanged instrumentation before execution.
        if node_tests.command_words(command):
            actual = node_tests.instrument(command, xml)
        elif vitest_tests.command_words(command, tree):
            actual = vitest_tests.instrument(command, xml, tree)
        elif framework == "pytest" and " -m pytest" in command:
            actual = f"{command} --junitxml={shlex.quote(str(xml))}"
        elif framework == "go" and (invocation := go_tests.parse(command)):
            actual = invocation.instrument()
        else:
            actual = python_tests.verbose_unittest(command)
        scope["actual_command"] = actual
        return actual

    def _environment(self):
        scope = self.scope
        writable = str(scope["scratch"] / "writable")
        return {
            "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": writable,
            "TMPDIR": writable,
            "TMP": writable,
            "TEMP": writable,
            "BASH_ENV": "/dev/null",
            "ENV": "/dev/null",
            "SHELL": "/bin/bash",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "OPENSSL_CONF": "/dev/null",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(scope["tree"] / "src") + os.pathsep + str(scope["tree"]),
            "CI": "1",
            "LANG": "en_US.UTF-8",
        }

    def run_command(self, command, cwd, log_path, *, timeout, env=None, checkpoint=None):
        scope = self.scope
        _require(
            scope is not None
            and scope["copy"] is not None
            and scope["result"] is None
            and command == scope["actual_command"]
            and Path(cwd) == scope["tree"]
            and Path(log_path).parent == scope["output"]
            and env is None
            and checkpoint is None,
            "command needs its original exact approved copy and default parent sink",
        )
        result = self._execute(command, log_path, timeout=timeout, setup=False, environment=self._environment())
        scope["result"] = result
        return result

    def _execute(self, command, log_path, *, timeout, setup, environment):
        facts = self._fresh()
        scope = self.scope
        runtime = check_runtime.inputs(self.workspace, facts["binding"]["contract"]["body"], setup=setup)
        roots = sorted({str(Path(sys.prefix).resolve()), str(Path(sys.base_prefix).resolve())})
        native = copies.native_git_setup(scope["scratch"]) if setup else None
        if native is not None:
            roots = sorted({*roots, native["executable"]})
        _require(
            all(not Path(name).is_relative_to(self.workspace) for name in roots),
            "original workspace interpreter dependency is unsupported",
        )
        profile = scope["control"] / ("check-" + uuid.uuid4().hex + ".sb")
        policy = containment._render_policy(
            self.workspace,
            scope["scratch"],
            read_workspace=False,
            read_roots=roots,
            denied_read_paths=(self.workspace,),
            read_exceptions=(scope["scratch"],),
            write_roots=(scope["tree"] / ".git" if setup else scope["scratch"] / "writable",),
            denied_write_paths=(() if setup else (scope["tree"],)),
            write_files=(log_path,),
        )
        policy += check_runtime.policy(runtime)
        profile.write_text(policy)
        profile.chmod(0o400)
        binaries = [
            Path("/usr/bin/sandbox-exec"),
            Path("/bin/bash"),
            Path("/usr/bin/git"),
            Path(sys.executable).resolve(),
        ]
        if native is not None:
            binaries.append(Path(native["executable"]))
        boundary = {
            "scratch": str(scope["scratch"]),
            "tree": str(scope["tree"]),
            "profile": str(profile),
            "profile_sha256": util.file_hash(profile),
            "setup": setup,
            "runtime_roots": roots,
            "executables": {str(path): util.file_hash(path) for path in binaries},
            "copy": deepcopy(scope["copy"]),
            "output": str(log_path),
            "result_slots": [str(pair[0]) for pair in scope["slots"].values()],
            **({"node_runtime": runtime} if runtime is not None else {}),
        }
        target = ["/usr/bin/sandbox-exec", "-f", str(profile), "/bin/bash", "--noprofile", "--norc", "-c", command]
        handle = self.owner.prepare_check(
            self.state,
            facts["binding"]["source"],
            purpose="verification_copy_setup" if setup else "initial_validate_check",
            command=target,
            environment=environment,
            cwd=scope["tree"],
            boundary=boundary,
            review_record=self.review_record,
        )

        @contextmanager
        def held(**options):
            self.sink.require_ready()
            current = source_scope.snapshot(self.workspace, self.state)
            _require(
                self.owner._binding(self.state, current) == facts["binding"],
                "parent task/settings/source changed before check launch",
            )
            with self.context.held_check(handle, command=target, environment=environment, **options) as (
                child,
                on_root,
            ):

                def checked_root(tree, release_token):
                    self.sink.require_ready()
                    current = source_scope.snapshot(self.workspace, self.state)
                    _require(
                        self.owner._binding(self.state, current) == facts["binding"],
                        "durable admission changed source/task/settings before release",
                    )
                    on_root(tree, release_token)

                yield child, checked_root

        with runner_check.track(
            self.state, self.run, "native_check", "Approved readonly verification", self.persist, deferred=True
        ):
            result = supervision.run(
                command, scope["tree"], log_path, timeout=timeout, env=environment, held_launch=held
            )
        _require(
            type(result.get("exit_code")) is int and not result.get("error") and not result.get("timed_out"),
            "original check did not collect a completed target: "
            + str(
                result.get("error")
                or ("original deadline expired" if result.get("timed_out") else "no collected target exit")
            )
            + "; retained output: "
            + str(result.get("output")),
        )
        selector = self.owner.finish_check(handle, result, after=source_scope.snapshot(self.workspace, self.state))
        result["native_check"] = selector
        if not setup:
            self._serial += 1
            self._produced[selector["finished_id"]] = self._serial
        return result

    def retain_results(self, receipt):
        scope = self.scope
        _require(scope is not None and scope["result"] is receipt, "result has no actual original check")
        self.authenticate(receipt)
        for scratch, retained in scope["slots"].values():
            if scratch.exists():
                _require(
                    scratch.is_file() and not scratch.is_symlink() and not retained.exists(),
                    "invalid or repeated structured result",
                )
                # Parent alone promotes result bytes after genuine quiescence.
                with retained.open("xb") as stream:
                    stream.write(scratch.read_bytes())
        return None

    def checkpoint(self):
        self._fresh()
        return self._serial

    def authenticate(self, receipt, *, after=None):
        selector = receipt.get("native_check") if isinstance(receipt, dict) else None
        serial = self._produced.get(selector.get("finished_id")) if isinstance(selector, dict) else None
        _require(
            serial is not None and (after is None or serial > after),
            "receipt is not a fresh execution by this original check owner",
        )
        self.sink.require_ready()
        current = source_scope.snapshot(self.workspace, self.state)
        return self.owner.authenticate_check(
            receipt, state=self.state, snapshot=current, review_record=self.review_record
        )
