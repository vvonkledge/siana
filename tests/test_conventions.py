"""The prose conventions in ORDERS.md, as a check rather than as a habit.

Two of them are exact, so they belong in a script: never the em dash, and wrap prose
at 88 columns. A rule nothing enforces is one every new agent has to be told, and the
telling is where it gets dropped. The rest of that section - comments that say why,
minimum code, mechanics apart from judgment - is judgment, and stays in the orders
where an agent reads it.

The column limit is for prose. Command examples and code are fenced or indented, and
wrapping one changes what it does, so those are read past rather than measured. The em
dash has no such exception: there is nowhere in a prose file it belongs.
"""

import os
import unittest

from helpers import DISTRO

LIMIT = 88
EM_DASH = "—"

# Auto-generated files are not written by hand here, so a violation in one is not a
# thing any agent could fix. ORDERS.md says never to edit them at all.
GENERATED = {"CHANGELOG.md"}


def prose_files():
    """Every markdown file in the distro. Discovered rather than listed, because a
    new prose file that nobody remembered to add to a list is exactly the one that
    drifts."""
    for dirpath, dirnames, filenames in os.walk(DISTRO):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".md") and name not in GENERATED:
                yield os.path.join(dirpath, name)


def prose_lines(text):
    """The lines of a markdown document that are prose, as (number, line).

    Fenced blocks are code. So are markdown's indented blocks, but only where
    markdown says one starts: after a blank line. Without that condition a wrapped
    bullet or a continued HTML comment - both indented, neither code - would be read
    past, and the check would go quiet exactly where these templates hold their
    longest prose.
    """
    fenced = False
    in_code = False
    previous_blank = True
    for number, line in enumerate(text.splitlines(), 1):
        blank = not line.strip()
        indented = line.startswith("    ") or line.startswith("\t")
        if line.lstrip().startswith("```"):
            fenced = not fenced
            previous_blank = False
            continue
        in_code = (indented or blank) if in_code else (previous_blank and indented)
        previous_blank = blank
        if fenced or in_code or blank:
            continue
        yield number, line


def report(violations):
    return "\n" + "\n".join(violations)


class Prose(unittest.TestCase):

    def test_the_distro_has_prose_to_check(self):
        # A checker that silently matched nothing would pass forever, and every
        # test below it would be reporting on an empty list.
        self.assertTrue(list(prose_files()))

    def test_no_prose_file_uses_an_em_dash(self):
        found = []
        for path in prose_files():
            with open(path, encoding="utf-8") as fh:
                for number, line in enumerate(fh.read().splitlines(), 1):
                    if EM_DASH in line:
                        rel = os.path.relpath(path, DISTRO)
                        found.append(f"{rel}:{number}: {line.strip()}")
        self.assertEqual(found, [], report(found))

    def test_prose_wraps_at_88_columns(self):
        found = []
        for path in prose_files():
            with open(path, encoding="utf-8") as fh:
                for number, line in prose_lines(fh.read()):
                    # A line with nothing to break on - a bare URL or a long path -
                    # cannot be wrapped, and demanding it be shorter would only be
                    # asking for it to be spelled wrong.
                    if len(line) > LIMIT and " " in line.strip():
                        rel = os.path.relpath(path, DISTRO)
                        found.append(f"{rel}:{number}: {len(line)} columns")
        self.assertEqual(found, [], report(found))


class ProseLines(unittest.TestCase):
    """What counts as prose, checked directly. A rule about which lines are measured
    is worth nothing if the answer is quietly "none of them"."""

    def test_a_fenced_block_is_not_prose(self):
        text = "words\n\n```\na line inside a fence\n```\n"
        self.assertEqual([line for _, line in prose_lines(text)], ["words"])

    def test_an_indented_command_example_is_not_prose(self):
        text = "Run it:\n\n    tasks --file f done id --reason 'x'\n\nafter that\n"
        self.assertEqual([line for _, line in prose_lines(text)],
                         ["Run it:", "after that"])

    def test_an_indented_continuation_is_still_prose(self):
        # No blank line before it, so markdown reads it as the same paragraph and
        # so does this. A bullet wrapped onto a second line lands here.
        text = "<!-- SIANA: what to build.\n     Concrete enough to be checkable. -->\n"
        self.assertEqual([line for _, line in prose_lines(text)],
                         ["<!-- SIANA: what to build.",
                          "     Concrete enough to be checkable. -->"])

    def test_prose_after_a_code_block_is_measured_again(self):
        text = "before\n\n    code\n\nafter\n"
        self.assertEqual([line for _, line in prose_lines(text)], ["before", "after"])


if __name__ == "__main__":
    unittest.main()
