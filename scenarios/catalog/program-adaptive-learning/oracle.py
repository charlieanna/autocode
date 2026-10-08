from harness.oracle import Check, hidden_tests, program_checks, python_tests, scratch_copy, tail


def check(project, scenario, run=None):
    with scratch_copy(project) as copy:
        delivered = python_tests(copy)
        hidden = hidden_tests(copy, scenario.dir / "hidden")
    checks = [Check("delivered_tests_pass", delivered.returncode == 0 and "Ran 0 tests" not in delivered.stderr, tail(delivered)),
              Check("independent_learning_journeys", hidden.returncode == 0, tail(hidden))]
    if run is None:
        return checks
    rows = {row["id"]: row for row in run.get("program", {}).get("workstreams", [])}
    ids, children = run.get("workstream_ids") or {}, run.get("children") or {}
    engine, backend = ids.get("ENGINE", "ENGINE"), ids.get("BACKEND", "BACKEND")
    expected_rule = "getting a problem right with a hint must count differently from getting it right alone"
    for wid in (engine, backend):
        active = (children.get(wid) or [{}])[-1]
        criterion = next((row for row in (active.get("approved_contract") or {}).get("body", {}).get("acceptance_criteria", [])
                          if row.get("id") == "C_HINT"), {})
        checks.append(Check(f"hint_rule_kept[{wid}]", rows.get(wid, {}).get("status") == "MERGED"
                            and rows.get(wid, {}).get("plan_check", {}).get("dropped") == []
                            and " ".join(criterion.get("criterion", "").split()).casefold().removesuffix(".") == expected_rule))
    skeleton = ids.get("S", "S")
    skeleton_runs = children.get(skeleton) or []
    prior = (rows.get(skeleton, {}).get("retired_runs") or [{}])[0].get("accepted_source") or {}
    active = (skeleton_runs or [{}])[-1]
    reviewed = [row for row in active.get("usage", {}).get("accounting", {}).get("attempts", [])
                if row.get("stage") in ("terra", "sol", "astra_review")]
    source = prior.get("snapshot", {}).get("revision")
    checks.append(Check("forced_no_op_recheck_freshly_reviewed", len(skeleton_runs) == 2
                        and (active.get("approved_contract") or {}).get("body", {}).get("initial_task", {}).get("kind") == "implement"
                        and active.get("status") == "TASK_COMPLETE" and [row["stage"] for row in reviewed] == ["terra", "sol", "astra_review"]
                        and all(row.get("source_revision") == source and not row.get("rejected") for row in reviewed)
                        and source is not None and active.get("evidence", {}).get("validator_source_revision") == source
                        and active.get("evidence", {}).get("check_replay", {}).get("verdict") == "PASS"))
    checks.append(Check("engine_and_backend_are_separate_TaskRuns", bool(children.get(engine)) and bool(children.get(backend))
                        and children[engine][-1]["run_dir"] != children[backend][-1]["run_dir"]
                        and children[engine][-1].get("workflow") == children[backend][-1].get("workflow") == "build"))
    revision = (run.get("program", {}).get("agreement") or {}).get("revision")
    affected = [wid for wid, row in rows.items() if (row.get("pin") or {}).get("revision") == revision]
    checks.append(Check("parent_revision_selectively_rechecks_one_child", revision == 3 and affected == [engine], str(affected)))
    checks.append(Check("simulation_limit_disclosed", any("real students learn better" in line for line in
                        (run.get("program", {}).get("final_check") or {}).get("not_proven", []))))
    return checks + program_checks(run, scenario)
