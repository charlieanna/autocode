import assert from "node:assert/strict";
import test from "node:test";

import raw from "./fixtures/reference-client.json";

import { ClientGap, applyDecisions, findUnsupplied, openDecisions, parseNamedCommand } from "../src/core/next-command";

/**
 * The extension may do nothing the headless conformance client cannot (#884).
 *
 * `fixtures/reference-client.json` records what `scenarios/harness/conformance.py`
 * itself makes of each `next_command`, taken from AutoCode on master. Parsing has
 * to agree exactly. Where the reference client acts, this one has to agree it may,
 * or refuse for the same reason.
 */

interface ReferenceCase {
  name: string;
  workspace: string;
  runDir: string;
  nextCommand: string;
  expect: {
    ok: boolean;
    argv?: string[];
    env?: Record<string, string>;
    decideOk: boolean;
    decidedTo?: string[];
    decideError?: string;
    error?: string;
  };
}

const fixtures: ReferenceFixtures = raw as unknown as ReferenceFixtures;

interface ReferenceFixtures {
  _source: string;
  cases: ReferenceCase[];
}

function pick(name: string): ReferenceCase {
  const item = fixtures.cases.find((row) => row.name === name);
  assert.ok(item, `fixture ${name} is missing`);
  return item;
}

test("the fixtures come from the reference client", () => {
  assert.match(fixtures._source, /conformance\.py/);
  for (const name of ["approve_plan", "route model", "grant recovery N", "other run", "not the CLI"]) {
    assert.ok(fixtures.cases.some((row) => row.name === name), `missing case ${name}`);
  }
});

for (const item of fixtures.cases) {
  test(`${item.name}: parsing agrees with AutoCode's reference client`, () => {
    const own = { runDir: item.runDir, workspace: item.workspace };
    if (!item.expect.ok) {
      assert.throws(() => parseNamedCommand(item.nextCommand, own), ClientGap);
      return;
    }
    const parsed = parseNamedCommand(item.nextCommand, own);
    assert.deepEqual(parsed.argv, item.expect.argv);
    assert.deepEqual(parsed.env, item.expect.env);
  });
}

test("the reference client stops on what this client must leave to a person", () => {
  for (const name of ["grant recovery N", "edit goal file", "route model"]) {
    const item = pick(name);
    assert.equal(item.expect.decideOk, false, `${name}: the reference client would have proceeded`);
    const parsed = parseNamedCommand(item.nextCommand, { runDir: item.runDir, workspace: item.workspace });
    assert.throws(() => applyDecisions(parsed.argv, {}), ClientGap, name);
  }
});

test("an unsupplied placeholder is named as the same gap in AutoCode", () => {
  for (const name of ["grant recovery N", "edit goal file"]) {
    const item = pick(name);
    const parsed = parseNamedCommand(item.nextCommand, { runDir: item.runDir, workspace: item.workspace });
    const word = findUnsupplied(parsed.argv);
    assert.ok(word, `${name} should name an unsupplied placeholder`);
    assert.match(item.expect.decideError ?? "", new RegExp(`leaves '?${word}`));
  }
});

test("a plain action runs on both clients with nothing invented", () => {
  for (const name of ["approve_plan", "abandon stage"]) {
    const item = pick(name);
    assert.equal(item.expect.decideOk, true);
    const parsed = parseNamedCommand(item.nextCommand, { runDir: item.runDir, workspace: item.workspace });
    assert.deepEqual(applyDecisions(parsed.argv, {}), parsed.argv);
    assert.deepEqual(openDecisions(parsed.argv), []);
  }
});

test("where the reference client answers for a person, this client asks one", () => {
  // conformance.py fills `q-1=ANSWER` from the question's proposed default so a scenario can run
  // unattended, and records the source it took it from. A client a person is watching must not do
  // that: a proposed default is AutoCode's guess, not the user's decision. So it refuses until a
  // person answers, then sends exactly what they said.
  const item = pick("answer proposed default");
  assert.equal(item.expect.decideOk, true);
  assert.ok(item.expect.decidedTo?.includes("q-1=the existing lock"), "the reference filled the proposed default");

  const parsed = parseNamedCommand(item.nextCommand, { runDir: item.runDir, workspace: item.workspace });
  assert.deepEqual(parsed.env, { AUTOCODE_RESOLVER_TOKEN: "r7:beef" });
  assert.deepEqual(openDecisions(parsed.argv), [{ questionId: "q-1", placeholder: "ANSWER", kind: "answer" }]);
  assert.throws(() => applyDecisions(parsed.argv, {}), /decision for a person/);
  assert.deepEqual(applyDecisions(parsed.argv, { "q-1": "the tenant lock" }), [
    "--answer",
    "q-1=the tenant lock",
    "--resolver-token",
    "-",
  ]);
});
