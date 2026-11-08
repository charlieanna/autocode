# 2026-11-08: what a "hi" shows, and where its 45 seconds go

**Status:** transcript noise fixed (see the pull request that adds this note); latency items open.
**Found:** 2026-11-08 on the owner's Mac, master `90545405b`, Kilo backend, Requirements on
`zai-coding-plan/glm-5.3` at high effort, in a new empty folder outside Git. The host was heavily
loaded (load average about 160), so absolute times are high; the split is what matters.

## What the person saw

For one "hi" the full-screen chat showed ten blocks. Only the reply was meant for the person:

| Block | Needed for "hi"? | Now |
| --- | --- | --- |
| "This folder was not a Git repository, so I started one here and the work happens in it: PATH" | No: nothing is written until a plan is approved | Shown after the first approved plan, one sentence, no private-project path |
| "Working on your request…" | Marginal | Unchanged |
| "Requirements · thinking ▸ 1 thought folded" (twice) | Pointer to `/live` | One fold: with the notices gone the second fold replaces the first |
| "Recovery · Requirements answer rejected / $: missing required_behaviors" | No: machinery, settled by AutoCode | Side panel and `/why` only |
| "Hi! I'm AutoCode's requirements assistant…" | **Yes** | Unchanged |
| "What would you like me to help you build…?" | No: the open question repeating the reply | Open (below) |
| "Recovery · Requirements rewrote its report" | No | Side panel and `/why` only |
| Header "AutoCode chat  1a866272d49d · RUN" (populated folder) | No: a random id | Header names the person's folder |

## Latency of one "hi" turn (Enter to reply on screen, about 45 s)

| Step | Seconds |
| --- | ---: |
| Chat client starts the controller | 0.4 |
| Python/CLI startup, `git init` and baseline commit, `kilo --version` | 2.3 |
| Kilo checks, concurrent: `auth list`, `models --verbose` (618 KB), `debug config` | 4.2 |
| Controller setup to stage start (2.8 s of it `source_launcher_preparation`) | 3.5 |
| Kilo boot: process spawn to model request | 6.2 |
| GLM 5.3: 1.6 s to first token, 7.4 s generation (11.7k tokens) | 9.0 |
| Provider exit, stage finish | 0.9 |
| Report rejected (`$: missing required_behaviors`), report repair: setup 3.1, Kilo boot 7.8, model 4.9, finish 1.5 | 17.3 |
| Controller exit, status poll, render | about 1.5 |

The streamed "Hi!" text was complete at 22 s, 23 s before the chat showed it. A second
run in line mode needed no repair and answered in 43 s (slower Kilo boot and model under load).

## Open

1. GLM 5.3's first Requirements answer to a greeting can omit `required_behaviors`; the repair
   costs about 17 s (one of two runs here).
2. The chat shows a streamed Requirements reply only once the report is accepted.
3. Kilo boots for 6 to 8 s on every launch, the repair included.
4. `source_launcher_preparation` takes 2.8 s on every launch.
5. A reply goes through two controller invocations about 4.8 s apart.
6. The open question is shown under a reply that already asks it.
7. In an empty folder a "hi" still runs `git init` and commits a baseline in the person's folder.
8. `autocode chat --line` does not flush when stdout is a pipe.
