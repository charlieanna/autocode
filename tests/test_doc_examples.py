"""What a reader copies from the documentation works: links land on a heading and shell examples
keep their arguments (#3861)."""

import functools
import re
import shlex
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md")), *sorted((ROOT / "examples").rglob("*.md"))]
SHELLS = {"sh", "bash", "shell", "zsh", "console"}
FENCE = re.compile(r"^\s*(```+|~~~+)\s*([\w+-]*)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*(?:\{#([\w-]+)\})?\s*#*\s*$")
LINK = re.compile(r"\]\(([^)\s#]*\.md)?#([^)\s]+)\)")


def slugs(title):
    """The anchors a heading gets: MkDocs' (the documentation site) and GitHub's (the repository view)."""
    text = re.sub(r"<[^>]+>|`", "", title).strip().lower()
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    words = re.sub(r"[^\w\s-]", "", text)
    return {re.sub(r"[-\s]+", "-", words.strip()), words.replace(" ", "-")}


def prose(path):
    """(line number, line) outside fenced blocks."""
    fence = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = FENCE.match(line)
        if match and (fence is None or match.group(1).startswith(fence)):
            fence = None if fence else match.group(1)
            continue
        if fence is None:
            yield number, line


def shell_lines(path):
    """(line number, line) inside fenced shell blocks."""
    fence = language = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = FENCE.match(line)
        if match and (fence is None or match.group(1).startswith(fence)):
            fence, language = (None, None) if fence else (match.group(1), match.group(2).lower())
            continue
        if fence and language in SHELLS:
            yield number, line


@functools.cache
def anchors(path):
    found = set()
    for _, line in prose(path):
        match = HEADING.match(line)
        if match:
            found |= {match.group(2)} if match.group(2) else slugs(match.group(1))
        found.update(re.findall(r'<a\s+(?:name|id)="([^"]+)"', line))
    return found


def dropped_words(line):
    """Words a POSIX shell reads as a comment although they look like arguments: `#812`, `#N`.

    A comment that documents a command is `# text`, with a space after the `#`; a word that
    starts with `#` and continues is an argument the reader meant to pass."""
    command = re.sub(r"^\s*\$\s+", "", line)
    if command.lstrip().startswith("#"):
        return []
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.commenters = ""
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return []
    # shlex keeps quotes' contents unquoted, so find the unquoted spelling in the raw line.
    return [word for word in words if re.fullmatch(r"#\S+", word) and re.search(r"(^|\s)" + re.escape(word), command)]


class DocumentationExampleTests(unittest.TestCase):
    def test_every_link_to_a_section_lands_on_a_heading(self):
        broken = []
        for page in PAGES:
            for number, line in prose(page):
                for match in LINK.finditer(line):
                    target = (page.parent / match.group(1)).resolve() if match.group(1) else page
                    if target.is_file() and match.group(2) not in anchors(target):
                        broken.append(f"{page.relative_to(ROOT)}:{number}: {match.group(0)[2:-1]}")
        self.assertEqual([], broken)

    def test_shell_examples_pass_every_argument_they_show(self):
        dropped = []
        for page in PAGES:
            for number, line in shell_lines(page):
                for word in dropped_words(line):
                    dropped.append(f"{page.relative_to(ROOT)}:{number}: {word} starts a comment: {line.strip()}")
        self.assertEqual([], dropped)

    def test_the_checks_notice_what_they_look_for(self):
        self.assertEqual(["#812"], dropped_words("autocode issue brief #812   # no side effects"))
        self.assertEqual(["#812"], dropped_words("$ autocode issue pr #812 --open"))
        self.assertEqual([], dropped_words("autocode issue brief 812   # no side effects"))
        self.assertEqual([], dropped_words("autocode issue brief '#812' OWNER/REPO#812"))
        self.assertEqual([], dropped_words('autocode notes retract x --reason "Fixed in #123"'))
        self.assertEqual([], dropped_words("# a comment line #812"))
        self.assertIn("reviewing-the-plan-in-a-pull-request", slugs("Reviewing the plan in a pull request"))
        self.assertIn("blind-tests-blind-tests", slugs("Blind tests (`--blind-tests`)"))
        self.assertIn("blind-tests---blind-tests", slugs("Blind tests (`--blind-tests`)"))


if __name__ == "__main__":
    unittest.main()
