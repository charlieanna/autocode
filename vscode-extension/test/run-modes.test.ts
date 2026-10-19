import assert from "node:assert/strict";
import test from "node:test";
import { readRunModes } from "../src/core/modes";

test("spend confirmation follows core mode metadata and old/unknown values stay conservative", () => {
  for (const [policy, expected] of [["planning", "planning"], ["start", "start"], [undefined, "start"], ["unknown", "start"]]) {
    const list = readRunModes(JSON.stringify({ schema_version: 1, default: "mode", modes: [
      { id: "mode", label: "Mode", start_args: ["--spec-first"], spend_confirmation: policy },
    ] }));
    assert.equal(list.modes[0]?.spendConfirmation, expected);
    assert.deepEqual(list.modes[0]?.startArgs, ["--spec-first"]);
  }
});
