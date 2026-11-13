import assert from "node:assert/strict";
import test from "node:test";

import { chosenPlacement, lacksPlaceList, placementArgs, readWorktrees } from "../src/core/worktrees";

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

test("only an AutoCode without the command counts as having no place list", () => {
  const old = "usage: autocode [TASK] [OPTIONS]\nautocode: error: unrecognized arguments: list. If this was the task, put all of it in quotes";
  assert.equal(lacksPlaceList({ code: 2, stderr: old }), true);
  assert.equal(lacksPlaceList({ code: 1, stderr: "autocode did not answer within 60 s" }), false, "a timeout");
  assert.equal(lacksPlaceList({ code: null, stderr: "" }), false, "a killed read");
  assert.equal(lacksPlaceList({ code: 2, stderr: "Input rejected: not a Git checkout" }), false, "the command's own refusal");
  assert.equal(lacksPlaceList({ code: 2, stderr: "usage: autocode worktrees list [-h]\nautocode worktrees list: error: unrecognized arguments: --x" }), false);
});

test("controller admission blocks every placement and keeps only the exact named setup action", () => {
  const controller = { ready: false, detail: "Select an isolated CLI before starting.", actions: [
    { id: "select_cli", label: "Select isolated AutoCode CLI",
      next_command: ["worktrees", "list", "--workspace", "/p", "--json"] },
  ] };
  const list = readWorktrees(JSON.stringify({ ...LIST, controller }));
  assert.deepEqual(list.controller, {
    ready: false, detail: controller.detail, action: {
      label: controller.actions[0]!.label, nextCommand: controller.actions[0]!.next_command,
    },
  });
  for (const place of ["new_branch", "branch", "folder"]) {
    assert.throws(() => chosenPlacement(list, { place, branch: "feature" }), /Select an isolated CLI/);
  }
  const hostile = readWorktrees(JSON.stringify({ ...LIST, controller: {
    ...controller, actions: [{ ...controller.actions[0], next_command: ["setup", "--install", "opencode"] }],
  } }));
  assert.equal(hostile.controller!.action, null, "never run an invented setup command");
  assert.throws(() => readWorktrees(JSON.stringify({ ...LIST, controller: { ready: "yes" } })), /controller setup/);
  assert.equal(readWorktrees(JSON.stringify(LIST)).controller, undefined, "older cores still list placements");
});
