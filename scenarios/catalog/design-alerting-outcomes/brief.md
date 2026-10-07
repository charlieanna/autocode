Design how we should monitor the dead-letter queue of our AWS order pipeline and alert the team in Slack when something goes wrong.

Deliver only `design/alerting.json`, a JSON object with these keys:

- `outcome_rules`: the alert rules we agree on, each `{"rule": text, "source": "user" or "agent_proposed"}`.
- `mechanisms`: one entry per technical choice the design makes, each `{"choice": what is being decided, "options": [{"name": text, "tradeoffs": text}, ...], "recommended": the name of one option, "basis": "agent_proposed" or "user_decision", "binding_constraint": true or false}`.
- `parameters`: values the design takes as configuration instead of deciding them, each `{"name": text, "description": text}`.
- `open_blockers`: facts still missing before this design could be deployed, each a string.
- `reliability`: `{"delivery": what happens when it is uncertain whether a Slack alert was delivered, "latency": {"target": text, "kind": "healthy_path_target" or "guarantee"}}`.
- `assumptions`: every assumption the design makes, each `{"text": text, "basis": "agent_proposed" or "user_answer"}`.
- `deployment`: `{"authorized": true or false, "note": text}`.
