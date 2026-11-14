import assert from "node:assert/strict";
import test from "node:test";

import {
  approvalToken,
  needsSummary,
  pauseLabel,
  planForApproval,
  progressLine,
  readRegistryList,
  readStatusPayload,
  RunStarting,
  usageLine,
  readChangesetSummary,
} from "../src/core/view";

const envelope = (view: unknown) => JSON.stringify({ ok: true, view });

test("readStatusPayload takes the view out of the --status envelope", () => {
  const view = readStatusPayload(envelope({ status: "RUNNING", done: false }));
  assert.equal(view.status, "RUNNING");
  assert.equal(view.done, false);
});

test("readStatusPayload refuses output that is not a status view", () => {
  assert.throws(() => readStatusPayload("not json"), /did not return JSON/);
  assert.throws(() => readStatusPayload(JSON.stringify({ status: "RUNNING" })), /did not return a status view/);
});

test("readStatusPayload says what a run still starting is doing, and refuses a start that stopped reporting", () => {
  const starting = (owner_alive: boolean) =>
    JSON.stringify({ version: 1, run_dir: "/ws/.autocode/runs/r", workspace: "/ws",
      starting: { phase: "preparing_run", label: "preparing the run", at: "t", started_at: "t", owner_alive } });
  assert.throws(() => readStatusPayload(starting(true)), (error) => error instanceof RunStarting && error.label === "preparing the run");
  assert.throws(() => readStatusPayload(starting(false)), (error) =>
    !(error instanceof RunStarting) && /start stopped while preparing the run/.test(String(error)));
});

test("a field the client expects to exist may still be missing", () => {
  // The view's contract is that fields may be added; an older run may not have them yet.
  const view = readStatusPayload(envelope({ status: "PAUSED_REGISTRY", done: false }));
  assert.equal("progress" in view, false);
  assert.equal(progressLine(view), "PAUSED_REGISTRY");
  assert.equal(usageLine(view), null);
});

test("progressLine falls back to the headline, then the status code", () => {
  assert.equal(progressLine({ status: "S", done: false, progress: { line: "one line" } }), "one line");
  assert.equal(progressLine({ status: "S", done: false, progress: { headline: "Builder working" } }), "Builder working");
  assert.equal(progressLine({ status: "S", done: false }), "S");
});

test("pauseLabel only describes a stopped run, not an approval checkpoint", () => {
  const approval = {
    status: "AWAITING_GOAL_APPROVAL",
    done: false,
    pause_category: "your_decision",
    pause_category_label: "Your decision",
  };
  assert.equal(pauseLabel(approval), null);
  assert.equal(pauseLabel({ ...approval, status: "WAITING_FOR_USER" }), null);
  assert.equal(
    pauseLabel({ ...approval, status: "PAUSED_TRANSPORT_CHANGED", pause_category_label: "Your environment" }),
    "Your environment",
  );
});

test("needsSummary says what the run waits for, in the view's own words where it has them", () => {
  assert.equal(
    needsSummary({ status: "S", done: false, progress: { needs_you: "plan approval needed" }, needs: { kind: "approve_plan" } }),
    "plan approval needed",
  );
  assert.equal(needsSummary({ status: "S", done: false, needs: { kind: "approve_plan" } }), "approval of the plan AutoCode displayed");
  assert.equal(
    needsSummary({ status: "S", done: false, needs: { kind: "answer", questions: [{ id: "q", question: "x" }] } }),
    "1 question(s)",
  );
  assert.match(needsSummary({ status: "S", done: false, needs: { kind: "resume", reason: "budget reached" } }), /budget reached/);
  assert.equal(needsSummary({ status: "S", done: false, needs: { kind: "continue" } }), "nothing; the run can simply proceed");
  assert.equal(
    needsSummary({ status: "RUNNING", done: false, needs: { kind: "continue" }, liveness: { kind: "supervised" } }),
    "a stage is currently running",
  );
  assert.equal(
    needsSummary({ status: "RUNNING", done: false, needs: { kind: "continue" }, liveness: { kind: "unsupervised" } }),
    "a stage is currently running",
  );
  assert.equal(
    needsSummary({ status: "RUNNING", done: false, needs: { kind: "continue" }, liveness: { kind: "unknown" } }),
    "nothing; the run can simply proceed",
  );
  assert.equal(
    needsSummary({ status: "RUNNING", done: false, needs: { kind: "continue" } }, true),
    "a stage is currently running",
  );
  assert.equal(
    needsSummary({ status: "RUNNING", done: false, needs: { kind: "continue" } }, false),
    "nothing; the run can simply proceed",
  );
});

