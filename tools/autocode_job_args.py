"""Argument combinations for exact stopped-job actions; no runtime or launch behavior."""


def validate(args, parser, explicit):
    # The token also binds a stopped job's model answer to the stop a person inspected (#463).
    names_job_model = (
        bool(args.answer)
        and all(item.startswith("route-") for item in args.answer)
        and not (args.resume_paused or args.retry_failed_stage)
    )
    args._job_cap_change = bool(
        args.job_retry_token
        and (args.max_seconds is not None or args.max_cost_usd is not None)
        and not (args.resume_paused or args.retry_failed_stage)
    )
    if args._job_cap_change and (
        args.task
        or explicit - {"run_dir", "workspace", "unit", "chat", "job_retry_token", "max_seconds", "max_cost_usd"}
    ):
        parser.error(
            "A stopped job's total-cap change takes only its token and total time/cost flags; retry separately"
        )
    if args.job_retry_token and not args.run_dir:
        parser.error("--job-retry-token requires --run-dir --resume-paused --retry-failed-stage")
    if (
        args.job_retry_token
        and not names_job_model
        and not args._job_cap_change
        and not (args.retry_failed_stage and args.resume_paused)
    ):
        parser.error(
            "--job-retry-token requires --resume-paused --retry-failed-stage, "
            "or --answer route-ROLE=MODEL alone to name a stopped job's model, "
            "or only --max-seconds/--max-cost-usd to save total caps without retrying"
        )
    # The answer issues a new token, so it never retries in the same invocation; refusing that
    # form here also keeps any other setting given with it from being saved (#463 review).
    if (
        args.job_retry_token
        and args.resume_paused
        and args.retry_failed_stage
        and any(item.partition("=")[0].startswith("route-") for item in [*args.answer, *args.delegate])
    ):
        parser.error(
            "--answer route-ROLE=MODEL --job-retry-token TOKEN names a stopped job's model on its own "
            "and issues a new token; then retry with --resume-paused --retry-failed-stage "
            "--job-retry-token NEW_TOKEN"
        )
