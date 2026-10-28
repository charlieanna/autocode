"""Terminal hints from the public question frontier, without changing JSON output."""


def questions(view: dict) -> str | None:
    """Each pending question with its options and proposed default, as the person reads it (#2325)."""
    need = view.get("needs") or {}
    if need.get("kind") != "answer":
        return None
    lines = ["Pending questions:"]
    for question in need.get("questions") or []:
        identifier = " ".join(str(question.get("id") or "").split())
        text = " ".join(str(question.get("question") or "").split())
        lines.append(f"  {identifier}: {text}")
        options = [" ".join(str(option).split()) for option in question.get("options") or []]
        if options := [option for option in options if option]:
            lines.append(f"      options: {', '.join(options)}")
        if default := " ".join(str(question.get("proposed_default") or "").split()):
            lines.append(f"      proposed default: {default}")
    return "\n".join(lines)


def summary(view: dict, *, after_answer: bool = False) -> str | None:
    """Show the current request after a saved answer or a read-only status call."""
    need = view.get("needs") or {}
    block = questions(view)
    if block is None:
        return "No questions left to answer." if after_answer else None
    lines = [block]
    if token := need.get("resolver_token"):
        lines.append(f"Resolver token: {token}")
    if action := need.get("action"):
        lines.append(f"Response action: {action}")
    if command := view.get("next_command"):
        # The exact command last, so a 2,700-line status payload still ends with the action (#2325).
        lines.append(f"Answer with: {command}")
    return "\n".join(lines)
