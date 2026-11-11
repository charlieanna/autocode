import assert from "node:assert/strict";
import test from "node:test";

import { chosenPlacement, placementArgs, readWorktrees } from "../src/core/worktrees";

const LIST = {
  schema: 1, folder: "/p", branch: "main", default: "new_branch",
  places: [
    { id: "new_branch", label: "New worktree and branch", does: "", start_args: ["--build-in-worktree"], warning: null },
    { id: "branch", label: "Existing branch", does: "", branches: [
      { name: "feature", start_args: ["--build-in-worktree", "--worktree-branch", "feature"], unavailable: null },
      { name: "taken", start_args: null, unavailable: "It is checked out in /elsewhere." },
      { name: "sneaky", start_args: ["--build-in-worktree", "--worktree-branch", "sneaky", "--yes"], unavailable: null },
    ] },
    { id: "folder", label: "This folder", does: "", start_args: ["--in-place"], warning: "can conflict" },
    { id: "odd", label: "Something else", does: "", start_args: ["--allow-uncontained-tools"], warning: null },
  ],
};

test("a new chat's places keep only the placements a start may carry", () => {
  const list = readWorktrees(JSON.stringify(LIST));
  assert.deepEqual(list.places.map((place) => place.id), ["new_branch", "branch", "folder"], "a place with other flags is dropped");
  const branches = list.places[1]!.branches;
  assert.deepEqual(branches.map((row) => [row.name, row.startArgs]), [
    ["feature", ["--build-in-worktree", "--worktree-branch", "feature"]], ["taken", null], ["sneaky", null]]);
  assert.match(branches[2]!.unavailable!, /names no way to start/);
  assert.equal(placementArgs(["--build-in-worktree", "--worktree-branch", "-x"]), null);
  assert.throws(() => readWorktrees("{}"), /does not read/);
});

test("a choice turns into the start flags the core named for it", () => {
  const list = readWorktrees(JSON.stringify(LIST));
  assert.deepEqual(chosenPlacement(list, undefined).args, ["--build-in-worktree"], "the default is a new branch");
  assert.deepEqual(chosenPlacement(list, { place: "branch", branch: "feature" }).args, ["--build-in-worktree", "--worktree-branch", "feature"]);
  assert.throws(() => chosenPlacement(list, { place: "branch", branch: "taken" }), /not available: It is checked out/);
  assert.throws(() => chosenPlacement(list, { place: "branch" }), /Pick the branch/);
  const folder = chosenPlacement(list, { place: "folder" });
  assert.deepEqual([folder.args, folder.place.warning], [["--in-place"], "can conflict"]);
});
