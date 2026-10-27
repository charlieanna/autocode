# Context growth attribution (#2136)

Read-only audit against master `fe3f8139c3aab36844ed2aaecf9d617cd85842c8`,
2026-10-27. No model request, route change, stage omission or cache experiment
was launched for this investigation. The observations below are retained
attempts, not eleven new runs or a reliability sample.

## Evidence and scope

The original two `eeb64352f` bug-fix measurements and
`plans/cost-measurement-2026-10-25.md` remain unavailable. Their $0.39/$0.57
cost split and historical 5% first-request cache rate are unverified here.
These other retained cases do not replace those missing samples.

- W1–W4 and B1: #2113 self-fix, run `20261026-063353-chat-74a0ba`.
  W1/W2 are the GLM writer timeouts before/after #2154; W3/W4 are later
  Codex writer attempts; B1 is its Builder. This task had 25 due cases and
  must not be described as the tiny paging task. Public bundle manifests:
  `dc57dd387688d062c6c3d652ea8e2d0883b6a86b4ef1902fa453c56ae55447e1`
  and `050378a08f19c7595b068b519bc31e71b5964de4ba34f2cb7ae6b4d7b333cbe2`.
- R1/R2: standalone #2113 review cohorts, not implementation benchmarks.
  R2 is the explicitly typed final comparison, run
  `20261027-014508-chat-6e0144`, manifest
  `339cccb77494f96f072216139ce31931712236a4d4e3f1634f28ec96046b8ece`.
- W5/B2/T1/T2: paging writer, Builder, initial Tester and recovery Tester,
  run `20261027-071301-chat-aea64d`, final manifest
  `bb900aee93a1ca15f7e1d7a2e90c430e1dcb714063e22a5e338fa093a405365b`.
  Its initial stages used #2196's runtime; T2 used merged #2205. The
  retained task completed with independent checks. This is not a clean
  all-stages-on-one-runtime timing experiment.

## Measurement method

Read only the owned `.prompt.md`/native `.jsonl` artifacts and public status
accounting. Use the final update per native tool call/part, rather than
counting streaming updates as new calls. Reconcile every attempt's completed
native finish count and normalized input/cache/output totals against public
request accounting: all eleven agree. W1/W2 have an unfinished final request;
their totals cover completed requests only and are not complete bills.

Split the final local prompt at `CURRENT HANDOFF DATA`. Count UTF-8 bytes of
instructions, the largest embedded report-schema JSON object and the exact
handoff. Top-level JSON entry spans plus punctuation/whitespace sum to that
handoff; nested sections are not added to their parents again. Prefix framing
is included with instructions. This is byte attribution, not model-token
attribution: Kilo's own system/tool context and tokenizer are not fully visible
in the saved prompt. Do not divide bytes by four and claim an exact token split.

Provider input is fresh + cache-read + cache-write tokens; output includes
reasoning. Distinguish first-request input, peak single completed request and
cumulative input. For each request, separately count observed tool input/output
bytes whose completion precedes that request's recorded start. This is a
correlation/lower-bound observation, not proof of the backend's retained message
layout. Native visible text omits some reasoning/intermediate messages; its
absence does not establish zero generated text. Tool interval unions exclude
overlap; request intervals are not HTTP/inference timings.

## Request and tool growth

All use Kilo. G = `zai-coding-plan/glm-5.3`; C = `openai/gpt-6.1-sol`.
Effort below is the recorded launch effort. Cache is cumulative read tokens;
no selected attempt reports cache writes.

| Case | Model / effort | Requests | First input | Peak input | Cumulative input | Cached input | Fresh input | Observed tool output bytes | Tool union / stage seconds |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| W1 | G / low | 26 | 44,330 | 127,180 | 2,502,704 | 2,374,528 | 128,176 | 287,097 | 2.622 / 601.900 |
| W2 | G / low | 55 | 42,557 | 145,123 | 5,931,950 | 5,785,024 | 146,926 | 330,021 | 6.436 / 601.881 |
| W3 | C / medium | 35 | 39,968 | 111,920 | 2,807,160 | 2,700,416 | 106,744 | 249,781 | 26.466 / 562.703 |
| W4 | C / medium | 28 | 33,339 | 101,269 | 2,257,058 | 2,151,936 | 105,122 | 224,097 | 6.848 / 503.002 |
| B1 | C / medium | 19 | 43,304 | 88,919 | 1,277,382 | 1,191,680 | 85,702 | 159,247 | 60.933 / 328.077 |
| R1 | G / low | 15 | 15,510 | 41,904 | 414,586 | 374,912 | 39,674 | 85,353 | 20.823 / 267.473 |
| R2 | G / low | 14 | 15,777 | 44,344 | 486,242 | 444,224 | 42,018 | 86,301 | 4.609 / 282.253 |
| W5 | G / low | 4 | 17,685 | 20,325 | 75,533 | 55,104 | 20,429 | 5,897 | 0.344 / 66.019 |
| B2 | C / medium | 12 | 24,930 | 31,832 | 340,124 | 307,968 | 32,156 | 17,359 | 4.328 / 139.021 |
| T1 | G / low | 4 | 33,754 | 39,639 | 146,406 | 106,688 | 39,718 | 11,589 | 2.421 / 100.454 |
| T2 | G / low | 6 | 34,298 | 42,091 | 225,453 | 183,232 | 42,221 | 10,331 | 2.889 / 130.952 |

