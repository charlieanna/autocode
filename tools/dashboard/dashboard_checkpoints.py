"""Dashboard checkpoint adapter: exclusively uses the supported runner CLI."""


class CheckpointMixin:
    def checkpoint_action(self, data, *, restore=False):
        allowed = {"workspace", "run", "checkpoint_id"} | ({"expected_token", "request_id"} if restore else set())
        if set(data) - allowed:
            raise ValueError("Unexpected checkpoint fields")
        self.require_visible_task(data)
        self._require_unarchived(data.get("run"))
        workspace = self.workspace_for(data.get("workspace"))
        run = self.run_for(workspace, data.get("run")) if workspace else None
        if not run:
            raise ValueError("Choose a task connected to this dashboard")
        ident = data.get("checkpoint_id")
        if not isinstance(ident, str) or not ident:
            raise ValueError("Choose a saved code checkpoint")
        args = [
            "checkpoint",
            "--workspace",
            str(workspace),
            "--run-dir",
            str(run),
            "--restore" if restore else "--compare",
            ident,
        ]
        if restore:
            for name in ("expected_token", "request_id"):
                if not isinstance(data.get(name), str) or not data[name]:
                    raise ValueError("Refresh and confirm the exact checkpoint before restoring")
                args.extend(["--" + name.replace("_", "-"), data[name]])
        result, error = self._json_command(args, timeout=120)
        if error:
            if error.get("uncertain"):
                raise ValueError(
                    "Checkpoint request unconfirmed. Keep this request and reconcile it; do not start a different restore."
                )
            raise ValueError(error["message"])
        if restore:
            self.status_cache.pop(str(run), None)
            self.registry_cache["at"] = 0
        return result
