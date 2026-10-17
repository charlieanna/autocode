import assert from "node:assert/strict";
import test from "node:test";

import {
  ClientGap,
  applyDecisions,
  findUnsupplied,
  isAdvancing,
  leavesForPerson,
  openDecisions,
  parseNamedCommand,
  splitCommandLine,
} from "../src/core/next-command";

const RUN = "/ws/.autocode/runs/20261015-run";
const WS = "/ws";
const own = { runDir: RUN, workspace: WS };

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
