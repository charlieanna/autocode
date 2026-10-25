import assert from "node:assert/strict";
import test from "node:test";

import {
  ClientGap,
  applyDecisions,
  effortLevels,
  boundOffer,
  commandInputs,
  findUnsupplied,
  isAdvancing,
  leavesForPerson,
  openDecisions,
  parseNamedCommand,
  requiresConfirmation,
  splitCommandLine,
} from "../src/core/next-command";
import type { StatusView } from "../src/core/view";

const RUN = "/ws/.autocode/runs/20261015-run";
const WS = "/ws";
const own = { runDir: RUN, workspace: WS };

function timeOffer(): StatusView {
  return {
    status: "PAUSED_TIME_LIMIT", done: false, recovery: { token: "current" },
    needs: { kind: "resume", command_inputs: [{ id: "max_seconds", kind: "integer", flag: "--max-seconds", placeholder: "N",
      arg_index: 2, label: "Total active time (seconds)", current: 2700, elapsed: 3010.885, minimum: 3011, maximum: Number.MAX_SAFE_INTEGER }] },
    next_command: `AUTOCODE_EXPECTED_RECOVERY_TOKEN=current autocode --workspace ${WS} --run-dir ${RUN} --resume-paused --max-seconds N --expected-recovery-token -`,
  };
}

function planningOffer(saveOnly = false): StatusView {
  return {
    status: "PAUSED_PLANNING_BUDGET", done: false, recovery: { token: "current" },
    needs: { kind: "planning_budget", command_inputs: [{ id: "planning_review_call_limit", kind: "integer",
      flag: "--planning-review-call-limit", placeholder: "N", arg_index: saveOnly ? 1 : 2,
      label: "Total plan-review calls", current: 2, used: 2, minimum: 3, maximum: Number.MAX_SAFE_INTEGER }] },
    next_command: `AUTOCODE_EXPECTED_RECOVERY_TOKEN=current autocode --workspace ${WS} --run-dir ${RUN} ${saveOnly ? "" : "--resume-paused "}--planning-review-call-limit N --expected-recovery-token -`,
  };
}

function grantOffer(): StatusView {
  return { status: "PAUSED_TIMEOUT_RECOVERY", done: false, recovery: { token: "current" },
    needs: { kind: "resume", command_inputs: [{ id: "grant_recovery", kind: "integer", flag: "--grant-recovery", placeholder: "N",
      arg_index: 2, label: "Additional recoveries", current: 3, used: 3, minimum: 1, maximum: 3 }] },
    next_command: `AUTOCODE_EXPECTED_RECOVERY_TOKEN=current autocode --workspace ${WS} --run-dir ${RUN} --resume-paused --grant-recovery N --expected-recovery-token -` };
}

test("finite recovery grant is only the exact resume command and cannot exceed counted spend", () => {
  const view = grantOffer();
  const named = parseNamedCommand(view.next_command!, own);
  const inputs = commandInputs(view, named);
  assert.deepEqual(applyDecisions(named.argv, { grant_recovery: "1" }, inputs), ["--resume-paused", "--grant-recovery", "1", "--expected-recovery-token", "-"]);
  for (const value of ["", "0", "4", "1.5", "1e2", "9007199254740992"]) assert.throws(() => applyDecisions(named.argv, { grant_recovery: value }, inputs), ClientGap);
  for (const [key, value] of Object.entries({ used: 3.5, minimum: 0, maximum: 4, current: 0, flag: "--max-seconds" })) {
    const bad = structuredClone(view); Object.assign(bad.needs!.command_inputs![0]!, { [key]: value });
    assert.throws(() => commandInputs(bad, named), ClientGap);
  }
  assert.throws(() => commandInputs(view, { ...named, argv: named.argv.slice(1) }), ClientGap);
  assert.throws(() => commandInputs(view, { ...named, env: { AUTOCODE_EXPECTED_RECOVERY_TOKEN: "stale" } }), ClientGap);
});

