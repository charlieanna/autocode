# Java scenario dependency rate limits (#4090)

The Ubuntu suite for #3981 failed before executing its tests on `97952cbb5`
and earlier on `4ff862264`. Both ran the same pinned Java setup and Maven
Central returned HTTP 429 while resolving the resources plugin. The current
companion scenario job got through the same prerequisite. The failed exact
job is https://github.com/charlieanna/autocode/actions/runs/44982724333/job/137086871499.

The shared warmup helper retries an observed Maven artifact-transfer HTTP 429
up to two times with waits of 5 and 20 seconds, within the existing 300-second
setup deadline. The retry forces a fresh Maven check for missing releases and
runs the original PageCountTest. All attempts are recorded in the setup log.
Ordinary build and test failures keep their existing fail behavior.

The two controls for successful recovery and repeated HTTP 429 fail on master
`b06e3d4268`. Forty-seven Java/CI scope tests pass with the candidate, plus
Ruff/format and mypy (1,000 files, including the changed helper). The actual
pinned Maven 3.9.9 and Temurin 17 ran PageCountTest successfully using a private
copy of the warmed repository. A second offline run passed the same named
test. The HTTP 429 assertions use controlled subprocess outcomes. Hosted CI
is pending, including validation with a fresh GitHub runner.
