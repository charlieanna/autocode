"""One language policy for stage instructions, before provider-specific sealing."""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
    from . import autocode_tool_handoff as tool_handoff
except ImportError:
    import autocode_prompts as prompts
    import autocode_tool_handoff as tool_handoff

RULE = prompts.get("fragments/support/response-language.md")


def apply(prompt: str) -> str:
    """Add guidance once without rewriting or trusting the handoff's source text."""
    instructions = prompt.partition(tool_handoff.MARKER)[0]
    if RULE in instructions:
        return prompt
    return tool_handoff.before_handoff(prompt, RULE)
