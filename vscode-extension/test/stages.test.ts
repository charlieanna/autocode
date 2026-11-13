import assert from "node:assert/strict";
import test from "node:test";
import { backendArgs, chatRoute, chatSource, currentRoute, fillChatSet, fillSet, modelArgs, readStages } from "../src/core/stages";
import { listKey, MAX_AGE_MS, StageListCache, STORAGE_KEY } from "../src/core/stage-cache";

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
  // Beside a dropdown that already shows the model: only where it comes from.
  assert.equal(chatSource(builder!), "this chat");
  assert.equal(chatSource(plain!), "Stage models");
  assert.equal(chatSource(readStages(view()).stages[0]!), "default");
  assert.equal(chatSource(readStages(view({ default: { model: "x", offered: false } })).stages[0]!), "", "nothing chosen until the chat starts");
  assert.throws(() => fillChatSet(plain!, "claude-haiku-4-5-20251001", "low"), /update AutoCode/, "an older AutoCode names no pick");
  // Only a stage flag and its value reach a start, and only a new chat's own list is read.
  for (const start_args of [["--max-cost-usd", "0"], ["--terra-model"], ["--terra-model", "--resume"], "--terra-model x"]) {
    assert.equal(readStages(JSON.stringify({ ...raw, chat: { ...raw.chat, start_args } })).chat, null, JSON.stringify(start_args));
  }
  assert.equal(readStages(JSON.stringify({ ...raw, chat: { ...raw.chat, next_command: ["stages", "set", "terra"] } })).chat, null);
  assert.equal(readStages(JSON.stringify({ ...raw, chat: { ...raw.chat, next_command: [...list, "--run-dir", "/r"] } })).chat, null);
});

// The shared new-chat list cache: what it shows at once, when it reads again, and what it keeps.

test("a saved list is shown at once and counted stale, never as fresh", () => {
  const saved = new Map([[STORAGE_KEY, { k: { stdout: view(), at: 5 } }]]);
  const cache = new StageListCache({ get: <T>(key: string) => saved.get(key) as T | undefined, update: async () => {} }, () => 1000);
  assert.ok(cache.peek("k"), "the saved list is shown without waiting for the CLI");
  assert.equal(cache.fresh("k"), false, "a saved list is read again in the background");
});

test("the cache key names the folder, the CLI and the exact arguments", () => {
  assert.notEqual(listKey("/a", "autocode", [], ["stages", "list"]), listKey("/b", "autocode", [], ["stages", "list"]));
  assert.notEqual(listKey("/a", "autocode", [], ["stages", "list"]), listKey("/a", "other", [], ["stages", "list"]));
  assert.notEqual(listKey("/a", "autocode", [], ["stages", "list"]), listKey("/a", "autocode", ["-u"], ["stages", "list"]));
  assert.notEqual(listKey("/a", "autocode", [], ["stages", "list"]), listKey("/a", "autocode", [], ["stages", "set"]));
  assert.equal(listKey(undefined, "autocode", [], ["x"]), listKey("", "autocode", [], ["x"]), "no folder is one folder");
});

test("concurrent reads of one list share a single call", async () => {
  const cache = new StageListCache(undefined, () => 1);
  let reads = 0;
  const slow = (): Promise<string> => { reads++; return new Promise((resolve) => setTimeout(() => resolve(view()), 5)); };
  const [a, b, c] = await Promise.all([cache.load("k", slow), cache.load("k", slow), cache.load("k", slow)]);
  assert.equal(reads, 1, "three surfaces wanting the list make one call");
  assert.equal(a.stages[0]!.id, "terra");
  assert.equal(b.stages[0]!.id, "terra");
  assert.equal(c.stages[0]!.id, "terra");
  assert.equal(cache.fresh("k"), true);
  assert.equal(cache.loading("k"), false);
});

test("a list made stale while it is read is kept but never counted fresh", async () => {
  let now = 100;
  const cache = new StageListCache(undefined, () => now);
  let release!: () => void;
  const reading = cache.load("k", () => new Promise<string>((resolve) => { release = () => resolve(view()); }));
  cache.expire();
  release();
  await reading;
  assert.ok(cache.peek("k"), "the answer is still shown");
  assert.equal(cache.fresh("k"), false, "a read that began before expire must not outlast it");
  now += MAX_AGE_MS;
  assert.equal(cache.fresh("k"), false, "an old timestamp does not age into fresh");
});