W1/W2 took about 602 seconds despite tool execution unions of 2.622/6.436
seconds. Their iterative request/exploration loop, not shell test duration,
dominates. Those intervals do not separate provider waiting, reasoning,
transport or orchestration. W2's smaller prompt did not resolve its timeout.
B2's cumulative 340,124 input tokens peak at only 31,832 in one request;
1.277 million cumulative input in B1 peaks at 88,919. Neither is evidence of
a million-token context window.

## Initial prompt attribution

The largest sections of T1 are the complete contract (22,930 bytes), derived
verification obligations (16,646), Builder report (10,680), runner regression
proof (7,659) and bounded source attachment (4,990). They are different
responsibilities; size alone does not authorize dropping them.

| Case | Instructions / transport bytes | Embedded report schema | Handoff bytes | Full local prompt |
| --- | ---: | ---: | ---: | ---: |
| W1 | 7,668 | 825 | 99,236 | 107,729 |
| W2 | 7,668 | 825 | 90,223 | 98,716 |
| W3 | 7,668 | 825 | 90,223 | 98,716 |
| W4 | 8,437 | 825 | 90,223 | 99,485 |
| B1 | 30,286 | 1,450 | 106,940 | 138,676 |
| R1 | 6,499 | 929 | 5,719 | 13,147 |
| R2 | 7,272 | 929 | 6,322 | 14,523 |
| W5 | 9,128 | 825 | 33,411 | 43,364 |
| B2 | 24,417 | 1,450 | 49,112 | 74,979 |
| T1 | 32,433 | 3,359 | 81,769 | 117,561 |
| T2 | 32,856 | 3,359 | 83,672 | 119,887 |

For T1, prior validation reports are only 30 bytes, so externalizing them is
not this sample's opportunity. W1's handoff is 99,236 bytes inside a 107,729-byte
full prompt: earlier comments reporting packet bytes did not count the full
prefix. #2154 reduces the handoff to 90,223 (full prompt 98,716), but W2 still
times out. Its largest single request is actually larger. No causal latency
or cache saving follows from that failed pair.

## Repetition and bounded candidates

1. **Preserve supplied compact JSON during containment (#2210).** Current
   containment re-indents compact execution handoffs. On T1/T2 this adds
   10,553/10,700 bytes, about 9% of the full local prompt, without adding any
   value. W1/W2 have 12,198/12,634 formatting-only bytes; B2 only 1,115.
   Preserve existing planning compactness and indented execution input.
   Negative controls: decoded type/value equality, escaped newline/Unicode,
   full boundary/private-read fields, current/stale proof instructions,
   output-schema relay and unchanged no-boundary behavior. This proves bytes,
   not faster inference. No raw source, receipts or requirements are removed.
2. **Extend exact same-packet references only where provenance agrees.**
   `verification_obligations.checks[].claim` repeats full criterion text from
   `goal_contract.body.acceptance_criteria`; current serializer references
   the criteria roster and replay commands, not these claims. B2/T1 contain
   8,147/8,858 repeated UTF-8 bytes in long identical string leaves overall;
   those are an upper bound, before pointer overhead, not promised savings.
   Bind a unique criterion ID and exact text; retain every obligation's kind,
   command, test, collection recipe, source and result. Refuse ambiguous IDs,
   changed text or proof. Round-trip the entire packet and preserve state.
3. **Reduce unnecessary exploratory turns using scoped evidence.** W1/W2
   expose 287,097/330,021 tool-output bytes; exact repeated output contributes
   zero/14 bytes. Repeated *reads* need not return identical chunks, so byte
   deduplication alone misses the loop. Attribute file/chunk identity and
   freshness before reusing anything. Keep uncovered discovery and fresh
   verification; wrong/stale source, changed dependencies and missing receipts
   must force new checks. No truncation or stage combination is proposed.
4. **Measure prefix stability before changing cache policy.** T1/T2 and W1/W2
   first requests have zero cached input; R1/R2 each report 2,688 cached input
   tokens on their first request. This does not identify whether AutoCode,
   backend instructions or another stable segment was cached. Dynamic paths,
   task/schema differences and timing confound attribution. A future matched
   experiment needs an explicit scope; none ran here. Do not promise a cache
   hit or infer a general hit rate from these cohorts.

The per-request numbers and observed message-byte progression are recorded in
the [#2136 audit appendix](https://github.com/charlieanna/autocode/issues/2136#issuecomment-6438619271). Full local native streams support this analysis; public
bundles retain only event tails, so their manifest hashes alone do not make
all native tool-output attribution independently replayable. The original
`eeb64352f` samples remain required before closing that evidence gap. No model,
stage-policy, approval, spend guarantee or reliability claim is changed here.
