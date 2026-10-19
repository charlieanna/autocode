import assert from "node:assert/strict";
import test from "node:test";

import {
  ClientGap,
  applyDecisions,
  commandInputs,
  findUnsupplied,
  isAdvancing,
  leavesForPerson,
  openDecisions,
  parseNamedCommand,
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
