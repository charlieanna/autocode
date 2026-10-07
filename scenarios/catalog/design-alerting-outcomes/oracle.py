"""Issue #450: a monitoring design that asks the person about outcomes and constraints and recommends the
mechanisms itself. See brief.md for the document's shape and scenario.toml for the person's answers.

The document is judged on its own (``check`` mode too): the person's rules are kept as theirs, every mechanism
choice offers options with tradeoffs and a recommendation that stays a recommendation, identities such as the
channel are parameters rather than blockers, the reliability promises follow what the person decided, and
nothing is deployed. No particular AWS architecture is required: any detection and any delivery mechanism
pass, as long as they are recommended with their alternatives.

With a run record it also judges how the run got there, from the questions the driver answered and the plan
the person approved (the status view's ``approved_contract``): Requirements asked about outcomes and
constraints, nobody asked the person to pick a mechanism (nor re-asked once they had no preference), the
unsupported guarantees stayed blocking questions until answered, the person approved the exact plan, and
that plan keeps its recommendations as proposals and authorizes no deployment.
"""
import json
import re
import tomllib

from harness.oracle import Check, load_json, only_changed_under, run_checks

KEYS = ("outcome_rules", "mechanisms", "parameters", "open_blockers", "reliability", "assumptions", "deployment")
# Named mechanisms a person should be recommended rather than asked to pick between. The dead-letter queue
# itself (SQS) is the person's own system, not a choice, so it is not listed.
MECHANISMS = {"webhook": r"web ?hooks?", "chat:write": r"chat:write", "slack app": r"slack app\b",
              "chatbot": r"chatbot|amazon q developer", "sns": r"\bsns\b|simple notification service",
              "lambda": r"\blambda\b", "eventbridge": r"eventbridge", "cloudwatch": r"cloudwatch",
              "step functions": r"step functions", "api gateway": r"api gateway"}
# Identities a provisional design takes as configuration (issue #450: parameters, not blockers).
IDENTITY = re.compile(r"\b(channel|account(?: id)?|region|arn|queue (?:name|url))\b", re.I)
NEGATED_DEPLOYMENT = re.compile(r"\b(no|not|never|without)\b[^.;]*\b(deploy\w*|provision\w*)"
                                r"|\b(deploys?|provisions?)\s+nothing\b", re.I)
GRANTED_DEPLOYMENT = re.compile(r"\b(may|can|allowed to|authori[sz]ed to)\s+(deploy|provision)\b", re.I)


def named_mechanisms(text) -> set:
    lowered = (text if isinstance(text, str) else json.dumps(text)).lower()
    return {name for name, pattern in MECHANISMS.items() if re.search(pattern, lowered)}


def person_words(scenario, run) -> str:
    """What the person said: the brief and their answers (the run's, else the scenario's own answers)."""
    if run is not None:
        answers = [str(row.get("answer", "")) for row in run.get("answers") or []]
    else:
        meta = tomllib.loads((scenario.dir / "scenario.toml").read_text())
        answers = list(((meta.get("fake") or {}).get("answers") or {}).values())
    return " ".join([scenario.brief, *answers])


def check(project, scenario, run=None):
    checks = []
    design, error = load_json(project / "design" / "alerting.json")
    present = isinstance(design, dict) and all(key in design for key in KEYS)
    checks.append(Check("design_document_present", present,
                        error or ("" if present else f"needs the keys {list(KEYS)}")))
    checks.append(only_changed_under(project, "design/"))
    if present:
        checks += document_checks(design, person_words(scenario, run))
    return checks + process_checks(run)


