#!/usr/bin/env python3
"""A scripted stand-in for the review step's agent, put on PATH as `claude`.

A real reviewer is an agent. It answers when it answers and never the same way twice,
so the answers this suite most needs - it wrote nothing, it wrote nonsense, it found
something nobody in the worktree can settle - are exactly the ones it could not be
made to give on cue. Only its transport is scripted here. Everything the command
builds around it is real: the argv, the prompt, the findings file, the exit code.

Driven by the environment the test sets:

    FAKE_FINDINGS     a JSON list of findings, or `silent` to write no file at all
    FAKE_PROMPT_OUT   where to copy the prompt, so a test can assert what it carried

The findings path is recovered from the prompt rather than passed in, so a command
that stopped substituting it would fail here rather than quietly reviewing into a
file nobody reads.
"""

import json
import os
import re
import sys

prompt = sys.argv[-1]

out = os.environ.get("FAKE_PROMPT_OUT")
if out:
    with open(out, "w") as fh:
        fh.write(prompt)

findings = os.environ.get("FAKE_FINDINGS", "[]")
if findings == "silent":
    raise SystemExit(0)

where = re.search(r"[^\s`]+\.findings\.json", prompt)
if not where:
    print("the prompt named no findings file", file=sys.stderr)
    raise SystemExit(1)

with open(where.group(0), "w") as fh:
    json.dump({"findings": json.loads(findings)}, fh)