test("a list that does not parse is neither kept nor shown", async () => {
  const cache = new StageListCache(undefined, () => 1);
  await assert.rejects(() => cache.load("k", async () => "not json"));
  assert.equal(cache.peek("k"), undefined);
  assert.equal(cache.fresh("k"), false);
  cache.put("k", "also not json");
  assert.equal(cache.peek("k"), undefined, "a put of an unreadable list keeps nothing");
});

test("a read again tells its listeners; an expire says every list went stale", async () => {
  const cache = new StageListCache(undefined, () => 1);
  const seen: string[] = [];
  cache.onChange((key) => seen.push(key));
  await cache.load("k", async () => view());
  cache.put("j", view());
  cache.expire();
  assert.deepEqual(seen, ["k", "j", ""]);
});

test("the newest lists are kept and the oldest dropped", async () => {
  const cache = new StageListCache(undefined, () => 1);
  for (let i = 0; i < 9; i++) await cache.load(`k${i}`, async () => view());
  assert.equal(cache.peek("k0"), undefined, "the oldest goes first");
  assert.ok(cache.peek("k8"), "the newest stays");
});

test("configured project and settings routes outrank Stage models for a new chat, without changing currentRoute", () => {
  const fromProject = readStages(view({
    chosen: { model: OPUS, effort: "high" },
    configured: { model: "zai-coding-plan/glm-5.3", effort: "high", source: "project" },
  })).stages[0]!;
  assert.equal(chatRoute(fromProject), "zai-coding-plan/glm-5.3 at high (project)");
  assert.equal(chatSource(fromProject), "project");
  assert.equal(currentRoute(fromProject), `${OPUS} at high (your choice)`, "Stage models still reports stages.toml");

  const fromSettings = readStages(view({
    configured: { model: "xiaomi-token-plan-sgp/mimo-v2.6-pro", effort: null, source: "settings" },
  })).stages[0]!;
  assert.equal(chatRoute(fromSettings), "xiaomi-token-plan-sgp/mimo-v2.6-pro at its own default level (settings)");
  assert.equal(chatSource(fromSettings), "settings");

  const picked = readStages(view({
    configured: { model: "zai-coding-plan/glm-5.3", effort: "high", source: "project" },
    chat: { model: "claude-haiku-4-5-20251001", effort: "low" },
  })).stages[0]!;
  assert.equal(chatRoute(picked), "claude-haiku-4-5-20251001 at low (this chat)");
  assert.equal(chatSource(picked), "this chat");
});

test("chat.changeable and modelArgs carry single-model locks and new-run model options", () => {
  const list = ["stages", "list", "--json", "--backend", "opencode"];
  const raw = JSON.parse(view());
  raw.chat = {
    next_command: [...list, "--", "--single-model", "zai-coding-plan/glm-5.3"],
    start_args: [],
    changeable: false,
    reason: "single-model sets one model for every stage; remove it to change one stage for this chat.",
  };
  const parsed = readStages(JSON.stringify(raw));
  assert.equal(parsed.chat?.changeable, false);
  assert.match(parsed.chat?.reason ?? "", /single-model/);

  assert.deepEqual(modelArgs(["--max-cost-usd", "5", "--in-place"]), []);
  assert.deepEqual(
    modelArgs([
      "--engine", "opencode",
      "--planner-model", "zai-coding-plan/glm-5.3",
      "--terra-reasoning-effort=high",
      "--single-model", "m",
      "--reasoning-effort", "low",
      "--builder-strong-model=s",
      "--max-cost-usd", "3",
      "--builder-model", "--dangling",
    ]),
    [
      "--",
      "--planner-model", "zai-coding-plan/glm-5.3",
      "--terra-reasoning-effort=high",
      "--single-model", "m",
      "--reasoning-effort", "low",
      "--builder-strong-model=s",
    ],
  );
});
