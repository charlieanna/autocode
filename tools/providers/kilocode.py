"""The configured Kilo facade with an explicit, concrete native launch seam.

Ordinary commands retain CommandProvider's config, identity and event contract.
Native support is not a configuration flag and cannot fall back to that path.
"""

import json
import re
import sys
from pathlib import Path

from . import command, kilocode_policy, kilocode_probe

try:
    from .. import autocode_output_cap as output_cap
    from .. import autocode_route_fit as route_fit
    from .. import autocode_route_levels as route_levels
except ImportError:
    import autocode_output_cap as output_cap
    import autocode_route_fit as route_fit
    import autocode_route_levels as route_levels

# The qualified Kilo release. On Linux, restricted stages already refuse another
# version (autocode_tool_containment's kernel shell); this is the same pin at the
# point a launch would otherwise reach the model, so a wrong install stops before
# any spend. macOS checkers use Kilo's own shell instead (#1766), so it has no pin.
RELEASE_VERSION = "7.8.3"


class KiloEffortError(RuntimeError):
    """The selected native reasoning variant is unsupported or cannot be verified."""


def check_version(version, platform=None):
    """Refuse a Kilo other than the qualified release where containment needs it; no request is launched."""
    if (platform or sys.platform) == "darwin":
        return
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+][\w.-]+)?", (version or "").strip())
    if not match or ".".join(match.groups()) != RELEASE_VERSION:
        raise RuntimeError(
            f"kilocode requires Kilo {RELEASE_VERSION} (found {version or 'no version'}); "
            "no provider request was launched"
        )


