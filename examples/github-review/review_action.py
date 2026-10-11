"""Trusted pull_request handler: installed tooling, a new output, and no posting."""

import json
import os
import subprocess
from pathlib import Path


def main(env=None, run=subprocess.run):
    env = dict(os.environ if env is None else env)
    cli = Path(env["AUTOCODE_REVIEW_CLI"])
    if not cli.is_absolute() or not cli.is_file():
        raise ValueError("AUTOCODE_REVIEW_CLI must name the trusted installed absolute CLI")
    options = json.loads(env.get("AUTOCODE_REVIEW_OPTIONS", "[]"))
    if not isinstance(options, list) or any(not isinstance(value, str) for value in options):
        raise ValueError("AUTOCODE_REVIEW_OPTIONS must be a JSON array of strings")
    # These are creation options only; the CLI rejects actions/workspace/workflow
    # overrides and the handler never takes --post from event data.
    return run(
        [str(cli), "review-pr", env["AUTOCODE_PR_URL"], "--out", env["AUTOCODE_REVIEW_OUT"], "--json", "--", *options],
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
