"""One explicit native ownership context and the existing supervised transport.

The bounded planning caller supplies original handles; no status adoption.
The caller owns the existing run/checkout contexts, prepares the concrete native
launch and drives wait_for_stage. This adapter acquires no lock, creates no
second ProcessTree, and never reads private run state.
"""

import os
import time
from contextlib import contextmanager
from pathlib import Path

try:
    from . import autocode_native_image as images
    from . import autocode_supervision as supervision
    from . import autocode_util as util
    from . import autocode_visual_factory as factory
    from . import autocode_visual_protection as protection
    from . import autocode_writer_barrier as barriers
except ImportError:
    import autocode_native_image as images
    import autocode_supervision as supervision
    import autocode_util as util
    import autocode_visual_factory as factory
    import autocode_visual_protection as protection
    import autocode_writer_barrier as barriers


def _require(value, reason):
    if not value:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native launch context: " + reason)


class NativeRunContext:
    @protection._technical_hold
    def __init__(
        self, workspace, run_dir, *, run_handle, checkout_handle, start_state, snapshot, activation_intent=None,
        planning_abandonment_intent=None,
    ):
        run = Path(run_dir).absolute()
        # Refuse attaching an origin to earlier execution even if a caller
        # omitted its stage summaries. Never open state.json to infer history.
        journal = run / "visual-native-protection"
        if not journal.exists() or (journal.is_dir() and not any(journal.iterdir())):
            for name in ("iterations", "tasks", "visual-native-history"):
                old = run / name
                _require(
                    not old.exists() or (old.is_dir() and not any(old.iterdir())),
                    "prior execution artifacts cannot acquire a fresh native origin",
                )
            _require(
                not (run / "active-processes.json").exists(),
                "prior execution ownership cannot acquire a fresh native origin",
            )
        self.factory = factory.NativeVisualFactory(
            workspace,
            run,
            run_handle=run_handle,
            checkout_handle=checkout_handle,
            start_state=start_state,
            snapshot=snapshot,
            activation_intent=activation_intent,
            **({"planning_abandonment_intent": planning_abandonment_intent}
               if planning_abandonment_intent is not None else {}),
        )
        self._checkout_handle = checkout_handle
        self._run_handle = run_handle

    def held_launch(self, prepared, **options):
        return self._held(prepared, check=False, **options)

    def held_check(self, prepared, **options):
        return self._held(prepared, check=True, **options)

    @contextmanager
    def _held(self, prepared, *, check, command, environment, options, receipt_path, timeout, checkpoint=None):
        """Yield the genuine outer Popen and its original on_root callback.

        Only a concrete NativeProtection preparation may enter. The caller
        must use the callback inside the existing wait_for_stage cleanup scope.
        Dropping this context before release closes the barrier and its target
        cannot execute; the same supervisor retains cleanup ownership.
        """
        owner = self.factory.protection
        owner._ownership()
        expected_type = protection.PreparedCheck if check else protection.PreparedLaunch
        pending = owner._pending_checks if check else owner._pending
        _require(
            type(prepared) is expected_type and prepared.owner is owner and pending.get(prepared.nonce) is prepared,
            "missing exact original native preparation",
        )
        body = protection._json(prepared.body)
        relay = None
        if not check and body.get("native_isolation", {}).get("source_relay") is not None:
            try:
                from . import autocode_kilo_source_relay as source_relay
            except ImportError:
                import autocode_kilo_source_relay as source_relay
            _require(type(timeout) in (int, float) and timeout > 0, "Kilo source launch requires the original finite cap")
            relay = source_relay.pending_original(body["native_isolation"]["source_relay"])
            relay_deadline = time.monotonic() + timeout
        _require(
            body["command_sha256"] == util.digest(command) and body["environment_sha256"] == util.digest(environment),
            "target differs from original native preparation",
        )
        expected_cwd = Path(body["cwd"]) if check else owner.workspace
        _require(
            Path(options.get("cwd", "")).absolute() == expected_cwd and options.get("env") == environment,
            "native cwd/environment differs from the owned workspace",
        )
        # close_fds does not close explicit standard streams or pass_fds.
        # Duplicated descriptors share the original open-file-description lock;
        # a target must never acquire an alias that can unlock its controller.
        run_lock = os.fstat(self._run_handle.fileno())
        inherited = supervision.launch_options_identity(options)
        _require(
            all(
                (fd.get("device"), fd.get("inode")) != (run_lock.st_dev, run_lock.st_ino)
                for fd in (*inherited["stdio"], *inherited["pass_fds"])
            ),
            "run-lock authority cannot be inherited by a target",
        )
        require_source = owner.require_prepared_check if check else owner.require_prepared_source
        require_source(prepared)
        writer = None if check else owner._writer_executions.get(prepared.nonce)
        if not check and body["boundary"]["allow_write"]:
            _require(
                writer is not None and writer.native is prepared, "native writer lost the original domain preparation"
            )
            writer.recheck()
        image_review = body.get("native_isolation", {}).get("image_review")
        if image_review is not None:
            images.require_prompt_fd(image_review["prompt"], options.get("stdin"))
        actual = {
            **options,
            "close_fds": True,
            "pass_fds": tuple(dict.fromkeys((*options.get("pass_fds", ()), self._checkout_handle.fileno()))),
        }
        child = None
        launch_seal = None
        try:
            with barriers.WriterBarrier(command, environment, payload_directory=owner.run) as barrier:
                inner, launch_options = barrier.launch(actual)
                with supervision.launch(
                    inner, receipt_path=receipt_path, timeout=timeout, checkpoint=checkpoint, **launch_options
                ) as child:
                    barrier.spawned(child)

                    def on_root(tree, release_token):
                        nonlocal launch_seal
                        owner._ownership()
                        require_source(prepared)
                        if image_review is not None:
                            images.require_prompt_fd(image_review["prompt"], options.get("stdin"))
                        if relay is not None:
                            launch_seal = {"prepared_sha256": util.digest(body), "prepared_id": prepared.row_id,
                                           "root": tree.capture_root(), "command_sha256": body["command_sha256"],
                                           "environment_sha256": body["environment_sha256"]}
                            relay._register_original(source_relay._REGISTRATION, prepared=prepared, tree=tree,
                                                     deadline=relay_deadline, launch_seal=launch_seal)
                        if check:
                            owner.release_check(prepared, barrier, child, tree, release_token)
                        else:
                            self.factory.release_launch(prepared, barrier, child, tree, release_token)

                    yield child, on_root
        finally:
            if relay is not None:
                relay.close(original_exit=None if child is None else child.returncode, launch_seal=launch_seal)