class KiloProvider(command.CommandProvider):
    # Every stage starts a fresh Kilo session, as the other providers' stages do (#1939): a resumed role
    # session grows stage after stage until Kilo compacts it on a context overflow. Read-only stages were
    # already fresh (#1739); a same-stage correction still resumes (autocode_provider_launch.prepare).
    RESUME_ACROSS_STAGES = False

    def check_tools(self, routes, workspace, run_dir, *, env, capture_command):
        """Read effective native policies before task spend, without a model or reasoning-variant check."""
        for index, part in enumerate(self._config["command"]):
            selector = (
                self._config["command"][index + 1]
                if part == "--agent" and index + 1 < len(self._config["command"])
                else part.removeprefix("--agent=")
                if part.startswith("--agent=")
                else None
            )
            if selector is not None:
                fields = set(re.findall(r"\{([a-z_]+)\}", command._mask_literal_braces(selector)))
                if fields - {"role", "model", "effort", "workspace", "run_dir"}:
                    raise RuntimeError(
                        "Kilo requires a static or role-based --agent selector for pre-dispatch tool verification; "
                        "a per-stage sandbox or report destination cannot identify the future agent; "
                        "no provider request was launched"
                    )
        observed = {}
        for role, route in routes.items():
            argv, child, _ = super().launch(
                role,
                workspace,
                run_dir,
                None,
                route.get("model"),
                route.get("reasoning_effort"),
                role == "builder",
                planning=role != "builder",
                env=env,
            )
            _, original = kilocode_policy.effective_agent(argv, child, workspace, cache=observed)
            policy = kilocode_policy.intersect(original.get("permission"), ("bash",))
            kilocode_policy.require_capture(policy, role=role, capture_command=capture_command)

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
            if image_preparation is not None or containment is not None and allow_write:
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
            self.validate_effort(model, effort, workspace, env=child, role=role)
            output_cap.apply(child, output_cap.KILO_VARIABLE)
            # Kilo's JSON CLI withholds text/reasoning until a block completes.
            # Forward real native deltas through the same bounded activity hook
            # as OpenCode; this grants neither tool proof nor completion credit.
            config = json.loads(child.get("KILO_CONFIG_CONTENT", "{}"))
            if not isinstance(config, dict) or not isinstance(config.get("plugin", []), list):
                raise ValueError("Kilo activity configuration must contain a plugin array")
            activity = Path(__file__).with_name("opencode_activity.mjs").resolve().as_uri()
            config["plugin"] = [p for p in config.get("plugin", []) if p != activity] + [activity]
            # Kilo summarizes a conversation on its own once it nears the window (compaction.auto); a
            # stage must see its handoff whole (#214), so that is off and a context that still runs
            # out is a named stop (autocode_context_window, #1942), never a silent summary.
            compaction = config.get("compaction") if isinstance(config.get("compaction"), dict) else {}
            config["compaction"] = {**compaction, "auto": False}
            report_schema = json.loads(Path(schema).read_text()) if schema and role == "requirements" else {}
            if "assistant_reply" in report_schema.get("properties", {}):
                hook = Path(__file__).with_name("requirements_stream.mjs").resolve().as_uri()
                config["plugin"] = [p for p in config["plugin"] if p != hook] + [hook]
            child["KILO_CONFIG_CONTENT"] = json.dumps(config)
            argv, child = kilocode_policy.restrict(
                argv,
                child,
                workspace,
                role=role,
                request=containment,
                report_recovery=report_recovery,
                portable_shell=portable_shell,
                write_stage=allow_write and not planning,
            )
            return argv, child, saved
        try:
            from .. import autocode_native_role_isolation as native
        except ImportError:
            import autocode_native_role_isolation as native
        if type(isolation) is not native.KiloRoleIsolation:
            raise ValueError("Kilo native launch requires its exact original Kilo owner")
        result = isolation.launch(
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
        self.validate_effort(model, effort, workspace, env=result[1], role=role)
        return result

    def validate_effort(self, model, effort, workspace=None, *, env=None, role=None):
        """Validate the exact native variant; an unknown name otherwise silently becomes the model default."""
        if not effort:
            return
        result = self._variant_listing(workspace, env)
        levels = route_levels.parse(result.stdout)
        if model not in levels and isinstance(result, command.env_prep.KeptCheck):
            # A kept listing is asked again once before a route is called unlisted (#3078): the person
            # cannot act on "name the model as Kilo lists it" when the route already is how Kilo lists it.
            levels = route_levels.parse(self._variant_listing(workspace, env, fresh=True).stdout)
        if model not in levels:
            raise KiloEffortError(
                f"Kilo's reasoning-variant listing has no variants for {model}; "
                "name the model as Kilo lists it; no model request was launched"
            )
        if effort not in levels[model]:
            supported = ", ".join(levels[model]) or "none (omit the explicit effort)"
            fit = route_fit.nearest(effort, levels[model])
            advice = (
                f"Name one of them with {route_fit.flag(role)} {fit}"
                if role and fit
                else "Choose a supported variant explicitly"
            )
            raise KiloEffortError(
                f"Kilo model {model} does not support reasoning variant {effort!r}; supported variants: {supported}. "
                f"{advice}; no model request was launched"
            )

    def _variant_listing(self, workspace, env, *, fresh=False):
        """The whole ``models --verbose`` listing, answered from a kept check unless ``fresh``."""
        words = [self._config["command"][0], "models", "--verbose"]
        try:
            # The whole verbose listing, not just this model's provider: route discovery reads the same
            # listing at the start of a task, so within a run this is answered from that check. A listing
            # cut off before its end is neither kept nor trusted, and is asked once more (#3078).
            result = kilocode_probe.probe(
                words,
                lambda seconds: command.env_prep.preflight_run(
                    words,
                    self._preflight_environment(env),
                    cwd=workspace,
                    require_executable=env is not None,
                    complete=route_levels.complete,
                    fresh=fresh,
                    capture_output=True,
                    text=True,
                    timeout=seconds,
                ),
                complete=route_levels.complete,
            )
        # Every way this can fail gets its own sentence: one message for four causes left a stopped run
        # unable to say which happened (#2959).
        except (kilocode_probe.KiloProbeTimeout, kilocode_probe.KiloProbeIncomplete) as error:
            raise KiloEffortError(str(error)) from error
        except OSError as error:
            raise KiloEffortError(
                f"Kilo's reasoning-variant listing could not start: {error}; no model request was launched"
            ) from error
        if result.returncode:
            raise KiloEffortError(
                f"Kilo's reasoning-variant listing failed (exit {result.returncode}); no model request was launched"
            )
        return result

    def local_settings(self, workspace=None, *, env=None):
        settings = super().local_settings(workspace, env=env)
        check_version(settings.get("version"))
        return settings


def runs_kilo(config) -> bool:
    """Whether a command provider config launches kilo: such a connection is a Kilo connection (#2010)."""
    words = config.get("command") if isinstance(config, dict) else None
    return isinstance(words, list) and bool(words) and isinstance(words[0], str) and Path(words[0]).name == "kilo"


def load(name):
    ordinary = command.load(name)
    return KiloProvider(ordinary._config, ordinary._path)
