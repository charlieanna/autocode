# Figma integration and concurrent project tasks

Scope: publish the existing Figma UI experiment as an integrated terminal workflow,
and let separate implementation tasks start from the same project without sharing
a writer lock or changing each other's checkout. The demonstration application is
paused; this change does not extend or claim completion of that demonstration.

## Delivered behavior

- `autocode-ui` remains a separate installed command; `autocode ui` is its alias.
- `--build` passes a completed UI run into the existing Autocode implementation
  loop. `--ui-run` imports a saved result; `--figma-file` accepts an existing design.
- Astra, Terra, Sol and Astra use GPT models through Codex and ChatGPT login for
  this workflow. Figma editing uses the connected plugin, without assuming a
  callable Figma Make API.
- Sol must pass and Astra must accept the same Figma URL. Each review attempt is
  saved separately. Handoff artifacts are hashed and checked before import.
- Design failures, dry runs and failed reviews cannot start implementation.
- Figma supplies the visual reference for independent implementation checks.
  Default visual criteria do not add a manual review gate or fabricate human
  approval events. The existing product-brief approval remains in force.
- New coding tasks get unique Git branches/worktrees from committed project HEAD.
  Existing runs keep their checkout, and `--in-place` preserves the old opt-in
  behavior. Same-worktree writer exclusion remains enforced.
- Dashboard launches no longer reserve the original project for new isolated
  tasks. Conversation recovery can find the task's checkpoint in its worktree.

## Verification

Regression coverage includes two real CLI processes starting concurrently from
the same project; independent source files, branches and writer locks; preservation
of dirty parent files; resumption through the original project; dashboard queue
independence and conversation recovery; rejected/tampered handoffs; bounded rework;
ChatGPT routing; and an accepted handoff carried through a complete implementation
loop in its own worktree.

Provider responses in these tests are explicitly fake. They verify orchestration
and recovery, not the aesthetic quality of new live Figma output. No additional
live-model design trial was started for this release.

Commands used:

```sh
python3 -m unittest discover -s tools -t . -p 'test_*.py'
python3 -m unittest discover -s tools/dashboard/tests -p 'test_*.py'
python3 -m unittest tools.test_figma_workflow tools.test_task_workspaces
for file in tools/dashboard/tests/test_*.js; do node "$file" || exit 1; done
python3 -m pip wheel --no-deps .
```

The wheel was installed in a disposable virtual environment; both UI entrypoints,
design dry-run behavior, concurrent CLI launches, and accepted-handoff execution
were checked against the packaged command.

## Practical limits

Task worktrees start from committed HEAD. Ignored dependencies, environment files
and uncommitted project changes are not copied. Worktrees remain available for
review and merging; the runner does not automatically merge or delete them.

Figma plugin access must be available to Codex CLI. Saved hashes protect local
handoff artifacts, not the mutable remote design; implementation roles inspect
the live Figma file again. Interrupted design stages retain their reports and
target URL; recovery uses a fresh design run against that file.
