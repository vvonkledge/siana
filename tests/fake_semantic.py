#!/usr/bin/env python3
"""A scripted stand-in for `semantic-layer`, put on PATH under that name.

The real one is another project's installed command. Driving it here would make this
suite depend on a package being installed, on a pack existing in a checkout beside
this one, and on a clock: a pack is fresh for a day, so every test written against a
real one would start failing on a Tuesday. Worse, the answers this consumer most
needs to refuse - a version it does not read, an exit code that disagrees with its
own status, a digest that does not match the bytes beside it, a key nobody defined -
are exactly the ones a correct implementation can never be made to give.

So the transport is scripted and nothing else is. The commands still run this as a
real process, hand it real arguments, pipe a real run document into it, and read its
real exit code and standard output. What it says is a fixture:

    FAKE_SEMANTIC_PLAN    a JSON file: {"<command>": {...}} keyed by the two words
    FAKE_SEMANTIC_CALLS   append every invocation here as JSONL, argv and stdin

A plan entry carries `doc` (rendered the way the contract says a response is
rendered), `stdout` (raw, for a document no correct implementation would write), or
`stdout_base64` (rawer still, for output that is not text at all), optionally
`stderr`, and `exit` (0 by default, or 1 when the entry is an error).

The helpers below build the well-formed answers, so a test that wants one hostile
field says so and inherits the rest rather than hand-writing a whole document.

One value in a plan is not a fixture: the string `"@as-of"`, anywhere in the
document, is replaced with the `--as-of` the caller actually stated. A real command
answers about the instant it was asked about, and a test whose subject reads the
clock itself cannot know that instant in advance.
"""

import base64
import hashlib
import json
import os
import sys

RESPONSE = "https://semantic-layer.19h09.co/cli/response"


def render(doc):
    """The exact spelling the contract states: sorted keys, two-space indent, one
    trailing newline, ASCII with everything else escaped."""
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def response(command, result=None, error=None, version=1, schema=RESPONSE):
    doc = {"schema": schema, "version": version, "command": command,
           "status": "ok" if error is None else "error"}
    if error is None:
        doc["result"] = result if result is not None else {}
    else:
        doc["error"] = error
    return doc


def digest(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def pack(content, manifest, source, target, observed_at, fresh_until, **over):
    """The `pack` block, with digests and byte counts that agree with the halves
    beside them. A test overriding one of them is scripting a provider whose answer
    contradicts itself, which is the thing this consumer is written to catch."""
    block = {
        "identity": hashlib.sha256(
            (digest(content) + digest(manifest)).encode()).hexdigest(),
        "content_digest": digest(content),
        "manifest_digest": digest(manifest),
        "observation": "https://semantic-layer.19h09.co/l2/github/api-github-com/"
                       "observation/8ff2a001",
        "source": source,
        "target": target,
        "observed_at": observed_at,
        "fresh_until": fresh_until,
        "artifact_count": 3,
        "content_bytes": len(content.encode()),
        "manifest_bytes": len(manifest.encode()),
        "content_media_type": "application/n-triples",
        "vocabulary_version": "0.1.0",
        "graph": "https://semantic-layer.19h09.co/graph/l2-observed",
    }
    block.update(over)
    return block


def export(as_of, content, manifest, **over):
    return {"as_of": as_of,
            "pack": pack(content, manifest, **over),
            "content": {"encoding": "utf-8", "text": content},
            "manifest": {"encoding": "utf-8", "text": manifest}}


def recorded(trace_id, outcome, pack_block, created=True, **over):
    result = {
        "run": f"https://semantic-layer.19h09.co/l3/run/{trace_id}",
        "trace_id": trace_id,
        "created": created,
        "outcome": outcome,
        "counts": {"spans": 1, "metrics": 0, "findings": 0},
        "pack": {f: pack_block[f] for f in ("identity", "content_digest",
                                            "manifest_digest", "observation",
                                            "source", "target")},
    }
    result.update(over)
    return result


def expired(as_of, horizon, ids=(), retention_days=90):
    return {"as_of": as_of, "horizon": horizon, "retention_days": retention_days,
            "expired": list(ids)}


AS_OF = "@as-of"


def answering(value, as_of):
    """`"@as-of"` wherever it appears, replaced with the instant that was asked
    about."""
    if isinstance(value, dict):
        return {k: answering(v, as_of) for k, v in value.items()}
    if isinstance(value, list):
        return [answering(v, as_of) for v in value]
    return as_of if value == AS_OF else value


def main():
    argv = sys.argv[1:]
    command = " ".join(argv[:2])
    stdin = sys.stdin.read() if "--input" in argv and "-" in argv else ""

    calls = os.environ.get("FAKE_SEMANTIC_CALLS")
    if calls:
        with open(calls, "a") as fh:
            fh.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")

    with open(os.environ["FAKE_SEMANTIC_PLAN"]) as fh:
        plan = json.load(fh)
    if command not in plan:
        # Loud rather than plausible. A missing entry is a test that forgot to
        # script this call, and answering it with a refusal would look exactly like
        # a provider saying no.
        print(f"fake semantic-layer: nothing scripted for {command!r}",
              file=sys.stderr)
        return 99

    entry = plan[command]
    if "stderr" in entry:
        sys.stderr.write(entry["stderr"])
    if "stdout_base64" in entry:
        sys.stdout.flush()
        sys.stdout.buffer.write(base64.b64decode(entry["stdout_base64"]))
        sys.stdout.buffer.flush()
    elif "stdout" in entry:
        sys.stdout.write(entry["stdout"])
    else:
        asked = argv[argv.index("--as-of") + 1] if "--as-of" in argv else None
        sys.stdout.write(render(answering(entry["doc"], asked)))
    return entry.get("exit", 0 if entry.get("doc", {}).get("status") != "error"
                     else 1)


if __name__ == "__main__":
    raise SystemExit(main())
