"""siana-console: the fleet on a loopback socket, and nothing else on it.

Three things here can be wrong in a way that costs the captain something real, and
each has a class of its own below.

**The address.** The listener asks for no credential, so `127.0.0.1` is the whole of
what stands between the captain's queue and whatever network this machine is on.
That is not provable by reading the code, so it is proved by connecting: every other
address this machine has must refuse, and the port must still be free to bind on
them.

**The relay.** A source that could not be read must arrive as a source that could
not be read. `siana-read` already refuses correctly, and it does not refuse in one
shape, so the failure worth catching is the console tidying those refusals into
something that looks like an answer: an unreadable store as no tasks, a silent herdr
as no minions.

**The claim.** Two consoles are two listeners racing for one port, and a claim
wrongly taken over is how the second one comes up. Every reclaim path is driven,
including the one where nothing can be proved and the answer is to refuse.

The stores stay real, and so does `siana-read`: relaying that command is the console's
whole job, and a stubbed one would only ever agree with what this suite already
believed it says. Three transports are scripted, each because the answers that matter
cannot be arranged for real - herdr in `fake_herdr.py`, `siana-read`'s own failures
in `fake_read.py`, and a `ps` that will not name one pid in `fake_ps.py`.
"""

import errno
import http.client
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import time
import unittest

from fake_herdr import FakeHerdr
from helpers import BIN, HomeTest, gone_pid, until

NODE = shutil.which("node")
CONSOLE = os.path.join(BIN, "siana-console")
HERE = os.path.dirname(os.path.abspath(__file__))

SOURCES = ("tasks", "projects", "obligations", "decisions", "fleet", "health")

# Longer than two of the stream's own polls, so a test that waits this long and sees
# nothing has seen a timer that really stopped rather than one it outran.
QUIET_S = 5.0


def free_port():
    """A loopback port nothing holds. Taken by binding one and letting it go, so it
    is a port this machine really had rather than a number that looked free."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def other_addresses():
    """Every address this machine answers to that is not IPv4 loopback.

    Discovered rather than listed: a test naming the addresses it believes a runner
    has is asserting that runner's network instead of the console's bind. `::1` is
    always in it, because it is the address a dual-stack listener picks up by
    accident and the one a reader would call loopback and wave through.
    """
    found = ["::1"]
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Nothing is sent: a connected UDP socket only picks a route, and this is
        # TEST-NET-1, reserved for exactly this.
        probe.connect(("192.0.2.1", 9))
        found.append(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.append(info[4][0])
    except socket.gaierror:
        pass
    return sorted({a for a in found if not a.startswith("127.")})


class Stream:
    """One `/api/stream` connection, read as it arrives.

    Raw, because this is about the framing: `http.client` would hand back a file
    object and hide whether the response head arrived before the first event, which
    is the half of server-sent events a client actually depends on.
    """

    def __init__(self, port, target="/api/stream"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=0.5)
        self.sock.sendall(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                          f"Accept: text/event-stream\r\n\r\n".encode())
        self.text = ""
        self.ended = False

    def read(self, timeout=1.0):
        """Whatever has arrived by now, appended to what arrived before."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                self.ended = True
                break
            if not chunk:
                self.ended = True
                break
            self.text += chunk.decode()
        return self.text

    def until(self, needle, timeout=30.0):
        deadline = time.time() + timeout
        while needle not in self.text and not self.ended and time.time() < deadline:
            self.read(0.5)
        return self.text

    def events(self):
        """The revision out of every `state` event so far, in order."""
        return [json.loads(block.splitlines()[0])["revision"]
                for block in self.text.split("data: ")[1:]]

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


