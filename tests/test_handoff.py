"""The document a merge request is made of, and everything it refuses to be.

The copy a human reads is written by the minion that did the work, because only that
agent understood both what was asked and what was built. What is exact is here: the
shape of that document, and how its sections become one title and one body. Nothing
in it reads a diff or asks a model, so every rule below is one a script can state and
a reviewer can check.

Each refusal is a way copy could reach a forge without describing the work: absent,
still carrying its scaffolding, half written, describing a commit the branch has
moved past, or quoting a path that only exists on the captain's machine.
"""

import os
import re
import unittest

from helpers import HomeTest, script

handoff = script("siana-handoff")

HEAD = "a" * 40
OTHER = "b" * 40

FILLED = """# Handoff

    title  Stop a review reading a diff nobody validated
    head   {head}

## Intent

Reviews were run against the target's current tip, so an unrelated merge widened
the diff and the reviewer read work nobody had asked about.

## Solution

The base is taken from the merge base of the recorded target, and an explicit base
the head is not descended from is refused rather than silently widened.

## Validation

`just test` passes, and the pipeline was rerun against a target that had moved.

## Hotspots

`bin/siana-pipeline`, where the base is chosen: a task that names no base now
measures from where it forked, and that is the path a legacy task takes.

## Risks and boundaries

A branch rebased mid-review still has to be rerun. Nothing here changes what a
review costs.
"""


def printed(block):
    """A block of the body as the command prints it: every line behind two spaces,
    the blank ones included.

    Asserted to the character where a defect was prose going missing between the
    document and the forge. That is the one failure nothing downstream can catch,
    because what arrives still reads like something somebody wrote."""
    return "".join(f"  {line}\n" for line in block.splitlines())


def filled(head=HEAD, **replace):
    text = FILLED.format(head=head)
    for old, new in replace.items():
        text = text.replace(old.replace("_", " "), new)
    return text


class Parsing(unittest.TestCase):
    """The two readers, driven directly. A rule about which lines are read is worth
    nothing if the answer is quietly "none of them"."""

    def test_the_preamble_stops_at_the_first_section(self):
        # The keyed lines are read from here and nowhere else. Without the stop, a
        # `Solution` section describing what the change titles something would be
        # published as the title of the merge request.
        text = "# Handoff\n\n    title  A\n\n## Intent\n\n    title  B\n"
        self.assertEqual(handoff.keyed(handoff.preamble(text), "title"), ["A"])

    def test_sections_come_back_in_order_with_the_guidance_stripped(self):
        text = ("## Intent\n\n<!-- what to write here -->\nthe problem.\n\n"
                "## Solution\n\nwhat was done.\n")
        self.assertEqual(handoff.sections(text),
                         [("Intent", "the problem."), ("Solution", "what was done.")])

    def test_prose_beside_a_comment_keeps_the_prose(self):
        # The inherited stripper dropped the whole line whenever `<!--` appeared on
        # it, so a sentence with a note written at the end of it left the document
        # without a word said about either.
        text = ("## Hotspots\n\nThe parser drops a line like this. <!-- a note -->\n"
                "This line survives.\n")
        self.assertEqual(
            handoff.sections(text),
            [("Hotspots",
              "The parser drops a line like this.\nThis line survives.")])

    def test_prose_after_a_comment_on_the_same_line_is_kept_too(self):
        text = "## Hotspots\n\nbefore <!-- a note --> after\n"
        self.assertEqual(handoff.sections(text), [("Hotspots", "before after")])

    def test_a_comment_spanning_lines_keeps_both_ends_apart(self):
        # Two lines and not one: prose the author wrote on separate lines is not
        # merged by a comment happening to run between them.
        text = "## Hotspots\n\nbefore <!-- a\nb --> after\n"
        self.assertEqual(handoff.sections(text), [("Hotspots", "before\nafter")])

    def test_a_heading_inside_a_fence_is_body(self):
        # The example is what the section is about. This distro's work is mostly the
        # markdown other agents are briefed with, so a handoff that could not quote a
        # heading could not say what it changed.
        text = "## Solution\n\n```markdown\n## Intent\n\nwhat was wrong.\n```\n"
        self.assertEqual(
            handoff.sections(text),
            [("Solution", "```markdown\n## Intent\n\nwhat was wrong.\n```")])

    def test_a_tilde_fence_is_a_fence(self):
        text = "## Solution\n\n~~~markdown\n## Intent\n~~~\n\nafter.\n"
        found = handoff.sections(text)
        self.assertEqual([h for h, _ in found], ["Solution"])
        self.assertTrue(found[0][1].endswith("after."))

    def test_a_fence_closes_on_its_own_marker_at_its_own_length(self):
        # A longer run is how one fence quotes another, so the three-backtick lines
        # inside a five-backtick block are content and neither of them ends it.
        text = "## Solution\n\n`````\n```\n## Intent\n```\n`````\n\nafter.\n"
        found = handoff.sections(text)
        self.assertEqual([h for h, _ in found], ["Solution"])
        self.assertTrue(found[0][1].endswith("after."))

    def test_a_sentence_opening_with_a_code_span_is_not_a_fence(self):
        # Markdown's own rule: a backtick fence's info string may not hold a
        # backtick. Without it, prose that starts with a quoted piece of code opens a
        # block nothing closes, and the document is refused for a fence it never had.
        text = "## Hotspots\n\n```json``` is the shape a caller reads.\n"
        self.assertEqual(handoff.sections(text),
                         [("Hotspots", "```json``` is the shape a caller reads.")])

    def test_a_comment_may_hold_a_heading(self):
        # Guidance about the sections is guidance, not a section. It is removed with
        # the rest of the comment rather than splitting the document.
        text = "## Hotspots\n\n<!-- say why, as in\n## Intent\n-->\nreal prose.\n"
        self.assertEqual(handoff.sections(text), [("Hotspots", "real prose.")])

    def test_a_repeated_section_is_kept_as_two(self):
        # Folded into one, whichever of them travelled would be a choice made for
        # the author by a dict.
        text = "## Hotspots\n\nfirst.\n\n## Hotspots\n\nsecond.\n"
        self.assertEqual([h for h, _ in handoff.sections(text)],
                         ["Hotspots", "Hotspots"])