test("unknown cost is not zero", () => {
  assert.equal(usageLine({ status: "S", done: false, usage: { tokens: 1200, cost_usd: {} } }), "1,200 tokens");
  const partial = usageLine({
    status: "S",
    done: false,
    usage: { tokens: 100, cost_usd: { reported: 0.5, complete: false } },
  });
  assert.match(partial ?? "", /\$0\.5000 \(partial\) reported/);
  const unknown = usageLine({ status: "S", done: false, usage: { unknown_stages: 3 } });
  assert.equal(unknown, "3 stage(s) unknown");
});

test("readRegistryList keeps a stale pointer honest instead of dropping it", () => {
  const list = readRegistryList(
    JSON.stringify({
      storage_root: "/Users/me/.autocode",
      diagnostics: ["registry_absent"],
      runs: [
        {
          id: "run-2",
          workspace: "/gone",
          run_dir: "/gone/.autocode/runs/b",
          availability: "workspace_missing",
          diagnostic: { message: "Workspace no longer exists" },
        },
      ],
    }),
  );
  assert.equal(list.storageRoot, "/Users/me/.autocode");
  assert.deepEqual(list.diagnostics, ["registry_absent"]);
  assert.equal(list.runs[0]!.availability, "workspace_missing");
  assert.equal(list.runs[0]!.diagnostic, "Workspace no longer exists");
  assert.equal(list.runs[0]!.status, null);
});

test("readRegistryList reads the summary where the CLI actually puts it", () => {
  // Captured from `autocode registry list --json` on master: an available record nests its
  // current summary under `diagnostic`, and only a stale record puts a message there.
  const list = readRegistryList(
    JSON.stringify({
      runs: [
        {
          availability: "available",
          diagnostic: { checkpoint_version: 3, next_stage: "terra", phase: "PAUSED_OR_BLOCKED", status: "PAUSED_BUDGET" },
          id: "run-191945ad7f7d2b8f991a7941",
          run_dir: "/ws/.autocode/runs/20260923-163453-implement-a-policy-42634123",
          task_id: "6a151521-4e76-4846-b3bc-f6d45faa921a",
          workspace: "/ws",
          workspace_id: "workspace-a54f7a702e71db8412393217",
        },
      ],
    }),
  );
  assert.equal(list.runs[0]!.status, "PAUSED_BUDGET");
  assert.equal(list.runs[0]!.phase, "PAUSED_OR_BLOCKED");
  assert.equal(list.runs[0]!.nextStage, "terra");
  assert.equal(list.runs[0]!.diagnostic, null);
  assert.equal(list.runs[0]!.title, null);
  assert.equal(list.runs[0]!.updatedAt, null);
});

test("readRegistryList reads each chat's title and last activity from its summary", () => {
  const list = readRegistryList(
    JSON.stringify({
      runs: [
        {
          availability: "available",
          diagnostic: { status: "COMPLETE", title: "Fix the login page", updated_at: "2026-11-11T00:06:21Z" },
          id: "run-1",
          run_dir: "/ws/.autocode/runs/20261111-000621-vscode-abc123",
          workspace: "/ws",
        },
      ],
    }),
  );
  assert.equal(list.runs[0]!.title, "Fix the login page");
  assert.equal(list.runs[0]!.updatedAt, "2026-11-11T00:06:21Z");
});

test("an empty or odd registry payload still reads as no runs", () => {
  assert.deepEqual(readRegistryList(JSON.stringify({ runs: [] })).runs, []);
  assert.deepEqual(readRegistryList(JSON.stringify({})).runs, []);
});

test("an approval is bound to the token the run asks for", () => {
  assert.equal(approvalToken({ status: "S", done: false, needs: { kind: "approve_plan", token: "r20:abc" } }), "r20:abc");
  assert.equal(approvalToken({ status: "S", done: false, needs: { kind: "review", token: "r1:xyz" } }), "r1:xyz");
  assert.equal(approvalToken({ status: "S", done: false, needs: { kind: "resume", reason: "x" } }), null);
  assert.equal(approvalToken({ status: "S", done: true }), null);
});

test("a revised plan is not the plan the token approves", () => {
  const shown = {
    revision: 20, hash: "abc", token: "r20:abc", review_markdown: "A complete canonical plan",
    acceptance_criteria: [{ id: "AC1", criterion: "one", verification_method: "run the test", human_review: false }],
    constraints: [], permission_boundaries: [],
  };
  const current = { status: "S", done: false, needs: { kind: "approve_plan", token: "r20:abc" }, displayed_plan: shown };
  assert.deepEqual(planForApproval(current), shown);

  // The plan moved to revision 21: the old projection proves nothing about what approval now means.
  const revised = { ...current, needs: { kind: "approve_plan", token: "r21:def" }, displayed_plan: shown };
  assert.equal(planForApproval(revised), null);

  // Nothing displayed to this client: approving would approve something unseen.
  assert.equal(planForApproval({ ...current, displayed_plan: undefined }), null);
});