@unittest.skipUnless(NODE, "siana-console runs on node")
class Console(HomeTest):
    """A home with the contracts in it, a scripted herdr, and one way to start a
    console against both."""

    def setUp(self):
        super().setUp()
        self.queue()
        self.contract("projects", "obligations", "decisions")
        self.herdr = FakeHerdr()
        self.herdr.start()
        self.addCleanup(self.herdr.stop)
        self.herdr.reply("agent.list", {"agents": []})
        self.port = free_port()
        self.starts = 0

    # -- fixtures ---------------------------------------------------------------

    def start(self, env=None, port=None, path=None, wait=True):
        """The console as a real process, with its output on disk.

        On disk rather than down a pipe: this process outlives the call that
        started it, and a pipe nobody is draining is a process that blocks on its
        own stderr instead of serving.
        """
        self.starts += 1
        out = self.at(f"console.{self.starts}.out")
        err = self.at(f"console.{self.starts}.err")
        environment = self.command_env({
            "SIANA_CONSOLE_PORT": str(self.port if port is None else port),
            "HERDR_SOCKET_PATH": self.herdr.path,
            "PATH": path or self.distro_path(),
        })
        environment.update(env or {})
        with open(out, "w") as o, open(err, "w") as e:
            proc = subprocess.Popen([CONSOLE], cwd=self.home, env=environment,
                                    stdout=o, stderr=e, text=True)
        proc.out, proc.err = out, err
        self.addCleanup(self.stop, proc)
        if wait:
            self.assertTrue(
                until(lambda: proc.poll() is not None or self.listening(port)),
                f"the console never bound a port:\n{self.said(proc)}")
            self.assertIsNone(proc.poll(),
                              f"the console stopped:\n{self.said(proc)}")
        return proc

    def stop(self, proc, how=None):
        if proc.poll() is None:
            (how or subprocess.Popen.terminate)(proc)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=30)
        return proc.returncode

    def said(self, proc):
        """Both streams of one console, for a failure message."""
        text = ""
        for name, path in (("stdout", proc.out), ("stderr", proc.err)):
            with open(path) as fh:
                text += f"--- {name} ---\n{fh.read()}"
        return text

    def refused(self, proc, *fragments):
        """A console that would not start, and what it said about it."""
        self.assertEqual(proc.wait(timeout=30), 1, self.said(proc))
        text = self.said(proc)
        for fragment in fragments:
            self.assertIn(fragment, text)
        return text

    def listening(self, port=None):
        try:
            socket.create_connection(
                ("127.0.0.1", self.port if port is None else port),
                timeout=1).close()
            return True
        except OSError:
            return False

    def request(self, target="/api/state", method="GET", port=None, timeout=180):
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.port if port is None else port, timeout=timeout)
        try:
            conn.request(method, target)
            res = conn.getresponse()
            return res.status, dict(res.getheaders()), res.read().decode()
        finally:
            conn.close()

    def state(self, target="/api/state"):
        """One `/api/state` answer, parsed. A 200 that is not one JSON document has
        already broken the contract, whatever it said."""
        status, _, body = self.request(target)
        self.assertEqual(status, 200, body)
        try:
            return json.loads(body)
        except ValueError as e:
            self.fail(f"/api/state is not one JSON document ({e}):\n{body}")

    def stream(self, target="/api/stream"):
        s = Stream(self.port, target)
        self.addCleanup(s.close)
        return s

    def task(self, **fields):
        args = [f"{k}={v}" for k, v in fields.items()]
        self.assertAccepted(self.run_cmd(
            ["datafile", "-f", self.at("tasks.jsonl"),
             "-c", self.at("schema-tasks.yaml"), "put",
             *sum((["--set", a] for a in args), [])]))

    def fakebin(self, *scripted):
        """A directory of scripted commands, ahead of everything else on PATH."""
        where = self.at("fakebin")
        os.makedirs(where, exist_ok=True)
        for name, source in scripted:
            target = os.path.join(where, name)
            shutil.copy(os.path.join(HERE, source), target)
            os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR)
        return where

    def path_with_no_read(self):
        """A PATH holding only what starting the console needs, and no `siana-read`.

        `distro_path` cannot do this one: it puts this checkout's `bin/` in front
        precisely so the commands under test answer, and `siana-read` is in it. So
        the world is built from nothing instead, with `node` for the shebang and
        `ps` for the claim's identity check and no third thing.
        """
        where = self.at("barebin")
        os.makedirs(where, exist_ok=True)
        for name in ("node", "ps"):
            found = shutil.which(name)
            self.assertIsNotNone(found, f"this machine has no {name}")
            link = os.path.join(where, name)
            if not os.path.lexists(link):
                os.symlink(found, link)
        return where

    def bytes(self, name):
        with open(self.at(name), "rb") as fh:
            return fh.read()

    def claim(self):
        with open(self.at("inbox", "console")) as fh:
            return json.load(fh)

    def write_claim(self, text):
        os.makedirs(self.at("inbox"), exist_ok=True)
        with open(self.at("inbox", "console"), "w") as fh:
            fh.write(text)

    def stranger(self):
        """A live process that is not a console, for a claim to name."""
        proc = subprocess.Popen(["sleep", "120"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        return proc

    def claiming(self, pid, port=None):
        self.write_claim(json.dumps(
            {"state": "running", "pid": pid, "command": "node siana-console",
             "host": "127.0.0.1", "port": self.port if port is None else port,
             "started": "2026-08-01T00:00:00Z"}))


class TheAddress(Console):
    """`127.0.0.1`, proved by connecting rather than by reading the source.

    The console asks for no credential. Bound one address wider it would serve the
    captain's whole queue - every task, every obligation, every decision, and every
    minion's working directory - to anything on the same network, and nothing about
    the process would look different from outside.
    """

    def unreachable_elsewhere(self):
        for address in other_addresses():
            with self.subTest(address):
                try:
                    sock = socket.create_connection((address, self.port), timeout=5)
                except OSError:
                    continue
                sock.close()
                self.fail(f"the console answered on {address}, which is not the "
                          f"loopback address it is allowed to bind")

    def test_it_answers_on_loopback(self):
        self.start()
        status, _, body = self.request()
        self.assertEqual(status, 200, body)

    def test_no_other_address_on_this_machine_answers(self):
        self.start()
        self.unreachable_elsewhere()

    def test_nothing_in_the_environment_moves_the_bind(self):
        # The failure that cannot be found by looking at a running process: a host
        # that is configurable is a host that gets configured wrongly once, and the
        # console would look exactly the same afterwards.
        self.start(env={"SIANA_CONSOLE_HOST": "0.0.0.0", "HOST": "0.0.0.0",
                        "HOSTNAME": "0.0.0.0", "SIANA_CONSOLE_BIND": "::"})
        self.unreachable_elsewhere()
        self.assertEqual(self.request()[0], 200)

    def test_the_port_is_still_free_on_every_other_interface(self):
        """The wildcard, proved by taking what it would have held.

        Connecting to another address only shows that nothing answered there, which
        a firewall could also produce. Binding it shows the console is not holding
        it: to the kernel `0.0.0.0` and this machine's own address are the same
        address, so a console on the wildcard makes this bind fail.
        """
        elsewhere = [a for a in other_addresses() if ":" not in a]
        if not elsewhere:
            self.skipTest("this machine has no non-loopback IPv4 address")
        self.start()
        for address in elsewhere:
            with self.subTest(address):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.bind((address, self.port))
                except OSError as e:
                    self.assertNotEqual(
                        e.errno, errno.EADDRINUSE,
                        f"{address}:{self.port} is held, so the console is not "
                        f"bound to loopback alone")
                finally:
                    sock.close()


class Routes(Console):
    """Two routes, and every other request refused without reaching anything."""

    def setUp(self):
        super().setUp()
        self.start()

    def test_only_the_two_documented_paths_are_served(self):
        for target in ("/", "/index.html", "/api", "/api/", "/api/state/",
                       "/api/statex", "/api/messages", "/favicon.ico",
                       "/.git/config"):
            with self.subTest(target):
                status, _, body = self.request(target)
                self.assertEqual(status, 404, body)
                self.assertEqual(json.loads(body)["code"], "NO_ROUTE")

    def test_a_traversal_reaches_no_file(self):
        # Nothing here opens a file for a request path at all, so this is belt and
        # braces. It is here because slice 3 serves a bundle, and a route table that
        # quietly normalised its way into serving one would pass every other test.
        for target in ("/api/state/../../../etc/passwd", "/api/../../etc/passwd",
                       "/api/state/..%2f..%2fetc%2fpasswd", "/%61pi/state",
                       "/api/state%00", "//api/state", "/api/state/.."):
            with self.subTest(target):
                status, _, body = self.request(target)
                self.assertEqual(status, 404, body)
                self.assertNotIn("root:", body)

    def test_a_query_string_reaches_no_other_route(self):
        for target in ("/api/state?rev=", "/api/state?rev=nothing",
                       "/api/state?path=/etc/passwd", "/api/state?rev=a&rev=b",
                       "/api/state?"):
            with self.subTest(target):
                status, _, body = self.request(target)
                self.assertEqual(status, 200, body[:400])

    def test_every_method_but_get_is_refused(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            for target in ("/api/state", "/api/stream"):
                with self.subTest(f"{method} {target}"):
                    status, headers, body = self.request(target, method=method)
                    self.assertEqual(status, 405, body)
                    self.assertEqual(headers.get("Allow"), "GET")

    def test_a_refused_request_is_a_document_too(self):
        # The caller is a program, so a refusal it cannot parse is a refusal it has
        # to guess at. The shape mirrors `siana-read`'s own, so a console never has
        # to learn two ways of being told no.
        for target, method in (("/nope", "GET"), ("/api/state", "POST")):
            with self.subTest(f"{method} {target}"):
                _, headers, body = self.request(target, method=method)
                self.assertIn("application/json", headers["Content-Type"])
                doc = json.loads(body)
                self.assertIn("error", doc)
                self.assertIn("code", doc)


class TheSnapshot(Console):
    """One document holding all six sources, each of them relayed whole.

    Driven through the real `siana-read` against a real store, because relaying it
    faithfully is the whole job: a stubbed one would only ever say what this suite
    already believed it says.
    """

    def setUp(self):
        super().setUp()
        self.task(id="one", title="first", verify="just test", status="doing",
                  updated="2026-08-01T00:00:00Z")
        self.start()

    def test_every_source_is_answered_in_one_document(self):
        doc = self.state()
        self.assertEqual(sorted(doc["sources"]), sorted(SOURCES))
        for name in SOURCES:
            with self.subTest(name):
                source = doc["sources"][name]
                self.assertEqual(source["source"], name)
                self.assertEqual(source["command"], ["siana-read", name])
                self.assertTrue(source["observed"])
                self.assertIsNotNone(source["document"])
                self.assertIsNone(source["error"])

    def test_a_store_arrives_with_its_records_and_its_own_revision(self):
        tasks = self.state()["sources"]["tasks"]
        self.assertEqual(tasks["exit"], 0)
        self.assertEqual([r["id"] for r in tasks["document"]["records"]], ["one"])
        self.assertEqual(tasks["document"]["bad_lines"], [])
        # The store's own inode-aware revision, passed through rather than replaced
        # by the console's: a reader caching per store needs the store's.
        self.assertIn("inode", tasks["document"]["revision"])

    def test_a_damaged_store_arrives_damaged_and_not_smoothed_over(self):
        self.store("tasks.jsonl", "not json at all")
        tasks = self.state()["sources"]["tasks"]
        self.assertEqual(tasks["exit"], 0, tasks)
        self.assertTrue(tasks["document"]["bad_lines"], tasks["document"])
        self.assertEqual([r["id"] for r in tasks["document"]["records"]], ["one"])

    def test_the_health_document_keeps_its_parts_apart(self):
        health = self.state()["sources"]["health"]
        for part in ("session", "wake", "watch"):
            self.assertIn(part, health["document"])
        # `siana-watch --status` writes faults to stderr and ok lines to stdout, and
        # only its exit code is the verdict. All three arrive, undecided.
        for part in ("exit", "stdout", "stderr"):
            self.assertIn(part, health["document"]["watch"])

    def test_the_fleet_document_is_what_herdr_said(self):
        self.herdr.reply("agent.list", {"agents": [
            {"pane_id": "w1:p1", "agent": "claude", "agent_status": "working"}]})
        fleet = self.state()["sources"]["fleet"]
        self.assertEqual(fleet["exit"], 0, fleet)
        self.assertEqual(fleet["document"]["state"], "ok")
        self.assertEqual(fleet["document"]["agents"][0]["pane_id"], "w1:p1")

    def test_no_request_writes_a_fleet_store(self):
        """Read, and only read.

        The stores themselves rather than the whole home: a `datafile` read may
        rewrite the `.idx` cache beside one, which `siana-read` discloses, and the
        console writes its own claim. What must never move is an authoritative
        record, and the log is where those live.
        """
        logs = ("tasks.jsonl", "projects.jsonl", "obligations.jsonl")
        before = {n: self.bytes(n) for n in logs if os.path.isfile(self.at(n))}
        self.state()
        self.stream().until("event: state")
        for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
            for target in ("/api/messages", "/api/state/one", "/"):
                self.request(target, method=method)
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            for target in ("/api/state", "/api/stream"):
                self.request(target, method=method)
        after = {n: self.bytes(n) for n in logs if os.path.isfile(self.at(n))}
        self.assertEqual(after, before)


class Degraded(Console):
    """A source that could not be read, arriving as a source that could not be read.

    This is the class that matters. Every failure below has an answer that looks
    healthy - an empty list, a fleet with no minions, a document with a field
    missing - and every one of those would be rendered to the captain as fact.
    """

    def scripted(self, **files):
        """A `siana-read` that says exactly what a test needs it to say."""
        where = self.at("scripted")
        os.makedirs(where, exist_ok=True)
        for name, text in files.items():
            with open(os.path.join(where, name.replace("_", ".")), "w") as fh:
                fh.write(text)
        return {"path": self.distro_path(
            self.fakebin(("siana-read", "fake_read.py"))),
            "env": {"FAKE_READ_DIR": where}}

    def test_an_unreadable_store_is_a_refusal_and_not_an_empty_one(self):
        # An empty `obligations` would tell the captain SIANA owes them nothing,
        # which is the one wrong answer that store must never give.
        os.remove(self.at("schema-obligations.yaml"))
        self.start()
        owed = self.state()["sources"]["obligations"]
        self.assertNotEqual(owed["exit"], 0, owed)
        self.assertEqual(owed["document"]["code"], "NO_CONTRACT")
        self.assertNotIn("records", owed["document"])

    def test_one_failing_source_does_not_erase_the_others(self):
        os.remove(self.at("schema-decisions.yaml"))
        self.start()
        doc = self.state()
        self.assertNotEqual(doc["sources"]["decisions"]["exit"], 0)
        self.assertEqual(doc["sources"]["tasks"]["exit"], 0,
                         doc["sources"]["tasks"])
        self.assertIn("records", doc["sources"]["tasks"]["document"])

    def test_an_unreachable_herdr_is_unknown_and_never_no_minions(self):
        """The failure that would read as an answer.

        `siana-read fleet` refuses this with `state: "unknown"` and no top-level
        `error` key at all, which is why nothing in the console indexes one. A
        console that did would find nothing there, call the source healthy, and
        render an empty fleet over minions that are still running.
        """
        self.herdr.stop()
        self.start()
        fleet = self.state()["sources"]["fleet"]
        self.assertNotEqual(fleet["exit"], 0, fleet)
        self.assertEqual(fleet["document"]["state"], "unknown")
        self.assertIsNone(fleet["document"]["agents"])
        self.assertEqual(fleet["document"]["code"], "HERDR_UNREACHABLE")

    def test_a_malformed_herdr_is_a_refusal_and_not_a_shorter_fleet(self):
        self.herdr.reply("agent.list", {"agents": "not a list"})
        self.start()
        fleet = self.state()["sources"]["fleet"]
        self.assertNotEqual(fleet["exit"], 0, fleet)
        self.assertEqual(fleet["document"]["code"], "HERDR_MALFORMED")

    def test_a_source_that_did_not_answer_with_json_is_named_as_that(self):
        self.start(**self.scripted(
            tasks_out="Traceback (most recent call last):\n  boom\n"))
        doc = self.state()
        tasks = doc["sources"]["tasks"]
        self.assertIsNone(tasks["document"])
        self.assertEqual(tasks["error"]["code"], "BAD_JSON")
        self.assertIn("Traceback", tasks["error"]["stdout"])
        # And the other five still answered.
        self.assertIsNotNone(doc["sources"]["projects"]["document"])

    def test_a_sources_exit_code_is_kept_apart_from_its_document(self):
        # `siana-read` exits 2 for a request it could not make sense of and 1 for a
        # source it could not read, and a console that folded those together would
        # tell a reader the store is broken when the request was.
        self.start(**self.scripted(
            decisions_out='{"source": "decisions", "error": "no",'
                          ' "code": "USAGE_ERROR"}',
            decisions_exit="2"))
        decisions = self.state()["sources"]["decisions"]
        self.assertEqual(decisions["exit"], 2)
        self.assertEqual(decisions["document"]["code"], "USAGE_ERROR")
        self.assertIsNone(decisions["error"])

    def test_a_source_that_answered_with_something_else_is_not_a_document(self):
        self.start(**self.scripted(fleet_out="[1, 2, 3]"))
        fleet = self.state()["sources"]["fleet"]
        self.assertIsNone(fleet["document"])
        self.assertEqual(fleet["error"]["code"], "NOT_A_DOCUMENT")

    def test_a_slow_source_does_not_hold_up_the_others(self):
        """The six are read at once.

        One after another, a store that takes a moment would be added to every other
        source's wait, and the console's answer would be as slow as the sum of its
        sources rather than as slow as the slowest of them.
        """
        held = self.scripted()
        held["env"]["FAKE_READ_SLEEP"] = ",".join(f"{name}:2" for name in SOURCES)
        self.start(**held)
        began = time.time()
        doc = self.state()
        self.assertEqual(sorted(doc["sources"]), sorted(SOURCES))
        self.assertLess(time.time() - began, len(SOURCES) * 2,
                        "the sources are being read one after another")

    def test_a_missing_siana_read_is_named_and_not_an_empty_fleet(self):
        self.start(path=self.path_with_no_read())
        doc = self.state()
        for name in SOURCES:
            with self.subTest(name):
                source = doc["sources"][name]
                self.assertIsNone(source["document"])
                self.assertEqual(source["error"]["code"], "NO_SIANA_READ")


class TheRevision(Console):
    """An opaque cache validator: the same for the same fleet, different for a
    different one, and never authority about either."""

    def setUp(self):
        super().setUp()
        self.task(id="one", title="first", verify="just test",
                  updated="2026-08-01T00:00:00Z")
        self.proc = self.start()

    def test_the_same_fleet_answers_with_the_same_revision(self):
        # Minted from the sources rather than from the moment of asking. A revision
        # that moved per request would make the 204 below unreachable and turn the
        # stream into a change announcement every two seconds.
        self.assertEqual(self.state()["revision"], self.state()["revision"])

    def test_a_matching_revision_answers_204_with_no_body(self):
        revision = self.state()["revision"]
        status, headers, body = self.request(f"/api/state?rev={revision}")
        self.assertEqual(status, 204)
        self.assertEqual(body, "")
        self.assertNotIn("Content-Type", headers)

    def test_only_an_exact_match_answers_204(self):
        revision = self.state()["revision"]
        for rev in ("", "nothing", revision[:-1], revision + "0", revision.upper()):
            with self.subTest(rev):
                status, _, _ = self.request(f"/api/state?rev={rev}")
                self.assertEqual(status, 200)

    def test_a_changed_store_changes_the_revision(self):
        revision = self.state()["revision"]
        self.task(id="two", title="second", verify="just test",
                  updated="2026-08-02T00:00:00Z")
        self.assertNotEqual(self.state()["revision"], revision)
        self.assertEqual(self.request(f"/api/state?rev={revision}")[0], 200)

    def test_a_changed_fleet_changes_the_revision(self):
        revision = self.state()["revision"]
        self.herdr.reply("agent.list", {"agents": [{"pane_id": "w1:p1"}]})
        self.assertNotEqual(self.state()["revision"], revision)

    def test_a_source_that_starts_failing_changes_the_revision(self):
        revision = self.state()["revision"]
        os.remove(self.at("schema-projects.yaml"))
        self.assertNotEqual(self.state()["revision"], revision)

    def test_a_compacted_store_changes_the_revision(self):
        """The change a size alone would miss.

        `datafile compact` rewrites the log in place, so the records can be the same
        while the file is a different file. `siana-read` reports an inode with every
        store revision for that reason, and the console's revision is computed over
        what it reported, so a reader caching against it refetches.
        """
        revision = self.state()["revision"]
        self.assertAccepted(self.run_cmd(
            ["datafile", "-f", self.at("tasks.jsonl"),
             "-c", self.at("schema-tasks.yaml"), "compact"]))
        self.assertNotEqual(self.state()["revision"], revision)

    def test_a_restart_against_untouched_stores_answers_the_same_revision(self):
        """No authoritative state lives only in this process.

        A revision drawn partly from memory would change on every restart, and a
        phone holding a cached document would refetch the whole fleet every time the
        captain stopped and started the console.
        """
        first = self.state()["revision"]
        self.stop(self.proc)
        self.start()
        self.assertEqual(self.state()["revision"], first)

    def test_the_document_still_says_when_it_was_read(self):
        # The revision leaves observation time out, so the document has to carry it:
        # a reader showing "as of" needs a clock, and a revision that moved with the
        # clock would be no use as a validator.
        doc = self.state()
        self.assertTrue(doc["observed"])
        self.assertTrue(doc["sources"]["fleet"]["observed"])


class TheStream(Console):
    """Server-sent events that announce a revision and carry no state.

    The stream is an optimisation. Everything below is about it staying one: it must
    never become a second account of the fleet that a reconnect has to reconcile
    against the first.
    """

    def setUp(self):
        super().setUp()
        self.task(id="one", title="first", verify="just test",
                  updated="2026-08-01T00:00:00Z")
        self.start()

    def test_it_is_an_event_stream_and_not_a_socket_upgrade(self):
        text = self.stream().until("\r\n\r\n")
        self.assertIn("HTTP/1.1 200", text)
        self.assertIn("text/event-stream", text)
        self.assertNotIn("101 Switching", text)

    def test_it_announces_the_current_revision_on_connect(self):
        s = self.stream()
        s.until("event: state")
        self.assertEqual(s.events()[-1], self.state()["revision"])

    def test_it_announces_a_new_revision_when_the_fleet_moves(self):
        s = self.stream()
        s.until("event: state")
        first = s.events()[-1]
        self.task(id="two", title="second", verify="just test",
                  updated="2026-08-02T00:00:00Z")
        self.assertTrue(until(lambda: (s.read(1.0), len(s.events()) > 1)[1],
                              timeout=60),
                        f"no second revision arrived:\n{s.text}")
        self.assertNotEqual(s.events()[-1], first)
        self.assertEqual(s.events()[-1], self.state()["revision"])

    def test_it_carries_no_id_so_no_client_can_ask_to_be_replayed(self):
        # An `id:` is what makes a browser send `Last-Event-ID` on reconnect.
        # Answering that honestly would mean keeping a replay log here, which is the
        # second source of truth this whole design refuses.
        text = self.stream().until("event: state")
        self.assertNotIn("id:", text)

    def test_the_state_endpoint_is_whole_without_it(self):
        # A client that never opens a stream, or whose stream drops and stays
        # dropped, is missing nothing.
        s = self.stream()
        s.until("event: state")
        s.close()
        self.assertIsNotNone(self.state()["sources"]["tasks"]["document"])

    def test_a_reconnecting_client_is_told_the_current_revision(self):
        first = self.stream()
        first.until("event: state")
        first.close()
        again = self.stream()
        again.until("event: state")
        self.assertEqual(again.events()[-1], self.state()["revision"])

    def test_the_last_client_leaving_stops_the_reading(self):
        """Disconnected clients are cleaned up, and the timer goes with them.

        Counted at herdr rather than inside the process: every poll asks it for the
        fleet, so a poll still running after the last client left is a call arriving
        while nothing is listening.
        """
        s = self.stream()
        s.until("event: state")
        self.assertTrue(until(lambda: len(self.herdr.calls_to("agent.list")) > 1,
                              timeout=30), "the stream never polled at all")
        s.close()
        time.sleep(QUIET_S)
        settled = len(self.herdr.calls_to("agent.list"))
        time.sleep(QUIET_S)
        self.assertEqual(len(self.herdr.calls_to("agent.list")), settled)


class TheSingleton(Console):
    """One console per home, and a claim only ever taken over when the console that
    wrote it can be proved to have stopped."""

    def test_it_records_the_port_it_owns(self):
        proc = self.start()
        claim = self.claim()
        self.assertEqual(claim["pid"], proc.pid)
        self.assertEqual(claim["port"], self.port)
        self.assertEqual(claim["host"], "127.0.0.1")
        self.assertIn("siana-console", claim["command"])

    def test_a_second_console_refuses_and_names_the_first(self):
        proc = self.start()
        self.refused(self.start(port=free_port(), wait=False),
                     "a console is already running",
                     f"pid {proc.pid}", f"port {self.port}")
        # And the first is untouched: neither its claim nor its listener moved.
        self.assertEqual(self.claim()["pid"], proc.pid)
        self.assertEqual(self.request()[0], 200)

    def test_a_claim_whose_process_is_gone_is_taken_over(self):
        self.claiming(gone_pid())
        proc = self.start()
        self.assertEqual(self.claim()["pid"], proc.pid)
        self.assertIn("replaced a console claim that had stopped", self.said(proc))

    def test_a_claim_whose_pid_is_now_something_else_is_taken_over(self):
        # Pids are reused, so a live pid is not a live console. The record names
        # what `ps` called the process, and that is what settles it.
        self.claiming(self.stranger().pid)
        proc = self.start()
        self.assertEqual(self.claim()["pid"], proc.pid)

    def test_a_claim_that_cannot_be_verified_refuses_rather_than_reclaiming(self):
        """The line this draws that `siana-watch` does not.

        A watcher treats everything it cannot confirm as stopped. A console must
        not: the holder it cannot ask about may be serving that port right now, and
        taking the claim from it is the second console this record exists to
        prevent. So `ps` saying nothing about a pid that is demonstrably alive is
        knowing nothing, and knowing nothing refuses.
        """
        stranger = self.stranger()
        self.claiming(stranger.pid)
        proc = self.start(path=self.distro_path(self.fakebin(("ps", "fake_ps.py"))),
                          env={"FAKE_PS_SILENT": str(stranger.pid)}, wait=False)
        self.refused(proc, "cannot be verified", str(stranger.pid))
        self.assertEqual(self.claim()["pid"], stranger.pid)

    def test_a_claim_that_is_not_a_record_refuses(self):
        for text in ("", "not json", "[]", '{"state": "running"}',
                     '{"pid": "a lot", "command": "node siana-console"}',
                     '{"pid": 1, "command": ""}'):
            with self.subTest(text):
                self.write_claim(text)
                self.refused(self.start(wait=False), "is not a record this wrote")

    def test_a_claim_that_cannot_be_read_refuses(self):
        os.makedirs(self.at("inbox"), exist_ok=True)
        os.mkdir(self.at("inbox", "console"))
        self.addCleanup(os.rmdir, self.at("inbox", "console"))
        self.refused(self.start(wait=False), "cannot be read")

    def test_losing_the_link_refuses_and_starts_nothing(self):
        """A claim that appeared between reading and linking.

        Reached here through a dangling symlink, which reads as absent and refuses
        `link` exactly as a claim written a moment ago would. Whatever put it there,
        the answer is the same: nothing was started, and nothing already there was
        touched.
        """
        os.makedirs(self.at("inbox"), exist_ok=True)
        os.symlink(self.at("inbox", "gone"), self.at("inbox", "console"))
        self.refused(self.start(wait=False), "another console claimed the record")
        self.assertFalse(self.listening())

    def test_nothing_is_killed_to_recover_a_claim(self):
        stranger = self.stranger()
        self.claiming(stranger.pid)
        self.start()
        self.assertIsNone(stranger.poll(), "the recovery killed the recorded pid")


class Teardown(Console):
    """Stopping, in every way a captain stops a process, and what is left behind."""

    def leftovers(self):
        return sorted(n for n in os.listdir(self.at("inbox")) if n != "console")

    def test_sigterm_releases_the_claim_and_leaves_no_temporary_file(self):
        proc = self.start()
        self.assertEqual(self.stop(proc), 0, self.said(proc))
        self.assertFalse(os.path.exists(self.at("inbox", "console")))
        self.assertEqual(self.leftovers(), [])

    def test_sigint_releases_the_claim_too(self):
        proc = self.start()
        self.assertEqual(self.stop(proc, lambda p: p.send_signal(signal.SIGINT)), 0,
                         self.said(proc))
        self.assertFalse(os.path.exists(self.at("inbox", "console")))
        self.assertEqual(self.leftovers(), [])

    def test_stopping_closes_the_listener_and_every_stream(self):
        proc = self.start()
        s = self.stream()
        s.until("event: state")
        self.stop(proc)
        self.assertFalse(self.listening())
        s.read(5.0)
        self.assertTrue(s.ended, "the stream was left open after the console "
                                f"stopped:\n{s.text}")

    def test_a_kill_leaves_a_record_a_later_start_can_prove_stale(self):
        proc = self.start()
        proc.kill()
        proc.wait(timeout=30)
        self.assertTrue(os.path.exists(self.at("inbox", "console")))
        again = self.start()
        self.assertEqual(self.claim()["pid"], again.pid)
        self.assertEqual(self.request()[0], 200)

    def test_a_bind_that_fails_leaves_no_claim_behind(self):
        """A claim naming a port this console never got.

        The claim is taken before the listener, because a console that bound first
        would leave a window in which a second one found no record at all. So a bind
        that fails has to put it back, or the next start finds a live-looking claim
        for a console that never served anything.
        """
        held = socket.socket()
        held.bind(("127.0.0.1", self.port))
        held.listen(1)
        self.addCleanup(held.close)
        self.refused(self.start(wait=False), "cannot be served")
        self.assertFalse(os.path.exists(self.at("inbox", "console")))
        self.assertEqual(self.leftovers(), [])

    def test_a_second_consoles_refusal_leaves_the_first_ones_claim(self):
        # Releasing on the way out has to mean this process's own record. A second
        # console tidying up after its own refusal would take the claim from a
        # console that is still serving.
        first = self.start()
        self.refused(self.start(port=free_port(), wait=False),
                     "a console is already running")
        self.assertEqual(self.claim()["pid"], first.pid)


class Configuration(Console):
    """The port, which is named and never guessed."""

    def test_an_unset_port_refuses_and_says_how_to_set_one(self):
        self.refused(self.start(env={"SIANA_CONSOLE_PORT": ""}, wait=False),
                     "SIANA_CONSOLE_PORT is not set", "siana-console")

    def test_a_port_that_is_not_a_number_refuses(self):
        for value in ("eight", "80 80", "8080.5", "-1", "0x1f90"):
            with self.subTest(value):
                self.refused(self.start(env={"SIANA_CONSOLE_PORT": value},
                                        wait=False), "SIANA_CONSOLE_PORT")

    def test_asking_for_any_free_port_refuses(self):
        # The claim records the port this console owns and is written before the
        # bind, so a port the kernel picks is one the record could only lie about.
        self.refused(self.start(port=0, wait=False), "SIANA_CONSOLE_PORT is 0")

    def test_a_number_that_is_not_a_port_refuses(self):
        self.refused(self.start(port=70000, wait=False), "is not a port")

    def test_a_refused_start_writes_no_claim(self):
        for env, port in (({"SIANA_CONSOLE_PORT": ""}, None), ({}, 0), ({}, 70000)):
            with self.subTest(env or port):
                self.start(env=env, port=port, wait=False).wait(timeout=30)
                self.assertFalse(os.path.exists(self.at("inbox", "console")))


class TheSlice(unittest.TestCase):
    """What this command is not, read off its source.

    Two of the slice's boundaries cannot be proved by running it. A host it never
    binds in this suite is still a host it could bind on a captain's machine, and an
    endpoint nobody thought to ask for is still an endpoint.

    Read with the comments taken out, because the comments discuss exactly the
    things being searched for - that is what they are there to explain - and a grep
    over them would be a check that can only be passed by never writing down why.
    """

    def setUp(self):
        with open(CONSOLE) as fh:
            self.source = code(fh.read())

    def test_the_comments_are_the_only_thing_stripped(self):
        # A stripper that quietly emptied the file would make every check below
        # pass forever.
        self.assertIn("const HOST = '127.0.0.1'", self.source)
        self.assertIn("http.createServer", self.source)
        self.assertNotIn("A phone cannot run", self.source)

    def test_the_only_address_it_knows_is_the_loopback_one(self):
        for forbidden in ("0.0.0.0", "'::'", '"::"', "localhost", "::ffff:"):
            self.assertNotIn(forbidden, self.source,
                             f"siana-console must not reach for {forbidden!r}")
        self.assertIn("server.listen(port, HOST", self.source)

    def test_the_port_is_the_only_setting_it_reads(self):
        # Every other `SIANA_CONSOLE_*` name belongs to a later slice - the Access
        # team, the audience tag, the origin. One read here early is a setting that
        # looks supported and is not.
        self.assertEqual(set(re.findall(r"SIANA_CONSOLE_\w+", self.source)),
                         {"SIANA_CONSOLE_PORT"})

    def test_it_runs_no_command_but_the_read_boundary_and_ps(self):
        # `siana-read` is the boundary. A console that opened a store, dialled herdr
        # or ran the watcher itself would be a second implementation of refusals
        # that already exist, free to drift from them.
        self.assertEqual(re.findall(r"spawn\(([^,]+),", self.source), ["READ"])
        self.assertEqual(re.findall(r"execFileSync\('([^']+)'", self.source), ["ps"])
        for forbidden in ("node:net", "node:dgram", "node:dns", "createReadStream"):
            self.assertNotIn(forbidden, self.source,
                             f"siana-console must not reach for {forbidden!r}")

    def test_it_carries_nothing_from_a_later_slice(self):
        # Slice 2 is loopback, read-only and unauthenticated on purpose. Half of an
        # authentication check is worse than none, because it looks like one.
        for forbidden in ("jwt", "jwks", "cloudflared", "access-control",
                          "websocket", "sendusermessage"):
            self.assertNotIn(forbidden, self.source.lower(),
                             f"siana-console must not reach for {forbidden!r}")


def code(text):
    """The source with its comments removed.

    Block comments wholesale, and only whole-line `//` comments: a `//` mid-line is
    inside a string here, and cutting from it would take `http://${HOST}` with it.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("//"))


if __name__ == "__main__":
    unittest.main()
