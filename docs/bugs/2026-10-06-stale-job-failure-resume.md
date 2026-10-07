# A stale job failure blocked resume after a later abandon (#567)

`--resume-paused` treated any saved `job_failure` as the current pause when
the status was `PAUSED_STAGE_ABANDONED`. Abandoning a Requirements attempt
after an earlier Investigator failure left that failure in place, so resume
printed the abandon reason and exited. The old token would have retried the
Investigator.

A job failure now gates resume only while the recovery record is still that
job. A later non-job abandon clears it. Plain `--resume-paused` continues
the stage that abandon named. The old token is refused instead of relaunching
the earlier job.
