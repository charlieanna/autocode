import { strict as assert } from "node:assert";
import { test } from "node:test";
import { markdown } from "../src/chat-markdown";

test("lists retain their starting number and task state", () => {
  const ordered = markdown("3. first\n4. second")[0]!;
  assert.equal(ordered.start, 3);
  const tasks = markdown("- [x] done\n- [ ] pending")[0]!;
  assert.deepEqual(tasks.children?.map((row) => row.checked), [true, false]);
});

test("escaped plan text retains literal underscores", () => {
  const nodes = markdown(String.raw`- tests/\_\_init\_\_\.py`);
  const flatten = (node: (typeof nodes)[number]): string => (node.text ?? "") + (node.children ?? []).map(flatten).join("");
  assert.equal(nodes.map(flatten).join(""), "tests/__init__.py");
});
