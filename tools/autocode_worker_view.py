"""Additive worker transport and observation projection, without host I/O."""

import re
from copy import deepcopy

PREFIX = [
    "autocode",
    "worker",
    "launch",
    "--workspace",
    "WORKSPACE",
    "--run-dir",
    "RUN_DIR",
    "--checkpoint",
    "CHECKPOINT",
    "--",
]


def execution(saved, *, observation=None, inspection=None):
    if not isinstance(saved, dict) or saved.get("mode") != "durable_local" or saved.get("version") != 1:
        return {"version": 1, "mode": "owned", "detach_supported": False}
    if not all(
        isinstance(saved.get(key), str) and re.fullmatch(r"[0-9a-f]{32}", saved[key])
        for key in ("launch_id", "controller_epoch")
    ):
        return {"version": 1, "mode": "unknown", "detach_supported": False}
    result = {
        key: deepcopy(saved.get(key))
        for key in ("version", "mode", "adapter", "launch_id", "controller_epoch", "boot_id", "runtime")
    }
    result.update(detach_supported=True, inspection=deepcopy(inspection))
    if observation and observation.get("checkpoint_token"):
        values = {
            "WORKSPACE": observation["workspace"],
            "RUN_DIR": observation["run_dir"],
            "CHECKPOINT": observation["checkpoint_token"],
        }
        result["next_command"] = [values.get(word, word) for word in PREFIX]
    return result
