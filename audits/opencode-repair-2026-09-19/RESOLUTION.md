# Autocode 0.5.4 repair verification — 2026-09-19

All five findings from the [OpenCode audit](../opencode-2026-09-19/AUDIT.md) are fixed.
The installed OpenCode binary remains 1.18.31; these repairs are in Autocode's adapter
and runner. The global editable `autocode` installation was refreshed to 0.5.4.

| Audited defect | Repair | Observed verification |
| --- | --- | --- |
| Detached OpenCode tools survived timeout and wrote after unlock | Track process ancestry, groups and birth identities; clean up before leaving a stage; persist worker ownership for later runs | Real OpenCode called a loopback fixture provider and launched a detached writer. After timeout the writer was dead, no late write occurred, and the workspace lock was reusable. |
| Inline role configuration lost existing permission denies | Preserve existing role fields and permission patterns; add Autocode restrictions | Native `debug agent` kept both `bash` and `webfetch` disabled before and after launch preparation. |
| Custom config-directory contents were absent from the transport identity | Hash directory configuration and agent/plugin/tool definitions, with a versioned legacy-checkpoint upgrade | Changing custom config from deny to allow changed the identity and native effective permissions. Agent-file drift also has a regression test. |
| Malformed completed reports became permanently active | Validate raw events, archive rejected attempts, and save a durable pause requiring explicit resume | Invalid JSON and incomplete schemas each archived exactly once; subsequent reconciliation returned without replay. A failed archive checkpoint retained original evidence for recovery. |
| Model lookup used the caller's directory | Execute model preflight in the target workspace | Native OpenCode accepted a project-only provider/model while Autocode's caller was in another directory. |

Additional runner repairs:

- Finished, rejected and recovered attempts count toward active time exactly once.
- Archive records point to the actual saved artifacts. Original artifacts are removed
  only after the new checkpoint is saved, and the pause is persisted before returning.
- Resumed responses must return the expected session ID. Uncertain attempts can be
  explicitly set aside with `--abandon-stage ATTEMPT_ID`; this preserves edits and
  logs, clears the affected session, and sends partial-work context back to Astra.
- Completed runs recheck source and evidence before reporting success. Changes pause
  for fresh Sol validation. Read-only status exposes `completion_current`.
- Source snapshots include executable modes and dirty submodule content.
- Completion acceptance is an exclusive action requiring an existing run. Fresh
  dry runs correctly show OpenCode as the default engine.
- Normal provider exits and interruptions clean up tracked background workers too.

## Executed checks

- Full source suite: **120 tests passed in 73.570 seconds**.
  `python3 -m unittest tools/test_autocode.py tools/test_goals.py tools/test_subprocess.py tools/test_opencode.py tools/test_process.py`
- Offline wheel build and isolated installation: passed. All four runner/adapter/process
  source modules in the wheel were compared byte-for-byte with the checkout.
- Installed-wheel CLI workflows: **3 tests passed in 13.725 seconds**, covering
  approval, implementation, independent validation, rework and explicit stage recovery.
- Refreshed global `autocode` command: **1 complete OpenCode fixture workflow passed
  in 3.752 seconds**, invoked from an unrelated temporary Git project.
- Python compilation, global CLI help and changed-source whitespace checks: passed.
- Native permission, config and workspace-model results: [native-results.json](native-results.json).
- Native timeout and detached-worker results: [native-timeout-result.json](native-timeout-result.json)
  and [raw events](native-timeout-events.jsonl).
- Final source and artifact hashes: [manifest.json](manifest.json). Patches against
  the pre-repair checkout are saved alongside this report.

Tests required local process inspection and used isolated temporary repositories.
Native checks used isolated OpenCode configuration and a deterministic loopback
provider; **no hosted model requests were made for these repairs**. User provider
credentials and global OpenCode configuration were not changed. Existing application
run checkpoints and goal approvals were not modified.

Process supervision is for trusted local tools. It is not an OS sandbox and cannot
guarantee containment of deliberately hidden daemons or an abruptly killed supervisor.
Known live workers block a new run. These tests verify runner behavior and the audited
native integrations; they do not evaluate real-model implementation or review quality.