class Assembly(unittest.TestCase):

    def body(self, **kw):
        return handoff.assemble({h: f"{h} prose." for h in handoff.SECTIONS}, **kw)

    def test_the_sections_are_published_in_one_fixed_order(self):
        # Document order would make the body depend on how the author happened to
        # type it, and a reader's questions come in a fixed order.
        text = self.body()
        found = re.findall(r"^## (.+)$", text, re.M)
        self.assertEqual(found, handoff.SECTIONS)

    def test_the_authors_own_copy_carries_no_fleet_trace(self):
        # What a minion sees while it is still writing. The review has not happened
        # and the QA task does not exist yet.
        text = self.body()
        self.assertNotIn("Independently reviewed", text)
        self.assertNotIn("Shipped by", text)

    def test_publishing_adds_the_review_and_the_ids(self):
        text = self.body(ship_id="add-json", qa_id="qa-add-json")
        self.assertIn("Independently reviewed and accepted by a second agent", text)
        self.assertIn("Shipped by `add-json`, accepted by `qa-add-json`.", text)
        # Inside `Validation`, because that is the section a reader asks the
        # question in, and after the author's own evidence rather than instead of it.
        validation = text.split("## Validation", 1)[1].split("## Hotspots", 1)[0]
        self.assertIn("Validation prose.", validation)
        self.assertIn("Independently reviewed", validation)