def document_checks(design, words):
    checks = []
    rules = [row for row in design.get("outcome_rules") or [] if isinstance(row, dict)]
    theirs = [row for row in rules if row.get("source") == "user"]
    invented = sorted({number for row in theirs for number in re.findall(r"\d+", str(row.get("rule", "")))}
                      - set(re.findall(r"\d+", words)))
    checks.append(Check("outcome_rules_are_the_persons", bool(theirs) and not invented,
                        f"numbers the person never gave: {invented}" if invented else
                        ("" if theirs else "no outcome rule is recorded as the person's")))

    entries = [row for row in design.get("mechanisms") or [] if isinstance(row, dict)]
    weak = []
    for row in entries:
        options = [option for option in row.get("options") or [] if isinstance(option, dict)]
        names = [str(option.get("name", "")).strip() for option in options]
        if (len(options) < 2 or not all(names)
                or not all(str(option.get("tradeoffs", "")).strip() for option in options)
                or row.get("recommended") not in names):
            weak.append(row.get("choice"))
    slack = [row for row in entries if "slack" in json.dumps([row.get("choice"), row.get("options")]).lower()]
    checks.append(Check("mechanisms_recommended_with_tradeoffs", bool(entries) and not weak and bool(slack),
                        f"choices without two options with tradeoffs and a recommended one: {weak}" if weak else
                        ("" if slack else "no choice of how alerts reach Slack")))

    free = [row for row in entries if row.get("binding_constraint") is False]
    decided = [row.get("choice") for row in free if row.get("basis") != "agent_proposed"]
    bound = [row.get("choice") for row in slack if row.get("binding_constraint") is not False]
    assumptions = [row for row in design.get("assumptions") or [] if isinstance(row, dict)]
    claimed = [row.get("text") for row in assumptions if row.get("basis") != "agent_proposed"
               and any(str(entry.get("recommended", "")).lower() in str(row.get("text", "")).lower()
                       for entry in free if entry.get("recommended"))]
    checks.append(Check("recommendations_are_not_the_persons_decisions", not decided and not bound and not claimed,
                        "; ".join(filter(None, [
                            f"recommended without a binding constraint but recorded as decided: {decided}" if decided else "",
                            f"the person named no restriction, yet bound: {bound}" if bound else "",
                            f"recommendations presented as the person's answers: {claimed}" if claimed else ""]))))

    parameters = [row for row in design.get("parameters") or [] if isinstance(row, dict)]
    channel = any("channel" in json.dumps(row).lower() for row in parameters)
    blocking = [text for text in design.get("open_blockers") or [] if IDENTITY.search(str(text))]
    checks.append(Check("identities_are_parameters_not_blockers", channel and not blocking,
                        f"identities held as blockers: {blocking}" if blocking else
                        ("" if channel else "the Slack channel is not a parameter")))

    reliability = design.get("reliability") if isinstance(design.get("reliability"), dict) else {}
    delivery = str(reliability.get("delivery", ""))
    latency = reliability.get("latency") if isinstance(reliability.get("latency"), dict) else {}
    exact = bool(re.search(r"exactly[- ]once", delivery, re.I))
    tradeoff = bool(re.search(r"\b(twice|duplicates?|again|retr\w*|miss\w*)\b", delivery, re.I))
    kind_ok = latency.get("kind") == "healthy_path_target" or bool(re.search(r"guarantee,? even during", words, re.I))
    checks.append(Check("reliability_promises_follow_the_persons_decisions", tradeoff and not exact and kind_ok,
                        "; ".join(filter(None, [
                            "promises exactly-once delivery" if exact else "",
                            "" if tradeoff else "does not say whether an uncertain delivery may repeat or miss an alert",
                            "" if kind_ok else f"latency kind {latency.get('kind')!r}, but the person asked for a "
                                               "healthy-path target"]))))

    deployment = design.get("deployment") if isinstance(design.get("deployment"), dict) else {}
    checks.append(Check("design_authorizes_no_deployment", deployment.get("authorized") is False,
                        f"deployment.authorized is {deployment.get('authorized')!r}"))

    recommended = [str(row["recommended"]) for row in free if row.get("recommended")]
    proposals = " ".join(str(row.get("text", "")).lower() for row in assumptions if row.get("basis") == "agent_proposed")
    bases = sorted({str(row.get("basis")) for row in assumptions} - {"agent_proposed", "user_answer"})
    unstated = [name for name in recommended if name.lower() not in proposals]
    checks.append(Check("assumptions_are_explicit", bool(assumptions) and not bases and not unstated,
                        f"unknown bases {bases}" if bases else
                        (f"recommendations not stated as proposed assumptions: {unstated}" if unstated else "")))
    return checks


