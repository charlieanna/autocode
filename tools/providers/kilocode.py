"""The configured Kilo facade with an explicit, concrete native launch seam.

Ordinary commands retain CommandProvider's config, identity and event contract.
Native support is not a configuration flag and cannot fall back to that path.
"""

from . import command


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
    ):
        if isolation is None:
            if containment is not None or image_preparation is not None:
                raise ValueError("Kilo native launch requires its original concrete isolation owner")
            return super().launch(
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