test("typed review allowance binds both existing shapes without adding resume or approval", () => {
  for (const saveOnly of [false, true]) {
    const view = planningOffer(saveOnly);
    const named = parseNamedCommand(view.next_command!, own);
    const inputs = commandInputs(view, named);
    const expected = [...(saveOnly ? [] : ["--resume-paused"]), "--planning-review-call-limit", "4", "--expected-recovery-token", "-"];
    assert.deepEqual(applyDecisions(named.argv, { planning_review_call_limit: "4" }, inputs), expected);
    for (const value of ["", "0", "2", "-1", "1.5", "4e2", "9007199254740992", "9".repeat(5000)]) {
      assert.throws(() => applyDecisions(named.argv, { planning_review_call_limit: value }, inputs), ClientGap);
    }
    for (const [key, value] of Object.entries({ used: 2.5, current: 0, minimum: 2, arg_index: saveOnly ? 2 : 1, label: "Seconds", flag: "--max-seconds" })) {
      const bad = structuredClone(view);
      Object.assign(bad.needs!.command_inputs![0]!, { [key]: value });
      assert.throws(() => commandInputs(bad, named), ClientGap, key);
    }
    assert.throws(() => commandInputs(view, { ...named, env: { AUTOCODE_EXPECTED_RECOVERY_TOKEN: "stale" } }), ClientGap);
    assert.throws(() => commandInputs(view, { ...named, argv: [...named.argv, "--approve-goal", "-"] }), ClientGap);
    assert.equal(findUnsupplied(named.argv), "N");
  }
});

test("typed total fills only the exact bound slot with the person's finite safe integer", () => {
  const view = timeOffer();
  const named = parseNamedCommand(view.next_command!, own);
  const inputs = commandInputs(view, named);
  assert.equal(findUnsupplied(named.argv, inputs), null);
  assert.equal(openDecisions(named.argv, inputs)[0]?.kind, "integer");
  assert.throws(() => applyDecisions(named.argv, {}, inputs), /whole number/);
  assert.deepEqual(applyDecisions(named.argv, { max_seconds: "5400" }, inputs),
    ["--resume-paused", "--max-seconds", "5400", "--expected-recovery-token", "-"]);
  assert.deepEqual(named.env, { AUTOCODE_EXPECTED_RECOVERY_TOKEN: "current" });
  for (const value of ["", " ", "0", "-1", "3010", "1.5", "5e3", "Infinity", "5400 --approve-goal -", "9007199254740992", "9".repeat(5000)]) {
    assert.throws(() => applyDecisions(named.argv, { max_seconds: value }, inputs), ClientGap, value.slice(0, 30));
  }
  assert.deepEqual(applyDecisions(named.argv, { max_seconds: String(Number.MAX_SAFE_INTEGER) }, inputs),
    ["--resume-paused", "--max-seconds", String(Number.MAX_SAFE_INTEGER), "--expected-recovery-token", "-"]);
});

test("typed total rejects stale tokens, changed commands and malformed descriptor metadata", () => {
  const view = timeOffer();
  const named = parseNamedCommand(view.next_command!, own);
  for (const [key, value] of Object.entries({ arg_index: 1, flag: "--max-cost-usd", minimum: 1, maximum: 0, elapsed: NaN, current: 2700.5 })) {
    const bad = structuredClone(view);
    Object.assign(bad.needs!.command_inputs![0]!, { [key]: value });
    assert.throws(() => commandInputs(bad, named), ClientGap, key);
  }
  for (const bad of [{ ...named, argv: [...named.argv, "--approve-goal", "-"] },
    { ...named, env: { AUTOCODE_EXPECTED_RECOVERY_TOKEN: "old" } }, { ...named, env: {} }]) {
    assert.throws(() => commandInputs(view, bad), ClientGap);
  }
  assert.equal(findUnsupplied(named.argv), "N", "bare N remains unsupported");
});

function costOffer(separate = false): StatusView {
  const raise = `AUTOCODE_EXPECTED_RECOVERY_TOKEN=current autocode --workspace ${WS} --run-dir ${RUN} --resume-paused --max-cost-usd N --expected-recovery-token -`;
  return { status: separate ? "RESOLVER_PENDING" : "PAUSED_COST_LIMIT", done: false, recovery: { token: "current" },
    needs: { kind: "resume", command_inputs: [{ id: "max_cost_usd", kind: "integer", flag: "--max-cost-usd", placeholder: "N",
      arg_index: 2, label: "Cost cap (whole USD)", current: 2.0, used: 2.37, minimum: 3, maximum: Number.MAX_SAFE_INTEGER,
      ...(separate ? { command: raise } : {}) }] },
    next_command: separate ? `autocode --workspace ${WS} --run-dir ${RUN} --resume-paused` : raise };
}

