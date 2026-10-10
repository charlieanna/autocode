"""Private CLI transport for controller tokens; no token grants authority by itself.

Callers keep the returned environment separate from public command records. The
CLI resolves only an explicitly supplied ``-``; provider/test environments must
always withhold CONTROL_TOKEN_ENV_VARS, including explicit pass-through requests.
"""

from collections.abc import Mapping, Sequence

TOKEN_ENV_VARS = {
    "resolver_token": "AUTOCODE_RESOLVER_TOKEN",
    "job_retry_token": "AUTOCODE_JOB_RETRY_TOKEN",
    "recover_job_report": "AUTOCODE_RECOVER_JOB_REPORT",
    "approve_goal": "AUTOCODE_APPROVE_GOAL_TOKEN",
    "review_token": "AUTOCODE_REVIEW_TOKEN",
    "expected_goal_token": "AUTOCODE_EXPECTED_GOAL_TOKEN",
    "expected_recovery_token": "AUTOCODE_EXPECTED_RECOVERY_TOKEN",
}
CHECKPOINT_TOKEN_ENV_VARS = {"expected_token": "AUTOCODE_CHECKPOINT_EXPECTED_TOKEN"}
PROGRAM_TOKEN_ENV_VARS = {"token": "AUTOCODE_PROGRAM_APPROVAL_TOKEN"}
CONTROL_TOKEN_ENV_VARS = frozenset(
    (*TOKEN_ENV_VARS.values(), *CHECKPOINT_TOKEN_ENV_VARS.values(), *PROGRAM_TOKEN_ENV_VARS.values())
)


def resolve_placeholders(args, parser, environ: Mapping[str, str], *, fields=TOKEN_ENV_VARS) -> None:
    """Resolve explicit selectors only; ambient variables never select an action."""
    for attribute, variable in fields.items():
        if getattr(args, attribute, None) == "-":
            value = environ.get(variable)
            if not value:
                parser.error(f"token value `-` requires {variable} in the environment")
            setattr(args, attribute, value)


def private_command(command: Sequence[str], *, argument_offset: int = 0) -> tuple[list[str], dict[str, str]]:
    """Copy a CLI command with tokens replaced by ``-`` and a private launch map.

    argument_offset skips the executable/runner prefix. Generic --token and
    --expected-token belong only to their exact subcommands. Respect argparse's
    end-of-options marker and last-value behavior; leave malformed options for the
    parser to reject. No caller or ambient environment is changed here.
    """
    result = list(command)
    arguments = result[argument_offset:]
    if arguments[:2] == ["program", "approve"]:
        fields = PROGRAM_TOKEN_ENV_VARS
    elif arguments[:1] == ["checkpoint"]:
        fields = CHECKPOINT_TOKEN_ENV_VARS
    elif arguments[:1] == ["program"]:
        fields = {}
    else:
        fields = TOKEN_ENV_VARS
    flags = {"--" + attribute.replace("_", "-"): variable for attribute, variable in fields.items()}
    private: dict[str, str] = {}
    index = argument_offset
    while index < len(result):
        word = result[index]
        if word == "--":
            break
        flag, separator, value = word.partition("=")
        variable = flags.get(flag)
        if variable is None:
            if flag.startswith("--") and any(full.startswith(flag) for full in flags):
                raise ValueError(f"Use the full controller token option instead of {flag}")
            index += 1
            continue
        if not separator:
            if index + 1 >= len(result):
                break
            value = result[index + 1]
            if value.startswith("-") and value != "-":
                index += 1
                continue
        # A last explicit '-' continues to use the caller's environment, not a
        # literal value from an earlier occurrence of this same option.
        private.pop(variable, None)
        if value and value != "-":
            private[variable] = value
            if separator:
                result[index] = flag + "=-"
            else:
                result[index + 1] = "-"
        index += 1 if separator else 2
    return result, private
