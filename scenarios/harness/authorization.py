"""Black-box client for docs/cli.md's private authorization wire contract.

No runtime imports: the CLI validates schema, command binding and authority.
"""

import json
import tempfile
from contextlib import contextmanager

FLAG = "--authorization-stdin"
OPTIONS = {
    "--approve-goal": "approve_goal",
    "--review-token": "review_token",
    "--resolver-token": "resolver_token",
    "--job-retry-token": "job_retry_token",
    "--recover-job-report": "recover_job_report",
    "--expected-goal-token": "expected_goal_token",
    "--expected-recovery-token": "expected_recovery_token",
    "--expected-token": "expected_token",
    "--token": "token",
}


def check_command_prefix(command):
    for argument in command:
        option = argument.partition("=")[0]
        if option != "--" and option.startswith("--") and any(flag.startswith(option) for flag in (*OPTIONS, FLAG)):
            raise ValueError("Supply authorization through actions, not the command prefix")


def prepare(command):
    safe, tokens = list(command), {}
    end = safe.index("--") if "--" in safe else len(safe)
    index = 0
    while index < end:
        option, equal, value = safe[index].partition("=")
        if option == FLAG or (
            option.startswith("--")
            and any(flag.startswith(option) for flag in (*OPTIONS, FLAG))
            and option not in OPTIONS
        ):
            raise ValueError("Supply full token options to the private authorization client")
        if option in OPTIONS:
            if not equal:
                index += 1
                if index == end or safe[index].startswith("--"):
                    raise ValueError("Missing authorization input")
                value = safe[index]
            field = OPTIONS[option]
            if field in tokens or value == "@stdin":
                raise ValueError("Duplicate or unavailable authorization input")
            tokens[field] = value
            safe[index] = option + "=@stdin" if equal else "@stdin"
        index += 1
    if not tokens:
        return safe, None
    data = json.dumps({"schema": 1, "tokens": tokens}).encode("utf-8")
    if len(data) > 65536:
        raise ValueError("Authorization envelope exceeds the wire limit")
    safe.insert(end, FLAG)
    return safe, data


@contextmanager
def input_stream(data):
    if data is None:
        yield None
        return
    with tempfile.TemporaryFile() as stream:
        stream.write(data)
        stream.seek(0)
        yield stream
