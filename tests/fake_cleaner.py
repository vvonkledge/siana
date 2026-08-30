#!/usr/bin/env python3
"""A cleanup agent, scripted: `pi --mode json -p` reduced to what `siana-clean` reads.

Pi is the second boundary in this distro a test cannot drive for real, and
`tests/fake_pi.mjs` already says why at length: a live pi wants a terminal, a model
and the captain's credentials, so a suite that drove one would spend money on every
run and would still be unable to script the cases that matter. Here those cases are a
cleaner that asks and stops, a cleaner that dies mid-round, a cleaner that never
returns, and a cleaner whose output is not JSON at all.

So the model is scripted and nothing else is. `siana-clean` spawns this as a real
process with a real process group, streams its real stdout, parses the real event
shape pi emits, kills it through the real signals, and reaps it for real. What is
faked is only what the model decided to do.

It is installed as a file named `pi` on the front of the test's PATH, so the command
under test resolves it exactly as it resolves the real one - including through
`shutil.which`, which is the lookup the guard has to be kept out of.

The script is a JSON file named by `SIANA_FAKE_PI`:

    {"steps": [{"ask": {"body": "...", "kind": "siana", "options": ["a", "b"]}},
               {"say": "the round's report"},
               {"run": ["siana-retire", "some-task"]},
               {"raw": "not json at all"},
               {"sleep": 30},
               {"waitfor": "/a/path/the/test/creates", "waitfor_s": 120}],
     "exit": 0}

Every invocation appends what it was given to `$SIANA_FAKE_PI.calls`, one JSON object
per line: the whole argument list, the PATH it was started with, and the environment
`siana-clean` set. That recording is the point rather than a convenience - the brief
a resumed cleaner is handed and the guard directory it is given are both things a
test can only check by watching what actually arrived.
"""

import json
import os
import subprocess
import sys
import time


def emit(event) -> None:
    """One line of pi's JSON stream, flushed. Unflushed, a report written just before
    an exit would reach the reader only when the pipe closed, which is exactly the
    ordering a killed round is about."""
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def say(text: str) -> None:
    emit({"type": "message_end",
          "message": {"role": "assistant",
                      "content": [{"type": "text", "text": text}]}})


def main() -> int:
    script_path = os.environ.get("SIANA_FAKE_PI")
    if not script_path:
        print("fake pi: SIANA_FAKE_PI is not set", file=sys.stderr)
        return 2
    with open(script_path) as fh:
        script = json.load(fh)

    with open(script_path + ".calls", "a") as fh:
        fh.write(json.dumps({
            "argv": sys.argv[1:],
            "path": os.environ.get("PATH", ""),
            "run": os.environ.get("SIANA_CLEAN_RUN", ""),
            "grants": os.environ.get("SIANA_CLEAN_GRANTS", ""),
            "task": os.environ.get("SIANA_TASK_ID", ""),
            "cwd": os.getcwd(),
        }) + "\n")

    for step in script.get("steps", []):
        if "say" in step:
            say(step["say"])
        elif "raw" in step:
            sys.stdout.write(step["raw"] + "\n")
            sys.stdout.flush()
        elif "sleep" in step:
            time.sleep(step["sleep"])
        elif "waitfor" in step:
            # A barrier rather than a sleep. A test that needs the command to be
            # provably inside its critical section - holding the lock, with a second
            # caller about to arrive - cannot get there by waiting a fixed time: too
            # short is flaky and too long is the whole suite. This parks the round
            # until the test says otherwise, so the interleaving is decided rather
            # than raced for.
            #
            # Bounded, like every other step here. This process is started in a
            # session of its own, so nothing the test does to the command that
            # spawned it reaches this one: an unbounded wait plus a test that fails
            # before it writes the barrier leaves a process polling forever, outliving
            # the whole suite run. The bound is long enough that no passing test ever
            # reaches it and short enough to be a stray that clears itself.
            deadline = time.time() + step.get("waitfor_s", 120)
            while not os.path.exists(step["waitfor"]):
                if time.time() > deadline:
                    say(f"gave up waiting for {step['waitfor']}")
                    return 1
                time.sleep(0.02)
        elif "ask" in step:
            ask = step["ask"]
            argv = ["siana-clean", "ask",
                    "--run", os.environ["SIANA_CLEAN_RUN"],
                    "--body", ask["body"], "--kind", ask.get("kind", "siana")]
            for option in ask.get("options", []):
                argv += ["--option", option]
            proc = subprocess.run(argv, capture_output=True, text=True)
            say(f"asked, exit {proc.returncode}: "
                f"{(proc.stdout + proc.stderr).strip()}")
        elif "run" in step:
            # Through the PATH it was given, which is how the guard is exercised:
            # what a shim does is only true if the child reaches the shim.
            #
            # `$RUN` is the one substitution, so that a test can have the child name
            # its own run without knowing the id when it writes the script.
            argv = [a.replace("$RUN", os.environ.get("SIANA_CLEAN_RUN", ""))
                    for a in step["run"]]
            proc = subprocess.run(argv, capture_output=True, text=True)
            say(f"ran {argv[0]}, exit {proc.returncode}: "
                f"{(proc.stdout + proc.stderr).strip()}")
        elif "die" in step:
            os.kill(os.getpid(), int(step["die"]))
    return int(script.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
