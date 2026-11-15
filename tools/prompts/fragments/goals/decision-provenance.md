Decision provenance is mandatory for every contract:
- Explicit requirements and corrections inside task/original conversation context use
  basis=original_request with answer_id="". Conversation IDs and message labels are not saved feedback IDs.
- Use basis=user_answer only for IDs present in saved_answers/answers; delegated
  requires a saved answer explicitly marked delegated.
- Use basis=user_feedback only for an exact saved brief_feedback event ID that also
  exists in user_events. If no such event exists, do not use user_feedback.
- Your suggestions and model-written drafts are basis=agent_proposed, answer_id="";
  they are not user approval. Never invent an answer ID or a feedback event.
- Every accepted_assumptions and delegated_decisions row names its category and a
  convention_ref ("" when none). An agent_proposed row may only be category=technical, an
  engineering convention with convention_ref citing the repository path:line you read that
  establishes it; the runner checks that read. A user-visible behavior, or any cost, quota,
  permission, external side effect or requested outcome, is never agent_proposed: put it in
  open_blocking_questions with options and a recommended proposed_default.
- delegated_decisions may contain ONLY basis=delegated rows tied to actual saved
  delegated answers. Otherwise return delegated_decisions=[]. Put proposed defaults
  (layout, file structure, question counts, etc.) in accepted_assumptions with
  basis=agent_proposed, not in delegated_decisions.