test("every bound the core describes is typed into its own named raise, not three hardcoded ones (#2044)", () => {
  const view = costOffer();
  const named = parseNamedCommand(view.next_command!, own);
  const inputs = commandInputs(view, named);
  assert.equal(findUnsupplied(named.argv, inputs), null);
  assert.deepEqual(applyDecisions(named.argv, { max_cost_usd: "5" }, inputs),
    ["--resume-paused", "--max-cost-usd", "5", "--expected-recovery-token", "-"]);
  assert.throws(() => applyDecisions(named.argv, { max_cost_usd: "2" }, inputs), ClientGap);
  for (const [key, value] of Object.entries({ label: "Total active time (seconds)", id: "max_seconds", minimum: 4,
    flag: "--max-unknown", used: -1, current: 0 })) {
    const bad = structuredClone(view);
    Object.assign(bad.needs!.command_inputs![0]!, { [key]: value });
    assert.throws(() => commandInputs(bad, named), ClientGap, key);
  }
  assert.equal(boundOffer(view, own), null, "an attached row is no separate offer");
});

test("a bound held behind a plain resume names its raise separately, bound to the current token", () => {
  const view = costOffer(true);
  const named = parseNamedCommand(view.next_command!, own);
  assert.deepEqual(commandInputs(view, named), [], "the plain resume has no slot");
  const offer = boundOffer(view, own)!;
  assert.deepEqual(applyDecisions(offer.named.argv, { max_cost_usd: "3" }, [offer.input]),
    ["--resume-paused", "--max-cost-usd", "3", "--expected-recovery-token", "-"]);
  assert.deepEqual(offer.named.env, { AUTOCODE_EXPECTED_RECOVERY_TOKEN: "current" });
  const stale = { ...view, recovery: { token: "other" } };
  assert.throws(() => boundOffer(stale, own), ClientGap);
  const elsewhere = structuredClone(view);
  elsewhere.needs!.command_inputs![0]!.command = view.needs!.command_inputs![0]!.command!.replace(RUN, "/other/run");
  assert.throws(() => boundOffer(elsewhere, own), ClientGap);
});

test("splitCommandLine keeps quoted words whole", () => {
  assert.deepEqual(splitCommandLine(`autocode --feedback "keep the retry logic"`), [
    "autocode",
    "--feedback",
    "keep the retry logic",
  ]);
  assert.deepEqual(splitCommandLine("autocode --answer q1='two words'"), ["autocode", "--answer", "q1=two words"]);
  assert.deepEqual(splitCommandLine("autocode   --status\t--json"), ["autocode", "--status", "--json"]);
});

test("splitCommandLine reports an unbalanced quote instead of guessing", () => {
  assert.throws(() => splitCommandLine(`autocode --feedback "open`), ClientGap);
});

test("parseNamedCommand moves the issued token into the environment", () => {
  const parsed = parseNamedCommand(
    `AUTOCODE_APPROVE_GOAL_TOKEN=r3:abc autocode --workspace ${WS} --run-dir ${RUN} --approve-goal -`,
    own,
  );
  assert.deepEqual(parsed.env, { AUTOCODE_APPROVE_GOAL_TOKEN: "r3:abc" });
  assert.deepEqual(parsed.argv, ["--approve-goal", "-"]);
});

test("parseNamedCommand keeps a value that contains an equals sign", () => {
  const parsed = parseNamedCommand(`SECRET=x=y autocode --status`, own);
  assert.deepEqual(parsed.env, { SECRET: "x=y" });
  assert.deepEqual(parsed.argv, ["--status"]);
});

test("parseNamedCommand refuses a command about a different run", () => {
  assert.throws(
    () => parseNamedCommand(`autocode --run-dir /elsewhere --workspace ${WS} --approve-goal -`, own),
    /names --run-dir/,
  );
  assert.throws(() => parseNamedCommand(`rm -rf /`, own), /does not start with autocode/);
});

