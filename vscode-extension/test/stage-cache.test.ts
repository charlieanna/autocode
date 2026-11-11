import assert from "node:assert/strict";
import test from "node:test";
import { listKey, MAX_AGE_MS, MAX_ENTRIES, StageListCache, STORAGE_KEY } from "../src/core/stage-cache";

/** The smallest `stages list --json` the parser keeps: one stage, labelled `label`. */
function list(label = "Builder"): string {
  return JSON.stringify({ version: 1, backend: "claude", label: "Claude Code", options_stop: null, stages: [{
    id: "builder", label, does: "", advice: "", default: { model: "haiku", effort: "medium", offered: true }, chosen: null, options: [],
    set: { next_command: ["stages", "set", "builder", "--json", "--model", "MODEL", "--effort", "EFFORT"] }, reset: null,
  }] });
}

function memory() {
  const values = new Map<string, unknown>();
  return { values, get: <T>(key: string) => values.get(key) as T | undefined, update: async (key: string, value: unknown) => { values.set(key, value); } };
}

test("one read serves every caller while it runs, and is fresh for a minute", async () => {
  let clock = 5_000;
  const cache = new StageListCache(undefined, () => clock);
  const key = listKey("/ws", "autocode", [], ["stages", "list", "--json"]);
  let reads = 0;
  let release!: (value: string) => void;
  const read = () => { reads++; return new Promise<string>((resolve) => { release = resolve; }); };
  const both = [cache.load(key, read), cache.load(key, read)];
  assert.equal(cache.loading(key), true);
  release(list());
  const [first, second] = await Promise.all(both);
  assert.equal(reads, 1);
  assert.equal(first, second);
  assert.equal(cache.loading(key), false);
  assert.equal(cache.peek(key)?.stages[0]?.label, "Builder");
  assert.equal(cache.fresh(key), true);
  clock += MAX_AGE_MS;
  assert.equal(cache.fresh(key), false, "older than a minute is read again");
});

test("lists are kept by folder, CLI and arguments", () => {
  assert.notEqual(listKey("/a", "autocode", [], ["x"]), listKey("/b", "autocode", [], ["x"]));
  assert.notEqual(listKey("/a", "autocode", [], ["x"]), listKey("/a", "python", ["tools/autocode.py"], ["x"]));
  assert.notEqual(listKey("/a", "autocode", [], ["--backend", "claude"]), listKey("/a", "autocode", [], ["--backend", "opencode"]));
});

test("a saved list is shown after a restart but counts as stale", async () => {
  const store = memory();
  const key = listKey("/ws", "autocode", [], ["stages", "list", "--json"]);
  await new StageListCache(store).load(key, async () => list("Saved"));
  assert.ok(store.values.get(STORAGE_KEY));
  const restarted = new StageListCache(store);
  assert.equal(restarted.peek(key)?.stages[0]?.label, "Saved");
  assert.equal(restarted.fresh(key), false);
});

test("a list that does not parse is never kept", async () => {
  const cache = new StageListCache();
  await assert.rejects(cache.load("k", async () => "{\"version\": 9}"), /Update AutoCode/);
  assert.equal(cache.peek("k"), undefined);
  cache.put("k", "not json");
  assert.equal(cache.peek("k"), undefined);
  await assert.rejects(cache.load("k", async () => { throw new Error("autocode exited 2"); }), /exited 2/);
  assert.equal(cache.loading("k"), false, "a failed read can be tried again");
  const store = memory();
  store.values.set(STORAGE_KEY, { k: { stdout: "garbled" } });
  assert.equal(new StageListCache(store).peek("k"), undefined, "a saved list an older extension wrote and this one cannot read");
});

test("a change makes every list stale, including one being read when it happened", async () => {
  let listeners = 0;
  const cache = new StageListCache(undefined, () => 1);
  cache.onChange(() => { listeners++; });
  cache.put("a", list());
  assert.equal(cache.fresh("a"), true);
  let release!: (value: string) => void;
  const reading = cache.load("b", () => new Promise<string>((resolve) => { release = resolve; }));
  cache.expire();
  release(list());
  await reading;
  assert.equal(cache.fresh("a"), false);
  assert.equal(cache.fresh("b"), false, "read before the change, so it may not show it");
  assert.equal(cache.peek("b")?.stages.length, 1, "still shown until the next read");
  assert.equal(listeners, 3, "put, expire and the read each say so");
});

test(`only the ${MAX_ENTRIES} lists written last are kept`, () => {
  const store = memory();
  const cache = new StageListCache(store);
  for (let index = 0; index <= MAX_ENTRIES; index++) cache.put(`k${index}`, list());
  cache.put("k1", list());
  cache.put("extra", list());
  assert.equal(cache.peek("k0"), undefined);
  assert.equal(cache.peek("k2"), undefined, "the oldest write goes first");
  assert.ok(cache.peek("k1"), "written again, it is new");
  assert.equal(Object.keys(store.values.get(STORAGE_KEY) as object).length, MAX_ENTRIES);
});
