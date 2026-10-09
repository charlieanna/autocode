"""Protected contract text that a planner revision only wrote differently.

The contract's protected items (required behaviors, scope exclusions, constraints, failure cases, acceptance
criteria) may change only with a saved user answer. Models rewrite a literal backslash-t as a tab and the
reverse, and were then refused for changing protected text (VALIDATION.md, 2026-09-26): a report repair, or a
question to the user, for a change that changed nothing the user asked for. ``restore_spelling`` accepts that
kind of revision by putting the approved text back, so the contract never carries a respelled protected item.

Pure functions over the contract body. Imports nothing from the runner.
"""
from __future__ import annotations

ESCAPES = (("\\t", "\t"), ("\\n", "\n"), ("\\r", "\r"))


def unescaped(text: str) -> str:
    for literal, character in ESCAPES:
        text = text.replace(literal, character)
    return text.strip()


def written_differently(old, new) -> bool:
    """The same text with an escape sequence ("\\t") in place of the character it stands for (a tab), or the
    reverse, and nothing else different. Any other difference is a change of meaning."""
    return isinstance(old, str) and isinstance(new, str) and old != new and unescaped(old) == unescaped(new)


def restore_spelling(previous: dict, body: dict, declared_items, protected_lists) -> None:
    """Put the previous body's exact protected text back into ``body`` wherever the revision only wrote it
    differently. An item the revision declares as changed is left alone, and every other difference still
    needs a saved user answer from the caller's guard."""
    for key in protected_lists:
        rows = body.get(key)
        if not isinstance(rows, list):
            continue
        before = previous.get(key, [])
        for item in before:
            if item in rows or item in declared_items:
                continue
            for index, candidate in enumerate(rows):
                if candidate not in before and candidate not in declared_items and written_differently(item, candidate):
                    rows[index] = item
                    break
    new_by_id = {row.get("id"): row for row in body.get("acceptance_criteria", []) if isinstance(row, dict)}
    for old in previous.get("acceptance_criteria", []):
        new = new_by_id.get(old["id"])
        if new is None or old["id"] in declared_items:
            continue
        fields = ("criterion", "verification_method")
        if all(new.get(field) == old[field] or written_differently(old[field], new.get(field)) for field in fields):
            new.update({field: old[field] for field in fields})
