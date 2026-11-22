"""Admit a stopped job's next recorded action against its replay authority."""

import copy

try:
    from . import autocode_quota_route as quota_route
    from . import autocode_replay_context as replay_context
    from . import autocode_replay_recording as recording
    from . import autocode_replay_transport as transport
    from . import autocode_review_effort as review_effort
    from . import autocode_role_transport as role_transport
    from . import autocode_route_fit as route_fit
except ImportError:
    import autocode_quota_route as quota_route
    import autocode_replay_context as replay_context
    import autocode_replay_recording as recording
    import autocode_replay_transport as transport
    import autocode_review_effort as review_effort
    import autocode_role_transport as role_transport
    import autocode_route_fit as route_fit


def next_action(state, workspace):
    """Return only the next action explicitly recorded for this stopped attempt."""
    failure = state.get("job_failure") or {}
    question = quota_route.job_question(state)
    tape = transport.selected(state.get("settings") or {})
    index = transport.consumed(state)
    if index >= len(tape.document["dispatches"]):
        raise recording.RecordingError("The recording has no next job dispatch")
    row = tape.document["dispatches"][index]
    group = [a for a in tape.document["actions"] if a["before_dispatch"] == row["id"]]
    if len(group) < 2 or any(a["kind"] != "route_job" for a in group[:-1]) or group[-1]["kind"] != "retry_job":
        raise recording.RecordingError("The recording has no bound job action")
    prior = [
        event
        for event in state.get("user_events") or []
        if event.get("kind") == "route_assignment" and event.get("attempt_id") == failure.get("attempt_id")
    ]
    if len(prior) >= len(group):
        raise recording.RecordingError("The recorded job actions have already been used")
    for event, recorded in zip(prior, group, strict=False):
        payload = recorded["payload"]
        effort = event.get("requested_effort")
        if (
            recorded["kind"] != "route_job"
            or event.get("role") != payload.get("role")
            or event.get("to") != payload.get("model")
            or effort != payload.get("effort")
            or event.get("from") != recorded["match"]["route"]["current_model"]
            or "requested_effort" not in event
            or event.get("actor") != "user_cli"
            or event.get("via") != "answer"
            or event.get("stage") != recorded["match"]["stage"]
            or event.get("engine") != recorded["match"]["route"]["engine"]
            or quota_route.stop_cause(event.get("pause_status")) != recorded["match"]["route"]["cause"]
        ):
            raise recording.RecordingError("A previous job answer differs from its recording")
    action = group[len(prior)]
    if action["match"].get("stage") != failure.get("stage") or row.get("stage") != failure.get("stage"):
        raise recording.RecordingError("The recorded job stage differs")
    if action["kind"] == "retry_job":
        previous = group[len(prior) - 1]
        expected = dict(previous["match"]["route"], current_model=previous["payload"]["model"])
        actual = (
            {
                "role": question.get("route_role"),
                "current_model": question.get("current_model"),
                "engine": question.get("engine"),
                "cause": question.get("cause"),
                "stopped_model": question.get("stopped_model"),
            }
            if isinstance(question, dict)
            else None
        )
        if actual != expected:
            raise recording.RecordingError("The recorded job retry stop differs")
    if action["kind"] == "route_job":
        if not isinstance(question, dict):
            raise recording.RecordingError("The stopped job has no recorded model question")
        route = {
            "role": question.get("route_role"),
            "current_model": question.get("current_model"),
            "engine": question.get("engine"),
            "cause": question.get("cause"),
            "stopped_model": question.get("stopped_model"),
        }
        recording.match_action(
            tape, action, {"needs": {"kind": "retry_job", "stage": failure["stage"], "route": route}}
        )
        actual = (((state.get("settings") or {}).get("roles") or {}).get(route["role"]) or {}).get("model")
        if actual != route["current_model"] or row.get("route_role") != route["role"]:
            raise recording.RecordingError("The recorded job route differs from the current role")
    recording.verify_source(tape, row["before"], workspace)
    return tape, row, action


def check_dispatch(state, candidate, workspace, tape, row):
    """Preflight the same recorded stage/route/source identity the emitter checks."""
    failure = state["job_failure"]
    role = row["route_role"]
    attempts = [
        entry
        for entry in state.get("stages") or []
        if entry.get("output") and quota_route._attempt_id(entry) == failure.get("attempt_id")
    ]
    if len(attempts) != 1:
        raise recording.RecordingError("The stopped job has no unique recorded attempt")
    trial = copy.deepcopy(candidate)
    stage = failure["stage"]
    route = trial["settings"]["roles"][role]
    effort, _ = review_effort.launch(trial, stage, role, False, route)
    effort = route_fit.launch(trial["settings"], role, effort, stage, model=route.get("model"))
    session = role_transport.resume_session(trial, role, stage, False, False, row["supports_sessions"], None)
    context = replay_context.launch(
        trial,
        stage=stage,
        iteration=trial["iteration"],
        report_only=False,
        original_stage=stage,
        changed_code_proof=None,
    )
    transport.match_dispatch(
        tape,
        context,
        role=attempts[0].get("role"),
        route_role=role,
        engine=quota_route.engine(trial["settings"], role),
        model=route.get("model"),
        effort=effort,
        provider=route.get("provider"),
        workspace=workspace,
        session=session,
        allow_write=False,
    )


def answer(state, candidate, workspace, role, model, requested_effort):
    tape, row, action = next_action(state, workspace)
    payload = action["payload"]
    if (
        action["kind"] != "route_job"
        or payload.get("role") != role
        or payload.get("model") != model
        or payload.get("effort") != requested_effort
    ):
        raise recording.RecordingError("The job answer differs from the recording")
    if not payload.get("effort_known") and quota_route.job_question(state):
        failure = state.get("job_failure") or {}
        bound = ((failure.get("configuration") or {}).get("roles") or {}).get(role) or {}
        current = (((state.get("settings") or {}).get("roles") or {}).get(role)) or {}
        if bound.get("reasoning_effort") != current.get("reasoning_effort"):
            raise recording.RecordingError("The recording does not preserve the original effort answer")
    check_dispatch(state, candidate, workspace, tape, row)


def retry(state, workspace):
    tape, row, action = next_action(state, workspace)
    if action["kind"] != "retry_job":
        raise recording.RecordingError("The recording requires a different job action before retry")
    check_dispatch(state, state, workspace, tape, row)