class Refusals(HomeTest):
    """Each of these is copy that could reach a forge without describing the work."""

    def write(self, text, task_id="use-target-merge-base"):
        os.makedirs(self.at("handoffs"), exist_ok=True)
        with open(self.at("handoffs", f"{task_id}.md"), "w") as fh:
            fh.write(text)
        return task_id

    def check(self, text, *args, task_id="use-target-merge-base"):
        self.write(text, task_id)
        return self.run_bin("siana-handoff", task_id, *args)

    def test_a_well_formed_handoff_is_accepted(self):
        # A suite of refusals with nothing that passes would go green on a validator
        # that refused everything.
        text = self.assertAccepted(self.check(filled(), "--head", HEAD))
        self.assertIn("Stop a review reading a diff nobody validated", text)

    def test_no_handoff_at_all(self):
        out = self.run_bin("siana-handoff", "use-target-merge-base")
        self.assertRefused(out, "no handoff for use-target-merge-base", "--scaffold")

    def test_a_scaffold_nobody_filled_in(self):
        self.template("handoff.md")
        self.run_bin("siana-handoff", "use-target-merge-base", "--scaffold")
        out = self.run_bin("siana-handoff", "use-target-merge-base")
        self.assertRefused(out, "{TITLE}", "looks like a")

    def test_no_title(self):
        out = self.check(filled().replace("    title  ", "    called  "))
        self.assertRefused(out, "records no title")

    def test_two_titles(self):
        out = self.check(filled().replace("    head   ", "    title  another\n    head   "))
        self.assertRefused(out, "more than one title")

    def test_a_title_no_reviewer_sees_the_end_of(self):
        out = self.check(filled(Stop_a_review="Stop " + "a review " * 12))
        self.assertRefused(out, "is the limit")

    def test_a_title_that_is_the_task_id(self):
        # The failure this whole document exists to stop, in its purest form: the
        # merge request named after a row in a queue nobody outside the fleet reads.
        out = self.check(filled(**{"Stop_a_review_reading_a_diff_nobody_validated":
                                   "use-target-merge-base"}))
        self.assertRefused(out, "is the task id")

    def test_no_head(self):
        out = self.check(filled().replace("    head   ", "    commit ", 1))
        self.assertRefused(out, "records no head")

    def test_an_abbreviated_head(self):
        # A prefix cannot be compared for equality with the head that was accepted,
        # so a short sha would make the staleness check quietly always pass.
        out = self.check(filled(head=HEAD[:12]))
        self.assertRefused(out, "the full sha")

    def test_a_head_the_branch_has_moved_past(self):
        out = self.check(filled(), "--head", OTHER)
        self.assertRefused(out, "describes aaaaaaaaaaaa", "at bbbbbbbbbbbb")

    def test_a_missing_section(self):
        out = self.check(filled().split("## Hotspots")[0])
        self.assertRefused(out, "no `## Hotspots` section")

    def test_a_section_that_is_only_a_heading(self):
        out = self.check(filled().replace(
            "A branch rebased mid-review still has to be rerun. Nothing here changes "
            "what a\nreview costs.", ""))
        self.assertRefused(out, "`## Risks and boundaries` section", "is empty")

    def test_a_section_nothing_publishes(self):
        # Refused rather than dropped: a section its author wrote and no reviewer
        # ever saw is worse than one that stopped the publish and said so.
        out = self.check(filled() + "\n## Screenshots\n\nnone.\n")
        self.assertRefused(out, "sections nothing publishes: Screenshots")

    def test_a_repeated_section(self):
        out = self.check(filled() + "\n## Hotspots\n\nsomething else.\n")
        self.assertRefused(out, "more than one `## Hotspots` section")

    def test_a_line_carrying_a_note_publishes_its_prose(self):
        """The first of the three the QA of this parser's own branch reproduced.

        Published as written minus the note, and the exact body asserted: a sentence
        deleted on the way to a forge is the one failure nothing downstream can see,
        because what arrives still reads like a document somebody wrote."""
        text = self.assertAccepted(self.check(filled().replace(
            "`bin/siana-pipeline`, where the base is chosen: a task that names no base"
            " now\nmeasures from where it forked, and that is the path a legacy task"
            " takes.",
            "The parser drops a line like this. <!-- a note to self -->\n"
            "This line survives."), "--head", HEAD))
        self.assertIn(printed("## Hotspots\n\nThe parser drops a line like this.\n"
                              "This line survives."), text)
        self.assertNotIn("a note to self", text)

    def test_a_markdown_example_of_a_section_is_not_a_section(self):
        """The second. `## Intent` inside a fenced example was read as a real
        duplicate, and the document was refused with a statement about itself that
        was not true."""
        text = self.assertAccepted(self.check(filled().replace(
            "The base is taken from the merge base of the recorded target, and an "
            "explicit base\nthe head is not descended from is refused rather than "
            "silently widened.",
            "The template now offers:\n\n```markdown\n## Intent\n\nwhat was "
            "wrong.\n```\n\nand nothing else."), "--head", HEAD))
        self.assertIn(printed("## Solution\n\nThe template now offers:\n\n"
                              "```markdown\n## Intent\n\nwhat was wrong.\n```\n\n"
                              "and nothing else."), text)

    def test_a_fence_still_leaves_a_real_duplicate_refused(self):
        # The fence is read past, not read as licence. A second `## Hotspots` outside
        # one is still a document whose author lost track of a section.
        out = self.check(filled().replace(
            "`bin/siana-pipeline`, where",
            "```markdown\n## Hotspots\n```\n\n`bin/siana-pipeline`, where")
            + "\n## Hotspots\n\nsomething else.\n")
        self.assertRefused(out, "more than one `## Hotspots` section")

    def test_a_fence_nothing_closes(self):
        # Refused rather than run to the end of the document: an unclosed fence
        # swallows every section after it, and what it reports then is four missing
        # sections, which is the wrong fault entirely.
        out = self.check(filled().replace("`bin/siana-pipeline`, where",
                                          "```markdown\n## Intent\n\nwhere"))
        self.assertRefused(out, "opens a fenced block that nothing closes", "```")

    def test_a_comment_nothing_closes(self):
        out = self.check(filled().replace("`bin/siana-pipeline`, where",
                                          "<!-- a note that never ends. where"))
        self.assertRefused(out, "opens a `<!--` that nothing closes", "-->")

    def test_a_path_that_only_exists_on_the_captains_machine(self):
        out = self.check(filled().replace(
            "`bin/siana-pipeline`, where", f"{self.home}/reports and where"))
        self.assertRefused(out, "the captain's machine and nowhere else")

    def test_the_variable_that_names_that_path(self):
        out = self.check(filled().replace("`just test` passes",
                                          "see $SIANA_HOME/reports; `just test` passes"))
        self.assertRefused(out, "names $SIANA_HOME")

    def test_the_home_written_the_way_the_readme_writes_it(self):
        """The third, and the one an author is likeliest to reach for: `~/.siana` is
        how this project's own README spells the directory."""
        out = self.check(filled().replace(
            "`just test` passes", "see ~/.siana/reports; `just test` passes"))
        self.assertRefused(out, "names ~/.siana", "the captain's machine")

    def test_the_variable_written_the_way_a_script_writes_it(self):
        out = self.check(filled().replace(
            "`just test` passes", "see ${SIANA_HOME}/reports; `just test` passes"))
        self.assertRefused(out, "names ${SIANA_HOME}")

    def test_the_same_directory_reached_through_the_home_variable(self):
        for form in ("$HOME/.siana", "${HOME}/.siana"):
            with self.subTest(form=form):
                out = self.check(filled().replace(
                    "`just test` passes", f"see {form}/reports; `just test` passes"))
                self.assertRefused(out, f"names {form}")

    def test_the_refusal_names_the_form_and_not_the_line(self):
        # What surrounds the path is the author's prose, and a refusal that quoted it
        # back would print the captain-local material this exists to keep in.
        out = self.check(filled().replace(
            "`just test` passes",
            "the verdict in ~/.siana/reports/qa-add-json.md; `just test` passes"))
        text = self.assertRefused(out, "names ~/.siana")
        self.assertNotIn("qa-add-json.md", text)

    def test_a_title_that_names_the_captains_machine(self):
        # The title is the single most visible line on the page, and it used to be
        # the one string this never looked at: the check ran over the five sections
        # and the title never reached it.
        out = self.check(filled(**{"Stop_a_review_reading_a_diff_nobody_validated":
                                   "Bind published copy to $SIANA_HOME/handoffs"}))
        self.assertRefused(out, "the title of", "names $SIANA_HOME")

    def test_prose_may_name_a_placeholder_token(self):
        """Scanning the whole document for `{NAME}` refused copy rather than
        scaffolding.

        This distro's templates are made of those tokens, so a handoff for work on
        one of them could not say what it changed, and there was nothing to escape it
        with. The scaffold's own markers are both in the preamble, and a section
        nobody filled in is already caught by being empty."""
        text = self.assertAccepted(self.check(
            filled().replace("`bin/siana-pipeline`, where",
                             "`bin/siana-brief`, where `{SHIP_TASK}` is substituted, "
                             "and `bin/siana-pipeline`, where")))
        self.assertIn("{SHIP_TASK}", text)


