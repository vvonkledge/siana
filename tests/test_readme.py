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
    """The commands `just init` links, read out of the justfile."""
    with open(JUSTFILE, encoding="utf-8") as fh:
        return parse_linked_commands(fh.read())


def parse_linked_commands(justfile):
    """The commands the `init` recipe of `justfile` links.

    Scoped to the `init` recipe because `uninstall` carries the same list, and the
    installing one is what the README is describing. A recipe ends where the next
    unindented line begins, which is how `just` itself reads one.

    Takes the text rather than reading the path, so the guard below can be checked
    against a justfile that violates it. A guard nothing exercises is how this
    parser came to have one that did not guard.
    """
    lines = justfile.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("init:"))
    end = next((i for i, line in enumerate(lines[start + 1:], start + 1)
                if line.strip() and not line[0].isspace()), len(lines))
    body = lines[start:end]
    loop = next(i for i, line in enumerate(body)
                if re.match(r"\s*for c in siana\b", line))
    # The loop that links, not some other loop over `c`. Scoped to this loop's own
    # body - the lines indented under its `for` - because an `ln -sfn` anywhere later
    # in the recipe would otherwise vouch for a loop that has none, which is precisely
    # how a second loop added above would quietly take this over.
    #
    # The body ends at the first line that is not indented under the `for`, and that
    # line has to be this loop's own `done`. Anything else there is a loop shape this
    # parser cannot scope - closed on the `for` line, or never closed at all - and the
    # failure runs the wrong way round: the scope ends late, not early, swallowing the
    # real loop's `ln -sfn` and passing on the decoy's list. So not finding the `done`
    # is the loud case, asserted before anything asks what the body contains.
    indent = re.match(r"\s*", body[loop]).group()
    close = next((i for i, line in enumerate(body[loop + 1:], loop + 1)
                  if line.strip() and not line.startswith(indent + " ")), None)
    assert close is not None and body[close].strip() == "done", (
        f"the first `for c in siana` loop in `init` is not closed by a `done` at "
        f"its own indent; this parser reads a loop written across lines:\n"
        f"{body[loop]}")
    assert any("ln -sfn" in line for line in body[loop:close]), (
        f"the first `for c in siana` loop in `init` does not link; point this "
        f"parser at the one that does:\n{body[loop]}")
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


class LinkedCommands(unittest.TestCase):
    """The parser's own guard, checked directly. It is meant to refuse a justfile
    whose first `for c in siana` loop is not the linking one, and it landed on main
    quietly accepting exactly that - the real loop further down answered for a decoy
    above it. A guard is worth nothing if the answer is quietly "everything passes".
    """

    LINKING = ('    for c in siana siana-brief; do\n'
               '        ln -sfn "$distro/bin/$c" "$bindir/$c"\n'
               '    done\n')
    DECOY = ('    for c in siana; do\n'
             '        test -f "$distro/bin/$c"\n'
             '    done\n')
    # The same decoy in two formattings whose `done` is not a bare line at the `for`'s
    # own indent. Both used to leave the scope running on into the linking loop below.
    ONE_LINE_DECOY = '    for c in siana; do test -f "$distro/bin/$c"; done\n'
    TRAILING_SPACE_DECOY = ('    for c in siana; do\n'
                            '        test -f "$distro/bin/$c"\n'
                            '    done \n')

    def recipe(self, *blocks):
        return "init:\n" + "".join(blocks) + "\nnext-recipe:\n    true\n"

    def test_it_reads_the_names_off_the_linking_loop(self):
        commands = parse_linked_commands(self.recipe(self.LINKING))
        self.assertEqual(commands, ["siana", "siana-brief"])

    def test_a_loop_that_does_not_link_is_refused(self):
        # The regression: the decoy is measured instead, and the linking loop below
        # vouches for it, so the README gets checked against one name in silence.
        with self.assertRaises(AssertionError):
            parse_linked_commands(self.recipe(self.DECOY, self.LINKING))

    def test_a_loop_closed_on_its_own_line_is_refused(self):
        # `justfile:228` writes a short loop on one line, so this is a formatting the
        # repository uses today. Its `done` never appears at the `for`'s indent, so
        # the scope ran on to the linking loop below and was vouched for by it.
        with self.assertRaisesRegex(AssertionError, "not closed by a `done`"):
            parse_linked_commands(self.recipe(self.ONE_LINE_DECOY, self.LINKING))

    def test_a_done_with_trailing_whitespace_still_ends_the_body(self):
        # One space after `done` is invisible in review and to `test_conventions`,
        # and it used to widen the scope over the linking loop. The decoy is refused
        # here for the reason it is actually wrong: it does not link.
        with self.assertRaisesRegex(AssertionError, "does not link"):
            parse_linked_commands(self.recipe(self.TRAILING_SPACE_DECOY, self.LINKING))


if __name__ == "__main__":
    unittest.main()
