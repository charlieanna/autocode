"""Terminal hints from the public question frontier, without changing JSON output."""


def summary(view: dict, *, after_answer: bool = False) -> str | None:
    """Show the current request after a saved answer or a read-only status call."""
    need = view.get("needs") or {}
    if need.get("kind") != "answer":
        return "No questions left to answer." if after_answer else None
    lines = ["Pending questions:"]
    for question in need.get("questions") or []:
        identifier = " ".join(str(question.get("id") or "").split())
        text = " ".join(str(question.get("question") or "").split())
        lines.append(f"  {identifier}: {text}")
    if token := need.get("resolver_token"):
        lines.append(f"Resolver token: {token}")
    if action := need.get("action"):
        lines.append(f"Response action: {action}")
    return "\n".join(lines)