function approvalProjection(terms: Record<string, unknown> = {}) {
  return readStatusPayload(envelope({
    status: "AWAITING_GOAL_APPROVAL", done: false,
    needs: { kind: "approve_plan", token: "plan-7" },
    displayed_plan: {
      token: "plan-7", review_markdown: "Print Hello with a test",
      acceptance_criteria: [{ id: "AC1", criterion: "Print Hello", verification_method: "test: hello", human_review: false }],
      constraints: [], permission_boundaries: [], scope_exclusions: [],
      ...terms,
    },
  }));
}

test("approval retains allowed legacy and complete plan projections without dropping fields", () => {
  for (const terms of [
    {},
    { milestones: [], end_to_end_flow: [] },
    { milestones: [{ id: "M1", objective: "Print Hello" }] },
    { end_to_end_flow: ["Run the tool"] },
    { milestones: [{ id: "", objective: "", acceptance_criteria: [] }], end_to_end_flow: [""] },
    {
      milestones: [{ id: "M1", objective: "Print Hello", acceptance_criteria: ["AC1"], extra: { retained: true } }],
      end_to_end_flow: ["Run the tool", "Read Hello"], extra: { retained: true },
    },
  ]) {
    const current = approvalProjection(terms);
    assert.equal(planForApproval(current), current.displayed_plan, JSON.stringify(terms));
    assert.deepEqual(planForApproval(current), current.displayed_plan);
  }
});

test("approval refuses present milestones that cannot describe the full plan", () => {
  for (const milestones of [
    null, false, "M1", {}, [null], ["M1"], [[]],
    [{ id: "M1" }], [{ objective: "Print Hello" }],
    [{ id: 1, objective: "Print Hello" }], [{ id: "M1", objective: false }],
  ]) {
    assert.equal(planForApproval(approvalProjection({ milestones })), null, JSON.stringify(milestones));
  }
});

test("approval refuses malformed optional milestone criterion references", () => {
  for (const acceptance_criteria of [null, false, "AC1", {}, [null], [1], [{}]]) {
    const milestones = [{ id: "M1", objective: "Print Hello", acceptance_criteria }];
    assert.equal(planForApproval(approvalProjection({ milestones })), null, JSON.stringify(acceptance_criteria));
  }
});

test("approval refuses present end-to-end flows that cannot be displayed", () => {
  for (const end_to_end_flow of [null, false, "Run the tool", {}, [null], [1], [{}], [[]]]) {
    assert.equal(planForApproval(approvalProjection({ end_to_end_flow })), null, JSON.stringify(end_to_end_flow));
  }
});

test("a complete plan still needs its exact displayed approval token", () => {
  const complete = {
    milestones: [{ id: "M1", objective: "Print Hello", acceptance_criteria: ["AC1"] }],
    end_to_end_flow: ["Run the tool", "Read Hello"],
  };
  for (const token of [undefined, "", "plan-8"]) {
    assert.equal(planForApproval(approvalProjection({ ...complete, token })), null);
    const current = approvalProjection(complete);
    current.needs = { kind: "approve_plan", token };
    assert.equal(planForApproval(current), null);
  }
});

test("the run-details usage line uses public cap accounting even without token usage", () => {
  const view = readStatusPayload(envelope({ status: "RUNNING", done: false,
    cost: { spent_usd: 1.966683, cap_usd: 2, complete: false, unpriced_stages: 10 } }));
  assert.equal(usageLine(view), "Cost counted toward cap: at least $1.97 / $2.00 cap (partial; 10 unpriced stages)");
});

test("changeset status JSON is read, and its refusal is an error (#2066)", () => {
  const summary = readChangesetSummary(JSON.stringify({
    changeset: "changeset-1", runs: [], approval: { set_token: null, waiting_on: [], next_command: null },
    verification: { waiting_on: [], done: true, merge_order: [], next_command: null },
  }));
  assert.equal(summary.changeset, "changeset-1");
  assert.throws(() => readChangesetSummary(JSON.stringify({ error: "this run is in no change set" })), /in no change set/);
  assert.throws(() => readChangesetSummary("{}"), /did not return a change set/);
});


test("a missing human action is not a completion claim", () => {
  for (const category of ["blocked", "stopped"]) {
    assert.equal(needsSummary({ status: "PAUSED_VERIFICATION_PREPARATION", done: false, needs: null,
      recovery: { category } }), "nothing needed from you");
  }
  assert.equal(needsSummary({ status: "TASK_COMPLETE", done: true, needs: null }), "nothing; the run is complete");
});


test("approval refuses missing, empty or malformed canonical review content", () => {
  for (const review_markdown of [undefined, null, false, 7, {}, [], "", " \n\t"]) {
    assert.equal(planForApproval(approvalProjection({ review_markdown })), null, JSON.stringify(review_markdown));
  }
});
