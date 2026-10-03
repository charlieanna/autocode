# Issue #214: exact output and measured live comparison

The old `compact_output` deduplicated arbitrary repeated lines. In a real
unittest run with 160 passing tests and two failures, the displayed result
contained only one traceback header and one assertion message. Both existed
in the retained original. GLM-5.3, MiMo-V2.6-Pro and GPT-6 Sol reproduced this
against frozen master `05095155` and recovered the original successfully.

The fix filters only recognized completed unittest output, preserves repeated
diagnostics and original line order, and retains exact content-addressed bytes
before omission. Capture still executes fresh commands and preserves exit
status and complete proof receipts. Exact file sections and explicit unchanged
identities share that retrieval path. Invalid storage falls back to full output.
Request context and display measurements are separate from cumulative usage.
See [usage and recovery](../exact-output.md).

## Live qualification

OpenCode 1.18.33, existing provider logins, medium reasoning, fresh projects and
sessions, 240-second per-turn ceiling, no retries. Routes:
`zai-coding-plan/glm-5.3`, `xiaomi-token-plan-sgp/mimo-v2.6-pro`,
`openai/gpt-6-sol`. Each pair used equivalent fixtures and instructions: one
162-test execution, two reads of a 300-line file, and an exact retrieval of
line 235. Additional model inspection calls are included in usage and time.
MiMo ran conservative first; the other two ran raw first.

All six post-fix probes passed execution, exit status, original hash, both
visible tracebacks, exact line retrieval and unchanged-source checks. GLM raw
reported the two fully qualified fixture test names: the scorer was corrected
to accept those exact equivalent identities, without relaxing other checks.
The original scoring output remains saved. An initial threaded harness stopped
before any model launch because the transport requires main-thread signal
handling; the successful campaign ran sequentially.

| Model | Mode | Input including cache | Cached input | Output including reasoning | Peak request input | Requests | Seconds | API-equivalent USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GLM-5.3 | raw | 153,795 | 132,736 | 1,555 | 33,230 | 6 | 64.7 | 0.07084 |
| GLM-5.3 | conservative | 203,847 | 188,928 | 3,210 | 27,141 | 9 | 147.9 | 0.08413 |
| MiMo-V2.6-Pro | raw | 180,078 | 155,520 | 1,947 | 36,618 | 6 | 108.4 | 0.01294 |
| MiMo-V2.6-Pro | conservative | 160,984 | 145,536 | 2,588 | 26,544 | 7 | 79.6 | 0.00950 |
| GPT-6 Sol | raw | 147,922 | 115,072 | 773 | 32,139 | 6 | 71.7 | 0.09644 |
| GPT-6 Sol | conservative | 120,811 | 96,256 | 918 | 23,723 | 6 | 93.4 | 0.07754 |

API-equivalent estimates use `scenarios/api-pricing-output-2026-10-03.json` and the
existing request-level repricer, with [Z.AI's rates](https://docs.z.ai/guides/overview/pricing)
and [GPT-6 Sol's rates](https://developers.openai.com/api/docs/models/gpt-6-sol),
plus [MiMo's published USD API rates](https://mimo.mi.com/models/en-US/mimo-v2.6-pro).
Cache writes were zero. Initial scoring left MiMo cost unavailable until those
rates were verified and the saved requests repriced. No subscription charge is inferred.

Displayed AutoCode tool bytes fell from about 49,328 to 16,454, including
metadata and the required retrieval. This did **not** ensure net savings:
GLM's extra directory inspection, log counting and byte checking increased its
estimate by $0.01330 (18.8%); Sol's fell by $0.01890 (19.6%) while latency rose.
MiMo's estimate fell by $0.00344 (26.6%).
Native inspection output is outside the display-byte counter but included in
provider tokens and cost. These are one-pair observations, not a statistically
reliable savings rate or proof of full-project mixed-model convergence.

Artifacts are local and ignored under `.scenario-runs/issue-214/`: baseline
source/probes, RTK evaluation, `paired-v2` frozen source, prompts, schemas,
launch identity, raw and normalized events, receipts, operation records, and
both original and corrected scores. No logs or evidence bundles are committed.
Post-probe changes add interrupted-request accounting, reject a `.log` receipt
name collision, and correct explicit-range byte accounting; their focused
regressions pass. The exercised successful capture/retrieval path is unchanged.

## Follow-up verification

Three conservative probes on feature commit `7961c7fd` passed (GLM, MiMo,
OpenAI). After integrating master, GLM and OpenAI passed again; MiMo completed
the capture, diagnostic, hash, and retrieval checks but reached the 240-second
turn limit without a final report. That run remains a failure, with no automatic
retry. Earlier MiMo passes do not establish uniform completion reliability.

Review then found that a Unicode line separator inside a message shifted the
filter's omission ranges relative to the exact byte reader. Both now use byte
line boundaries. A regression checks the referenced bytes, and a fresh OpenAI
live probe retrieved the first omitted test line after an actual Unicode
separator; its exact report, execution, hash, and diagnostic checks all passed.
The baseline, paired and follow-up probes are focused transport checks, not
complete software-project campaigns.

The merged feature passed master's full CI suite at `441fbc65`. Local parallel
runs encountered failures that remain recorded separately; the diagnosis
module (39 tests) and truncated-review module (5 tests) passed isolated reruns.
The post-integration fake catalog finished with 53 PASS, one existing
NOT_EXERCISED case and one intentional live-Investigator SKIPPED case.
Follow-up artifacts are ignored under `.scenario-runs/issue-214-integration/`,
including the timed-out MiMo run and `unicode-v4` exact-retrieval probe.
