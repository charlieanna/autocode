"""Render Requirements instructions against the exact model-facing schema."""

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


def render(template: str, response_schema: dict) -> str:
    conversational = "assistant_reply" in response_schema.get("properties", {})
    return template.format(
        conversation_intro=(
            prompts.get("fragments/autoplanner/requirements-conversation.md") if conversational else ""
        ),
        reply_prefix="assistant_reply and " if conversational else "",
        confirmation_target="assistant_reply" if conversational else "summary",
    )