def process_checks(run):
    """How the run reached the document; nothing in ``check`` mode (run is None)."""
    if run is None:
        return []
    checks = run_checks(run, workflow="design", plan_approved=True)
    answers = [row for row in run.get("answers") or [] if isinstance(row, dict)]
    asked = [" ".join([str(row.get("question", "")), *map(str, row.get("options") or [])]) for row in answers]

    condition = any(re.search(r"\balert", text, re.I) and re.search(r"\b(age|older|threshold|number of|any message|"
                                                                    r"count as)\b", text, re.I) for text in asked)
    destination = any(re.search(r"\bchannel\b", text, re.I) and re.search(r"\b(existing|restriction)", text, re.I)
                      for text in asked)
    checks.append(Check("requirements_asked_about_outcomes_and_constraints", condition and destination,
                        "; ".join(filter(None, ["" if condition else "never asked what should trigger an alert",
                                                "" if destination else "never asked which channel and whether an "
                                                                       "existing integration or restriction binds"]))))

    offered = [row.get("id") for row in answers
               if named_mechanisms(row.get("options") or []) or len(named_mechanisms(str(row.get("question", "")))) >= 2]
    checks.append(Check("no_mechanism_put_to_the_person", not offered,
                        f"questions offering mechanisms to choose from: {offered}" if offered else ""))

    free = next((index for index, row in enumerate(answers)
                 if re.search(r"no (preference|restriction)", str(row.get("answer", "")), re.I)), None)
    reasked = [row.get("id") for row in answers[free + 1:] if named_mechanisms(row)
               or re.search(r"\b(integration|api)\b", str(row.get("question", "")), re.I)] if free is not None else []
    checks.append(Check("no_mechanism_reasked_after_no_preference", free is not None and not reasked,
                        f"re-asked after the person had no preference: {reasked}" if reasked else
                        ("" if free is not None else "the person was never asked about restrictions or preferences")))

    duplicate = any(re.search(r"\b(twice|duplicate|again)\b", text, re.I) and re.search(r"\bmiss", text, re.I)
                    for text in asked)
    deadline = any(re.search(r"\btarget\b", text, re.I) and re.search(r"\bguarantee", text, re.I) for text in asked)
    steps = [step.get("kind") for step in run.get("steps") or []]
    last_answer = max((index for index, kind in enumerate(steps) if kind == "answer"), default=-1)
    approval = steps.index("approve-plan") if "approve-plan" in steps else -1
    checks.append(Check("unsupported_guarantees_were_blockers", duplicate and deadline and last_answer < approval,
                        "; ".join(filter(None, [
                            "" if duplicate else "an uncertain delivery (repeat or miss) was never put to the person",
                            "" if deadline else "never asked whether the deadline is a target or a guarantee",
                            "" if last_answer < approval else "the plan was approved before the questions were answered"]))))

    body = ((run.get("view") or {}).get("approved_contract") or {}).get("body") or {}
    approach = " ".join(map(str, body.get("technical_approach") or []))
    recommends = bool(re.search(r"\brecommend", approach, re.I)) and len(named_mechanisms(approach)) >= 2
    checks.append(Check("approved_plan_recommends_mechanisms", recommends,
                        "" if recommends else "the approved plan names no recommended mechanism with its alternatives"))
    rows = [row for field in ("accepted_assumptions", "delegated_decisions") for row in body.get(field) or []
            if isinstance(row, dict) and named_mechanisms(str(row.get("text", "")))]
    decided = [row.get("text") for row in rows if row.get("basis") != "agent_proposed"]
    checks.append(Check("approved_plan_keeps_recommendations_as_proposals", bool(rows) and not decided,
                        f"recorded as the person's decision: {decided}" if decided else
                        ("" if rows else "the approved plan records no mechanism recommendation as an assumption")))
    # Said in the constraints or the permission boundaries; neither may grant it.
    limits = [str(text) for field in ("constraints", "permission_boundaries") for text in body.get(field) or []]
    denied = any(NEGATED_DEPLOYMENT.search(text) for text in limits)
    granted = [text for text in limits if GRANTED_DEPLOYMENT.search(text)]
    checks.append(Check("approved_plan_authorizes_no_deployment", denied and not granted,
                        f"grants deployment: {granted}" if granted else
                        ("" if denied else f"nothing in the approved plan says it deploys nothing: {limits}")))
    return checks
