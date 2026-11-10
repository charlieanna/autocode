"""The per-role model, provider and reasoning-effort flags of the main parser.

Moved out of autocode_args.build_parser so the parser's size stays bounded. Each role flag takes
its job name (``--builder-model``) and its old code-name spelling (``--terra-model``) as an alias
with the same destination (autocode_names).
"""

from __future__ import annotations

try:
    from . import autocode_names as names
except ImportError:
    import autocode_names as names


def add_arguments(parser, default_models, role_models, efforts) -> None:
    """Add the per-task Planner, Builder, Tester and Completion Reviewer model, provider and effort flags."""
    for role, model in role_models.items():
        label = {
            "astra": "per-task Planner (and, in a run without their own routes, the reviewers')",
            "terra": "Builder",
            "sol": "Tester",
            "completion": "Completion Reviewer",
        }[role]
        parser.add_argument(
            *names.spellings(f"--{role}-model"),
            dest=f"{role}_model",
            metavar="MODEL",
            help=f"Override the {label} model (joint default: "
            f"{default_models[role]}; "
            f"Codex-only default: {model}; resumes keep the saved model)",
        )
    parser.add_argument(
        *names.spellings("--astra-provider"),
        dest="astra_provider",
        metavar="PROVIDER",
        help="Codex model_provider override for the per-task Planner's route",
    )
    parser.add_argument(
        *names.spellings("--terra-provider"),
        dest="terra_provider",
        metavar="PROVIDER",
        help="Codex model_provider override for the Builder (e.g. ZAI); default is the local Codex login",
    )
    parser.add_argument(
        *names.spellings("--sol-provider"),
        dest="sol_provider",
        metavar="PROVIDER",
        help="Codex model_provider override for the Tester (e.g. ZAI); default is the local Codex login",
    )
    parser.add_argument(
        "--completion-provider",
        help="Codex model_provider override for the completion owner; default is the local Codex login",
    )
    parser.add_argument("--reasoning-effort", choices=efforts, help="Reasoning effort for roles without a role flag")
    parser.add_argument(
        *names.spellings("--astra-reasoning-effort"),
        dest="astra_reasoning_effort",
        choices=efforts,
        help="Override reasoning effort for the per-task Planner's route only",
    )
    parser.add_argument(
        *names.spellings("--terra-reasoning-effort"),
        dest="terra_reasoning_effort",
        choices=efforts,
        help="Override reasoning effort for the Builder only",
    )
    parser.add_argument(
        *names.spellings("--sol-reasoning-effort"),
        dest="sol_reasoning_effort",
        choices=efforts,
        help="Override reasoning effort for the Tester only",
    )
    parser.add_argument(
        "--completion-reasoning-effort", choices=efforts, help="Override reasoning effort for the completion owner only"
    )
