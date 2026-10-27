# Live verification follow-ups for multi-repository chat

The requested independent KiloCode reviews used MiMo v2.6 Pro
(`xiaomi-token-plan-sgp/mimo-v2.6-pro`) and GLM-5.3
(`zai-coding-plan/glm-5.3`), both at high effort. They ran AutoCode's read-only
review workflow in separate checkouts; no Builder was launched. Kilo 7.8.12 ran
through the existing macOS portable supervision/policy path. This is live code
review, not live-model repository-discovery or real-account PR qualification.

The first full-diff attempts reviewed `cd0f3709b` against `0b7c5a73b` and each
hit its 1,200-second stage limit before producing a validated final verdict.
They are retained failures, not passing reviews. The live GLM pass nevertheless
executed a failing public-view probe for the unsupported durable coordination
offer. Redacted bundle manifest hashes:

- MiMo: `f6f9bd21092b6cbf6fc1d0dd1de2f84c81b85796f5745624a16e9e1e8f99098a`
- GLM: `947ed5661f2c36dacac5b2a840e2f9faa5bc7c064b592fd00659c7fff953eeaa`

Earlier startup attempts first refused an unavailable default GLM route, then
paused on unused default cross-family assignments. The latter performed default
OpenAI model preflights but did not reach a Reviewer. Subsequent invocations pin
every route to the requested MiMo/GLM families. The refreshed master additionally
probes only the Reviewer for an explicit review. All attempts are retained under
the session's private temporary verification directories.

## Direct corrections

- [#2172](https://github.com/charlieanna/autocode/issues/2172): an existing
  reserved-worktree symlink could resolve to the original checkout and pass the
  common-directory/base checks. Direct real-Git probes also reproduced redirected
  parents and a primary-Git-directory alias. Preparation, member status/dispatch
  and publication now require a literal linked worktree with its own matching Git
  registration. Legitimate reuse preserves pending edits and the original source.
- [#2174](https://github.com/charlieanna/autocode/issues/2174): a durable bug chat
  advertised coordination that initialization refused. Only owned execution now
  advertises that action; the core refusal and the original scope remain intact.
- [#2173](https://github.com/charlieanna/autocode/issues/2173): GLM's wider checks
  exposed an unchanged pre-feature test expecting Linux policy admission on macOS.
  Its separate small PR retains the strict Linux test and uses the existing
  portable-policy coverage on macOS. Runtime policy is unchanged.

The new isolation regressions fail on the old code; 69 focused checks pass after
the direct fix. The supported Linux symptom-to-three-PR flow passes in 344.914 s.
After refreshing master to `0ae69d521`, 120 focused checks pass in 3.324 s, and the
joint baseline/candidate proof plus retained-record reread passes in 8.553 s.
The strict Linux Kilo refusal still executes and passes (0.827 s); all 11 general
and portable-policy tests pass. Changed-file lint/format and focused typing pass.

The final live reviews and CI results are recorded in the follow-up pull requests
linked from these issues. Their source revisions, reached Reviewer routes,
durations, verdicts and bundle identities distinguish them from the timed-out
attempts above. No model was used to repair the application source.
