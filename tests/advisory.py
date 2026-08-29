"""A SIANA home with an advisory session in it, for the tests that need one.

A fixture module rather than a base class inside one of the test files, matching
`fake_herdr.py`: three test modules need this and a test module importing another
test module would be deciding which of them discovery loads first.

**Nothing here is faked.** The session is a real `siana-afk` process, because the
property every test below rests on is that a session is a process and not a file. A
stand-in that wrote the record itself would be precisely the forgery the pid and `ps`
checks exist to refuse, and every test built on one would pass against a mechanism
that does not work.
"""

import json
import os
import signal
import subprocess

from helpers import BIN, HomeTest, until

# A well-formed proposal, in the shape `siana-gate` validates and SIANA writes. Kept
# here rather than in each test so that a test about one missing field is visibly a
# test about that field, and not about a record that was never right to begin with.
PROPOSAL = {
    "action": "siana-publish qa-add-json",
    "evidence": ["task:qa-add-json done", "reports/qa-add-json.md"],
    "alternatives": ["hold until the captain returns: rejected, it costs a night"],
    "principles": ["Publish work two independent minions have accepted."],
    "confidence": "high",
    "reversibility": "R2",
}

PRINCIPLES = """# Principles

Publish work two independent minions have accepted, and never work only one has.
"""


class Advisory(HomeTest):
    """A home the gate can be driven against, with or without a live session."""

    def setUp(self):
        super().setUp()
        self.contract("projects", "decisions")
        self.queue()
        self.project("demo", target="main")
        self.principles(PRINCIPLES)
        self.store("tasks.jsonl",
                   {"id": "add-json", "title": "Add a --json flag", "status": "done",
                    "verify": "just test", "verify_kind": "cmd", "deps": [],
                    "context": [], "project": "demo",
                    "updated": "2026-08-29T09:00:00Z"},
                   {"id": "qa-add-json", "title": "QA add-json", "status": "done",
                    "verify": "just e2e", "verify_kind": "cmd", "deps": ["add-json"],
                    "context": [], "project": "demo", "base": "siana/add-json",
                    "updated": "2026-08-29T10:00:00Z"})

    def principles(self, text):
        with open(self.at("principles.md"), "w") as fh:
            fh.write(text)
        return self.at("principles.md")

    def proposal(self, name="record.json", **over):
        """A decision record on disk, well formed unless a test says otherwise.

        `None` removes a field, which is how a test says the record left one out
        rather than gave it a wrong value."""
        record = dict(PROPOSAL)
        record.update(over)
        record = {k: v for k, v in record.items() if v is not None}
        path = self.at(name)
        with open(path, "w") as fh:
            json.dump(record, fh)
        return path

    def gate(self, *args, **kw):
        return self.run_bin("siana-gate", *args, **kw)

    def decide(self, task="qa-add-json", record=None, klass="publish"):
        return self.gate(klass, "--task", task,
                         "--record", record or self.proposal())

    def session(self, *args):
        """A live advisory session, as a real process.

        Waited for rather than slept on, and stopped in a cleanup, so a test that
        fails part way through does not leave a `siana-afk` holding a home the suite
        is about to delete."""
        argv = [os.path.join(BIN, "siana-afk"), *(args or ("--until", "10m",
                                                          "--project", "demo"))]
        proc = subprocess.Popen(argv, cwd=self.home, env=self.command_env(), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.finish, proc)
        self.assertTrue(until(lambda: os.path.exists(self.at("afk"))),
                        "siana-afk never recorded itself")
        return proc

    def finish(self, proc):
        """Stop a session and return everything it said.

        The output is returned rather than read off the pipes afterwards, because
        `communicate` closes them: a test that read `proc.stderr` after this would
        get a closed-file error instead of the warning it was asserting on. Safe to
        call twice, since it is also registered as a cleanup."""
        if getattr(proc, "said", None) is not None:
            return proc.said
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.said = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.said = proc.communicate(timeout=15)
        return proc.said

    def grant(self):
        with open(self.at("afk")) as fh:
            return json.load(fh)

    def rewrite_grant(self, **fields):
        """Change one fact in the live session's record, leaving the pid and the
        command it recorded alone.

        This is how a test reaches a state the clock would otherwise have to be waited
        out for. What it must never touch is the two fields that say which process
        this is, because those are what the reader under test is actually checking."""
        record = self.grant()
        record.update(fields)
        with open(self.at("afk"), "w") as fh:
            json.dump(record, fh)
        return record

    def ledger(self):
        """The decision ledger, folded, oldest first."""
        out = {}
        if not os.path.exists(self.at("decisions.jsonl")):
            return []
        with open(self.at("decisions.jsonl")) as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    out[rec["id"]] = rec
        return list(out.values())

    def assertNothingPermitted(self, *outs):
        """No call returned 0, and no record in the ledger reads permitted.

        Both halves, because either alone would pass against the other's bug: an exit
        code says what a caller was told, and the ledger says what the captain will
        read in the morning."""
        for out in outs:
            self.assertNotEqual(out.returncode, 0,
                                f"a gate call returned 0:\n{out.stdout}{out.stderr}")
        verdicts = {rec.get("verdict") for rec in self.ledger()}
        self.assertNotIn("permitted", verdicts)
        self.assertTrue(verdicts <= {"proposed", "refused"}, verdicts)
