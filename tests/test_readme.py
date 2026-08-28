"""The README's list of installed commands, checked against the justfile.

`just init` links a set of commands into the bindir, and README.md tells the captain
which ones they are. Two hand-maintained copies of one list drift, and this one
already has: three commands landed and the README kept naming five. Nothing caught
it, because a list is only wrong against another list nobody was comparing it to.

The justfile is the copy that is executed, so it is the authoritative one and this
reads the set out of it rather than repeating it here. A third copy would drift the
same way, and would drift silently, because a test agreeing with itself always passes.
"""

import os
import re
import unittest

from helpers import DISTRO

JUSTFILE = os.path.join(DISTRO, "justfile")
README = os.path.join(DISTRO, "README.md")

# The paragraph in the README that answers "what got installed". Anchored on its
# opening words rather than on a line number, and asserted to still be there: a
# restructure that moves it should turn this suite red and be told what to update,
# which is the one thing the silent drift above never did.
ANCHOR = "Into the bindir"


def linked_commands():
    """The commands `just init` links, read out of the justfile.

    Scoped to the `init` recipe because `uninstall` carries the same list, and the
    installing one is what the README is describing. A recipe ends where the next
    unindented line begins, which is how `just` itself reads one.
    """
    with open(JUSTFILE, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("init:"))
    end = next((i for i, line in enumerate(lines[start + 1:], start + 1)
                if line.strip() and not line[0].isspace()), len(lines))
    body = lines[start:end]
    loop = next(i for i, line in enumerate(body)
                if re.match(r"\s*for c in siana\b", line))
    # The loop that links, not some other loop over `c`. Checked rather than
    # assumed, so a second one added above it cannot quietly take this over.
    assert any("ln -sfn" in line for line in body[loop:]), body[loop]
    names = re.search(r"for c in (.+?);\s*do", body[loop]).group(1)
    return names.split()


def bindir_paragraph():
    """The README paragraph naming what was linked, as one string. Paragraphs are
    blank-line separated, and this one is wrapped, so it is read whole: a name that
    landed on the next line is still named."""
    with open(README, encoding="utf-8") as fh:
        paragraphs = fh.read().split("\n\n")
    return next((p for p in paragraphs if p.startswith(ANCHOR)), None)


class Readme(unittest.TestCase):

    def test_init_links_commands(self):
        # A parser that matched nothing would leave every assertion below it
        # reporting on an empty list, and passing forever.
        self.assertIn("siana", linked_commands())

    def test_the_readme_says_what_init_linked(self):
        self.assertIsNotNone(
            bindir_paragraph(),
            f"no paragraph in README.md starts {ANCHOR!r}; if it moved, point "
            f"ANCHOR in this test at where it went")

    def test_the_readme_names_every_command_init_links(self):
        paragraph = bindir_paragraph() or ""
        # Backticked, because `siana` is a prefix of every other name: a bare
        # substring search would find all eight in the word `siana-dispatch`.
        missing = [c for c in linked_commands() if f"`{c}`" not in paragraph]
        self.assertEqual(missing, [], f"\nnot named in README.md: {missing}")


if __name__ == "__main__":
    unittest.main()
