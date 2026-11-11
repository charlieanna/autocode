import assert from "node:assert/strict";
import test from "node:test";
import { backendArgs, chatRoute, currentRoute, fillChatSet, fillSet, readStages } from "../src/core/stages";

const OPUS = "claude-opus-5-5";
const set = ["stages", "set", "terra", "--backend", "claude", "--json", "--model", "MODEL", "--effort", "EFFORT"];

/** The shape `autocode stages list --backend claude --json` prints, trimmed to the Builder. */
function view(stage: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: 1, backend: "claude", label: "Claude Code", file: "/home/me/.config/autocode/stages.toml", options_stop: null,
    stages: [{
      id: "terra", label: "Builder", does: "Writes the code for one task.", advice: "A fast, cheaper model at medium.",
      flag: "--terra-model", effort_flag: "--terra-reasoning-effort",
      default: { model: "claude-haiku-4-5-20251001", effort: "medium", offered: true }, chosen: null,
      options: [
        { model: "claude-haiku-4-5-20251001", levels: ["low", "medium"], family: "claude", context: null, account: "Claude subscription", effort_choices: ["low", "medium"], unavailable: null },
        { model: OPUS, levels: ["low", "high"], family: "claude", context: 200000, account: "Claude subscription", effort_choices: ["low", "high"], unavailable: "the Completion Reviewer runs claude-opus-5-5" },
        { model: 7, effort_choices: ["low"] },
      ],
      set: { next_command: set }, reset: null,
      ...stage,
    }, { label: "no id", set: { next_command: set } }],
  });
}

test("the stage view keeps each stage's text, options and named commands, and drops malformed rows", () => {
  const read = readStages(view());
  assert.equal(read.label, "Claude Code");
  assert.equal(read.stages.length, 1, "a stage without an id is dropped");
  const [builder] = read.stages;
  assert.equal(builder!.does, "Writes the code for one task.");
  assert.deepEqual(builder!.options.map((row) => [row.model, row.unavailable, row.context]), [
    ["claude-haiku-4-5-20251001", null, null],
    [OPUS, "the Completion Reviewer runs claude-opus-5-5", 200000],
  ]);
  assert.deepEqual(builder!.set, set);
  assert.equal(builder!.reset, null);
  assert.throws(() => readStages(JSON.stringify({ version: 2, stages: [] })), /Update AutoCode/);
});

test("saving fills only the stage's own command with a listed model and one of its levels", () => {
  const [builder] = readStages(view()).stages;
  assert.deepEqual(fillSet(builder!, "claude-haiku-4-5-20251001", "low").slice(-4), ["--model", "claude-haiku-4-5-20251001", "--effort", "low"]);
  assert.throws(() => fillSet(builder!, "evil --flag", "low"), /not offered evil --flag/);
  assert.throws(() => fillSet(builder!, OPUS, "low"), /cannot run claude-opus-5-5: the Completion Reviewer/);
  assert.throws(() => fillSet(builder!, "claude-haiku-4-5-20251001", "max"), /takes low, medium/);
  const [unnamed] = readStages(view({ set: { next_command: ["stages", "set", "terra", "MODEL"] } })).stages;
  assert.throws(() => fillSet(unnamed!, "claude-haiku-4-5-20251001", "low"), /names no choice/);
});

test("each stage says what it runs now and the list names the backend only when the person chose one", () => {
  const [chosen] = readStages(view({ chosen: { model: OPUS, effort: "high" }, reset: { next_command: ["stages", "reset", "terra"] } })).stages;
  assert.equal(currentRoute(chosen!), `${OPUS} at high (your choice)`);
  assert.deepEqual(chosen!.reset, ["stages", "reset", "terra"]);
  assert.equal(currentRoute(readStages(view()).stages[0]!), "claude-haiku-4-5-20251001 at medium (default)");
  assert.equal(currentRoute(readStages(view({ default: { model: "x", offered: false } })).stages[0]!), "chosen from your accounts when a chat starts");
  assert.deepEqual(backendArgs(["--engine", "claude"], "claude"), ["--backend", "claude"]);
  assert.deepEqual(backendArgs(["--provider=kilocode"], "kilocode"), ["--backend", "kilocode"]);
  assert.deepEqual(backendArgs(["--max-cost-usd", "3"], "opencode"), []);
});

test("a new chat's list names its picks, the command for each and the flags its start takes", () => {
  const list = ["stages", "list", "--json", "--backend", "claude"];
  const raw = JSON.parse(view({
    chat: { model: "claude-haiku-4-5-20251001", effort: "low" },
    chat_set: { next_command: [...list, "--chat-stage", "terra", "MODEL", "EFFORT"] },
    chat_reset: { next_command: list },
  }));
  raw.chat = { next_command: [...list, "--chat-stage", "terra", "claude-haiku-4-5-20251001", "low"],
    start_args: ["--terra-model", "claude-haiku-4-5-20251001", "--terra-reasoning-effort", "low"] };
  const read = readStages(JSON.stringify(raw));
  const [builder] = read.stages;
  assert.equal(chatRoute(builder!), "claude-haiku-4-5-20251001 at low (this chat)");
  assert.deepEqual(builder!.chatReset, list);
  assert.deepEqual(fillChatSet(builder!, "claude-haiku-4-5-20251001", "medium").slice(-4), ["--chat-stage", "terra", "claude-haiku-4-5-20251001", "medium"]);
  assert.throws(() => fillChatSet(builder!, OPUS, "low"), /cannot run claude-opus-5-5/, "refused as a saved choice is");
  assert.deepEqual(read.chat?.startArgs, raw.chat.start_args);
  // Without a pick a stage reads as every new chat runs it.
  const [plain] = readStages(view({ chosen: { model: OPUS, effort: "high" } })).stages;
  assert.equal(chatRoute(plain!), `${OPUS} at high (Stage models)`);
  assert.throws(() => fillChatSet(plain!, "claude-haiku-4-5-20251001", "low"), /update AutoCode/, "an older AutoCode names no pick");
  // Only a stage flag and its value reach a start, and only a new chat's own list is read.
  for (const start_args of [["--max-cost-usd", "0"], ["--terra-model"], ["--terra-model", "--resume"], "--terra-model x"]) {
    assert.equal(readStages(JSON.stringify({ ...raw, chat: { ...raw.chat, start_args } })).chat, null, JSON.stringify(start_args));
  }
  assert.equal(readStages(JSON.stringify({ ...raw, chat: { ...raw.chat, next_command: ["stages", "set", "terra"] } })).chat, null);
  assert.equal(readStages(JSON.stringify({ ...raw, chat: { ...raw.chat, next_command: [...list, "--run-dir", "/r"] } })).chat, null);
});
