"""A terminal-screen test waits for everything it then asserts (AGENTS.md, "Before you push").

tmux shows half-drawn frames between a key and curses' redraw, so a test that waits for one string
and asserts another passes on an idle machine and fails on a loaded CI runner. #4167's CI failed
``tests.test_chat_attention_screen`` that way (2026-11-25): it waited for the ATTENTION header and
asserted the acknowledged state drawn after it. ``TerminalScreen.wait_for_all`` waits for them all.
"""

import ast
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
LOOKAHEAD = 3  # statements after the wait that read the same screen text


def _waited_text(node):
    """(name, text) for ``name = <screen>.wait_for("text")``, else None."""
    if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
        return None
    call = node.value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "wait_for"):
        return None
    if not call.args or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
        return None
    return node.targets[0].id, call.args[0].value


def _asserted_text(node, name):
    """The literal a ``self.assertIn("text", name)`` checks on the waited screen, else None."""
    if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
        return None
    call = node.value
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "assertIn" and len(call.args) >= 2):
        return None
    text, screen = call.args[:2]
    if isinstance(text, ast.Constant) and isinstance(text.value, str) and isinstance(screen, ast.Name):
        return text.value if screen.id == name else None
    return None


def races(source: str) -> list[tuple[int, str, str]]:
    """(line, waited, asserted) for each assertion on text the wait just before it did not wait for."""
    found = []
    for parent in ast.walk(ast.parse(source)):
        for field in ("body", "orelse", "finalbody"):
            body = getattr(parent, field, None)
            if not isinstance(body, list):
                continue
            for index, node in enumerate(body):
                waited = _waited_text(node)
                if waited is None:
                    continue
                name, text = waited
                for later in body[index + 1 : index + 1 + LOOKAHEAD]:
                    asserted = _asserted_text(later, name)
                    if asserted is not None and asserted not in text:
                        found.append((node.lineno, text, asserted))
    return found


class TerminalScreenWaitTests(unittest.TestCase):
    def test_a_wait_covers_the_text_asserted_on_its_screen(self):
        found = [
            f"{path.name}:{line}: waits for {waited!r}, then asserts {asserted!r}"
            for path in sorted(TESTS.glob("test_*.py"))
            for line, waited, asserted in races(path.read_text())
        ]
        self.assertEqual([], found, "wait with screen.wait_for_all(...) for every asserted text")

    def test_the_check_finds_the_race_and_accepts_its_fix(self):
        race = 'def test(self):\n    shown = screen.wait_for("ATTENTION")\n    self.assertIn("0 unread", shown)\n'
        self.assertEqual([(2, "ATTENTION", "0 unread")], races(race))
        fixed = race.replace('wait_for("ATTENTION")', 'wait_for_all("ATTENTION", "0 unread")')
        self.assertEqual([], races(fixed))
        same = 'def test(self):\n    shown = screen.wait_for("1 unread item")\n    self.assertIn("1 unread", shown)\n'
        self.assertEqual([], races(same))


if __name__ == "__main__":
    unittest.main()
