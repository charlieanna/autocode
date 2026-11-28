# Shadow route suggestions on 208 saved runs (2026-11-28)

Step 3 of restarting automatic model choice from measured outcomes (#4251, #2049). The shadow
suggestion (`autocode_route_suggestion`) was replayed over saved runs, oldest first, with each run
suggested from the runs before it only (`tools/autocode_route_backtest.py`). No model was called.

**Input.** 208 `autocode bundle` archives of live Claude Code runs from 13 to 16 November 2026
(sha256 of their sorted `manifest.json` digests begins `0e9460cb298975a2`). 149 counted (live and
completed); 58 had not completed and 1 declared no provenance.

**Pass bar, fixed before the replay.** Every check is zero:

| check | result |
| --- | ---: |
| suggestions that break checker independence | 0 |
| suggestions that could make the producer stronger than its checker | 0 |
| suggestions over a stage the person chose | 0 |
| suggestions on fewer than 8 counted attempts | 0 |
| suggestions whose route did worse over all runs, at the 95% range | 0 |

**What it shows, and what it cannot.**

- Every saved run named every stage's model (`route_origins` is `user_explicit` for all 7 routes
  in all 208), so as saved every stage was kept for the person and nothing was suggested.
- Replayed as if no stage had been chosen, the Builder and the Planner still got no suggestion:
  every run used the same route per stage (Sonnet 5.5 plans, Haiku 4.5 builds, Opus 5.5 reviews,
  all at medium), so there was never a second route to compare. 197 Builder and 199 Planner
  decisions were "no better route"; the earliest 11 and 9 had too little evidence.
- So the replay shows the rules hold on real history. It cannot show that a suggestion would pick
  better, because this history holds no alternative. The pass-bar checks themselves are shown to
  catch each kind of broken suggestion by `tests.test_route_backtest`, on synthetic histories that
  do vary.

**What would change this.** Counted runs on a second route for the same job and backend: a stage
choice changed for a while, or the probes of #3630. Shadow records now land in each ledger row
(`route_suggestion`), so the next backtest can read them without the bundles.

Reproduce: unpack the archives into one folder and run
`python3 tools/autocode_route_backtest.py FOLDER` (exit 0 when the bar passes; `--json` for the
full report).