class Scaffold(HomeTest):

    def test_it_writes_the_home_template(self):
        self.template("handoff.md")
        out = self.assertAccepted(
            self.run_bin("siana-handoff", "add-json", "--scaffold"))
        self.assertIn("scaffolded", out)
        with open(self.at("handoffs", "add-json.md")) as fh:
            self.assertIn("{TITLE}", fh.read())

    def test_it_never_writes_over_one_that_exists(self):
        # A QA minion may already have read it, and a scaffold over a filled handoff
        # loses the only copy of prose no diff can reconstruct.
        self.template("handoff.md")
        self.run_bin("siana-handoff", "add-json", "--scaffold")
        out = self.run_bin("siana-handoff", "add-json", "--scaffold")
        self.assertRefused(out, "already has a handoff")

    def test_a_home_with_no_template(self):
        out = self.run_bin("siana-handoff", "add-json", "--scaffold")
        self.assertRefused(out, "no handoff template", "just init")


class Template(HomeTest):
    """The distro's own template, against the validator that reads it.

    Two copies of one shape: the headings the template offers and the sections the
    script requires. They drift silently, and the drift lands on a minion filling in
    a section that will be refused, or leaving out one nobody showed it.
    """

    def test_a_filled_scaffold_is_a_valid_handoff(self):
        self.template("handoff.md")
        self.run_bin("siana-handoff", "add-json", "--scaffold")
        path = self.at("handoffs", "add-json.md")
        with open(path) as fh:
            text = fh.read()
        text = text.replace("{TITLE}", "Say what changed and why it matters")
        text = text.replace("{HEAD}", HEAD)
        # Every section's guidance replaced by the prose it asks for, which is what
        # a minion filling this in does.
        text = re.sub(r"<!--.*?-->", "what this section is for.", text, flags=re.S)
        with open(path, "w") as fh:
            fh.write(text)
        out = self.assertAccepted(
            self.run_bin("siana-handoff", "add-json", "--head", HEAD))
        for heading in handoff.SECTIONS:
            self.assertIn(f"## {heading}", out)


if __name__ == "__main__":
    unittest.main()
