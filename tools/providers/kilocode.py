"""The configured Kilo facade with an explicit, concrete native launch seam.

Ordinary commands retain CommandProvider's config, identity and event contract.
Native support is not a configuration flag and cannot fall back to that path.
"""

import json
from pathlib import Path

from . import command, kilocode_policy

try:
    from .. import autocode_output_cap as output_cap
except ImportError:
    import autocode_output_cap as output_cap


class KiloProvider(command.CommandProvider):
    def launch(
        self,
        role,
        workspace,
        run_dir,
        session,
        model,
        effort,
        allow_write,
        *,
        planning=False,
        report=None,
        schema=None,
        prompt_file=None,
        sandbox=None,
        env=None,
        containment=None,
        isolation=None,
        image_preparation=None,
        report_recovery=False,
        portable_shell=False,
    ):
        if report_recovery and (allow_write or not planning or isolation is not None):
            raise ValueError("Kilo report recovery requires ordinary read-only planning")
        if isolation is None:
            if image_preparation is not None or containment is not None and (allow_write or planning):
                raise ValueError("Kilo native launch requires its original concrete isolation owner")
            argv, child, saved = super().launch(
                role,
                workspace,
                run_dir,
                session,
                model,
                effort,
                allow_write,
                planning=planning,
                report=report,
                schema=schema,
                prompt_file=prompt_file,
                sandbox=sandbox,
                env=env,
            )
            output_cap.apply(child, output_cap.KILO_VARIABLE)
            # Kilo's JSON CLI withholds text/reasoning until a block completes.
            # Forward real native deltas through the same bounded activity hook
            # as OpenCode; this grants neither tool proof nor completion credit.
            config = json.loads(child.get("KILO_CONFIG_CONTENT", "{}"))
            if not isinstance(config, dict) or not isinstance(config.get("plugin", []), list):
                raise ValueError("Kilo activity configuration must contain a plugin array")
            activity = Path(__file__).with_name("opencode_activity.mjs").resolve().as_uri()
            config["plugin"] = [p for p in config.get("plugin", []) if p != activity] + [activity]
            report_schema = json.loads(Path(schema).read_text()) if schema and role == "requirements" else {}
            if "assistant_reply" in report_schema.get("properties", {}):
                hook = Path(__file__).with_name("requirements_stream.mjs").resolve().as_uri()
                config["plugin"] = [p for p in config["plugin"] if p != hook] + [hook]
            child["KILO_CONFIG_CONTENT"] = json.dumps(config)
            if planning or not allow_write:
                argv, child = kilocode_policy.restrict(
                    argv,
                    child,
                    workspace,
                    role=role,
                    request=containment,
                    report_recovery=report_recovery,
                    portable_shell=portable_shell and not planning,
                )
            return argv, child, saved
        try:
            from .. import autocode_native_role_isolation as native
        except ImportError:
            import autocode_native_role_isolation as native
        if type(isolation) is not native.KiloRoleIsolation:
            raise ValueError("Kilo native launch requires its exact original Kilo owner")
        return isolation.launch(
            self,
            role,
            workspace,
            run_dir,
            session,
            model,
            effort,
            allow_write,
            planning=planning,
            report=report,
            schema=schema,
            prompt_file=prompt_file,
            sandbox=sandbox,
            env=env,
            containment=containment,
            image_preparation=image_preparation,
        )


def load(name):
    ordinary = command.load(name)
    return KiloProvider(ordinary._config, ordinary._path)
