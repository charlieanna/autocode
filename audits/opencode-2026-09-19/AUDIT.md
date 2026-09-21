# Autocode's OpenCode integration audit — 2026-09-19

**Result: five reproduced defects, including two high-priority issues. Fix the timeout and permission handling before resuming autonomous implementation.**

Scope: Autocode 0.5.3's OpenCode adapter, its runner lifecycle and recovery, configuration identity, model lookup, approval gates, and transport tests. Native checks used installed OpenCode 1.18.31. This is not an audit of the entire upstream OpenCode application.

The existing suite passed: **101 tests in 31.564 seconds**. Additional reproductions exposed the defects below. The first suite attempt was blocked by the outer sandbox's process-inspection restriction; the approved rerun passed without changing the guard. No application implementation, provider authentication, or global OpenCode configuration was changed. The AI Engineer Growth run remains at `AWAITING_GOAL_APPROVAL`.

All provider-facing audit tests used isolated temporary configuration. Metadata probes made no inference calls. The timeout probe used the real OpenCode CLI against a deterministic HTTP provider bound to `127.0.0.1`, with a placeholder key and synthetic input. No hosted model requests were made.

## 1. P1 — Commands survive the provider timeout and write after the workspace is unlocked

Location: [autocode.py:145](/Users/ankurkothari/Documents/workspace/autocode/tools/autocode.py:145), particularly the termination branch at line 155.

`run_role()` terminates only the direct OpenCode process. It neither contains nor waits for commands that OpenCode launches. The normal pause path then releases the workspace lock.

**Reproduction:** the real OpenCode 1.18.31 CLI was pointed at a local fake provider that requested `python3 audit_worker.py` through its native bash tool. The worker marked its start, waited 12 seconds, and wrote a second marker. Autocode's stage timeout was eight seconds. Autocode returned `PAUSED_PROVIDER_TIMEOUT` with OpenCode exit code `-15`, but the worker was still alive, later wrote the second marker, and the workspace lock was available for acquisition.

This means a paused run can keep changing the repository while the user or another run starts work. The current timeout unit test mocks a single process and misses the native child-process behavior.

**Correction:** contain or track owned command descendants, including detached process groups; terminate them and confirm they have stopped before releasing the workspace lock. Preserve the uncertain request without replay. Add a native process-tree regression test that checks for writes after timeout.

Evidence: [timeout result](/Users/ankurkothari/Documents/workspace/autocode/audits/opencode-2026-09-19/autocode-opencode-timeout-result.json), [native events](/Users/ankurkothari/Documents/workspace/autocode/audits/opencode-2026-09-19/timeout-native-events.jsonl), and [reproduction script](/Users/ankurkothari/Documents/workspace/autocode/audits/opencode-2026-09-19/autocode_opencode_timeout_probe.py).

## 2. P1 — Inline role restrictions are discarded during launch

Location: [autocode_opencode.py:80](/Users/ankurkothari/Documents/workspace/autocode/tools/autocode_opencode.py:80).

The shallow merge replaces the entire existing `agent.autocode_sol` definition in `OPENCODE_CONFIG_CONTENT`. Autocode adds its own task/edit restrictions but loses the inherited role's other restrictions.

**Reproduction:** configure the inline `autocode_sol` role with `bash: deny` and `webfetch: deny`. Native `opencode debug agent autocode_sol` reports both tools disabled before applying `launch()`. Running the same native metadata check with the adapter's generated environment reports both tools enabled. Top-level permission rules are preserved; this defect specifically concerns restrictions inside an existing matching role definition.

This contradicts the adapter's promise to preserve the user's restrictions. OpenCode's documented defaults are permissive, and agent permissions affect effective access; losing a deny can therefore enable a tool. See the [official permissions documentation](https://opencode.ai/docs/permissions/).

**Correction:** merge the inherited role's permission policy with Autocode's additional denies without broadening any inherited restriction. Cover scalar and pattern-based policies in tests, using native resolved-permission checks.

Evidence: `inline_permission_override` in [probe results](/Users/ankurkothari/Documents/workspace/autocode/audits/opencode-2026-09-19/autocode-opencode-audit-results.json).

## 3. P2 — Changes within a custom configuration directory bypass drift detection

Location: [autocode_opencode.py:33](/Users/ankurkothari/Documents/workspace/autocode/tools/autocode_opencode.py:33), especially the environment hashes at lines 43–44.