test("openDecisions names the placeholders a person has to fill", () => {
  assert.deepEqual(openDecisions(["--answer", "q-1=ANSWER", "--resume-paused", "--no-chat"]), [
    { questionId: "q-1", placeholder: "ANSWER", kind: "answer" },
  ]);
  assert.deepEqual(openDecisions(["--answer", "route-GLM=MODEL"]), [
    { questionId: "route-GLM", placeholder: "MODEL", kind: "model" },
  ]);
});

test("an unsupported named effort is a choice among the levels the model declares (#2057)", () => {
  const effort = { question_id: "effort-plan_reviewer", role: "plan_reviewer", declared: ["low", "high", "max"], requested: "medium" };
  const argv = ["--answer", "effort-plan_reviewer=EFFORT"];
  assert.deepEqual(openDecisions(argv), [{ questionId: "effort-plan_reviewer", placeholder: "EFFORT", kind: "effort" }]);
  assert.deepEqual(effortLevels(effort, "effort-plan_reviewer"), ["low", "high", "max"]);
  assert.deepEqual(applyDecisions(argv, { "effort-plan_reviewer": " High " }, [], effort), ["--answer", "effort-plan_reviewer=high"]);
  assert.throws(() => applyDecisions(argv, { "effort-plan_reviewer": "medium" }, [], effort), /Choose one of low, high, max/);
  assert.throws(() => applyDecisions(argv, { "effort-plan_reviewer": "high" }), /does not describe/);
  assert.throws(() => applyDecisions(argv, {}, [], effort), /decision for a person/);
});

test("applyDecisions substitutes the person's words and keeps everything else", () => {
  assert.deepEqual(applyDecisions(["--answer", "q-1=ANSWER", "--no-chat"], { "q-1": "use the existing lock" }), [
    "--answer",
    "q-1=use the existing lock",
    "--no-chat",
  ]);
});

test("applyDecisions never invents a decision a person owes", () => {
  assert.throws(() => applyDecisions(["--answer", "q-1=ANSWER"], {}), ClientGap);
  assert.throws(() => applyDecisions(["--grant-recovery", "N"], {}), /leaves N open/);
  assert.throws(() => applyDecisions(["--edit-goal", "GOAL_FILE"], {}), /leaves GOAL_FILE open/);
  assert.throws(() => applyDecisions(["--explain"], {}), /leaves --explain open/);
});

test("findUnsupplied reports a gap without needing the person's answers", () => {
  assert.equal(findUnsupplied(["--answer", "q-1=ANSWER"]), null);
  assert.equal(findUnsupplied(["--grant-recovery", "N"]), "N");
});

test("only a command that continues the run is advancing", () => {
  assert.equal(isAdvancing(["--resume-paused", "--no-chat"]), true);
  assert.equal(isAdvancing(["--approve-goal", "-"]), false);
  assert.equal(isAdvancing(["--answer", "q-1=x"]), false);
});

test("a pause AutoCode stopped for a person is not the client's to answer", () => {
  assert.equal(leavesForPerson({ kind: "resume" }), true);
  assert.equal(leavesForPerson({ kind: "retry_job" }), true);
  assert.equal(leavesForPerson({ kind: "recover_source" }), true);
  assert.equal(leavesForPerson({ kind: "answer", resolver_scope: "blocker" }), true);
  assert.equal(leavesForPerson({ kind: "answer", resolver_scope: "operational_exhaustion" }), true);
  assert.equal(leavesForPerson({ kind: "answer", resolver_scope: "clarify" }), false);
  assert.equal(leavesForPerson({ kind: "approve_plan" }), false);
  assert.equal(leavesForPerson(null), false);
});

test("the core can suppress a duplicate resume dialog without authorizing automatic continuation", () => {
  const need = { kind: "resume", confirmation_required: false };
  assert.equal(requiresConfirmation(need), false);
  assert.equal(leavesForPerson(need), true);
  assert.equal(requiresConfirmation({ kind: "resume" }), true);
  assert.equal(requiresConfirmation({ kind: "resume", confirmation_required: true }), true);
  assert.equal(requiresConfirmation({ kind: "retry_job", confirmation_required: false }), true);
  assert.equal(requiresConfirmation({ kind: "answer", resolver_scope: "blocker", confirmation_required: false }), true);
});