`local_settings()` hashes the `OPENCODE_CONFIG_DIR` path string, but not the configuration files inside that directory. Other native configuration sources, such as the legacy global `config.json` and agent definition files, are also outside its enumerated file list. OpenCode supports layered configuration and custom directories; see the [official configuration documentation](https://opencode.ai/docs/config/).

**Reproduction:** keep `OPENCODE_CONFIG_DIR` fixed and change that directory's `opencode.json` from `permission.bash: deny` to `allow`. Native OpenCode changes the effective bash permission, while Autocode's complete transport identity remains identical.

A resumed run can therefore use a different effective policy without the promised `PAUSED_TRANSPORT_CHANGED` checkpoint.

**Correction:** fingerprint the effective nonsecret configuration inputs actually consumed by the supported OpenCode version, including custom-directory contents and agent definitions, while keeping credentials out of saved state and output. Add a regression that changes a file while retaining the same directory path.

Evidence: `custom_config_drift` in [probe results](/Users/ankurkothari/Documents/workspace/autocode/audits/opencode-2026-09-19/autocode-opencode-audit-results.json).

## 4. P2 — Completed malformed reports cannot recover through the normal CLI

Location: [autocode.py:390](/Users/ankurkothari/Documents/workspace/autocode/tools/autocode.py:390).

Recovery parses the final report and validates its schema before entering the exception handler that archives rejected completed stages. A completed response containing prose, or valid JSON missing a required field, raises before archival and leaves `active_stage` in place. Recovery runs before processing normal resume and user-input actions.

**Reproduction:** supply native-format events with a successful terminal `stop` and exit code zero. Test both a prose final message and a JSON object missing required fields. Two consecutive recovery attempts produce the same error; both leave the active stage intact and archive zero attempts.

The initial rejection is correct, but `--resume-paused` cannot make progress. The operator must abandon the run or edit its checkpoint manually, despite the provider request already being known to have completed.

**Correction:** route completed parsing/schema failures through the rejected-stage archival path, preserving the raw response and workspace changes. Keep retries explicit; uncertain or incomplete requests must remain in the separate reconciliation path.

Evidence: `malformed_report_recovery` and `wrong_schema_recovery` in [probe results](/Users/ankurkothari/Documents/workspace/autocode/audits/opencode-2026-09-19/autocode-opencode-audit-results.json).

## 5. P2 — Model preflight uses the caller's directory rather than the target workspace

Location: [autocode_opencode.py:49](/Users/ankurkothari/Documents/workspace/autocode/tools/autocode_opencode.py:49); caller at [autocode.py:822](/Users/ankurkothari/Documents/workspace/autocode/tools/autocode.py:822).

`check_models()` invokes `opencode models` without a target directory. Actual agent execution uses `--dir workspace`. When Autocode is called from another directory using `--workspace`, these two commands can load different project configurations.

**Reproduction:** define a harmless custom model in the target repository's `opencode.json`. Native `opencode models` run inside that repository lists the model. Calling the adapter's model check from its parent directory rejects the same model as unavailable. No provider call is needed to reproduce this.

This breaks valid project-specific providers when using Autocode from another directory and can also allow a preflight to check the wrong project's configuration.

**Correction:** pass the target workspace into model preflight and use the same working directory and relevant configuration environment as the subsequent agent launch.

Evidence: `project_model_context` in [probe results](/Users/ankurkothari/Documents/workspace/autocode/audits/opencode-2026-09-19/autocode-opencode-audit-results.json).

## Passing checks and limits

The existing suite covers explicit goal approval, separate role sessions, contract propagation, command-evidence matching, stale evidence rejection, complete-stage recovery, and completion gates. These checks passed. The adapter's cache and reasoning-token aggregation matches the accounting in the [OpenCode 1.18.31 source](https://github.com/anomalyco/opencode/blob/v1.18.31/packages/opencode/src/session/session.ts#L321).

OpenCode's permissions are not an OS sandbox. Autocode already documents that distinction; this audit does not count that disclosed architectural limitation as an additional defect. The two P1 findings are concrete failures beyond that disclosure: restrictions are removed, and a timed-out run continues writing.

No full hosted-model product build was exercised during this audit. A passing fake-provider workflow cannot establish real-model review quality or product usefulness. Implementation of the five corrections remains outstanding.

Source hashes and baseline command are recorded in [manifest.json](/Users/ankurkothari/Documents/workspace/autocode/audits/opencode-2026-09-19/manifest.json). The metadata/recovery probes can be rerun with [autocode_opencode_audit_probes.py](/Users/ankurkothari/Documents/workspace/autocode/audits/opencode-2026-09-19/autocode_opencode_audit_probes.py). The timeout probe needs permission to bind a local loopback port and launches a short-lived worker in a temporary Git repository.
