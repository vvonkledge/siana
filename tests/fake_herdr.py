"""A herdr, scripted: one unix socket, one thread, and whatever answers a test needs.

Herdr is the one boundary in this distro that a test cannot drive for real. A live
server wants a terminal, a machine with one, and an agent whose first frame arrives
when it arrives - so the answers that matter most here, the ones where herdr is slow,
wrong, or gone, are exactly the ones a live server could never be made to give on
cue. Left untested, those are the paths that decide whether a minion is alive,
whether its prompt landed, and whether a half-made dispatch is cleaned up.

So herdr's transport is scripted and nothing else is. The commands still open a real
unix socket, speak the real line protocol and parse the real replies, because that
framing is where `Unreachable` is decided, and a reader that gets it wrong reports a
live fleet as dead. `tasks` and `datafile` stay real for the reason the rest of this
suite keeps them real: a stub store would only ever agree with what the suite already
believed about it.
"""

import _thread
import json
import os
import shutil
import socket
import tempfile
import threading


def socket_dir():
    """A directory short enough for a socket to live in, whatever `$TMPDIR` says.

    An `AF_UNIX` path is capped near 104 bytes, and a default macOS temp directory
    already spends most of that. A home under `$TMPDIR` is therefore not somewhere a
    socket can live: point `$TMPDIR` at anything nested and every herdr-facing test
    in the suite errors in `setUp`, before a single behaviour is exercised. So the
    socket gets its own short directory and nothing else moves.

    Asked per socket rather than settled at import, so the answer is the one that
    holds when a `FakeHerdr` is actually built - which is the only place the length
    of `$TMPDIR` can hurt anyone, and the only place a test can hold this."""
    return "/tmp" if os.path.isdir("/tmp") else tempfile.gettempdir()


# An answer meaning: take the request, then close without saying anything. It is how
# herdr presents when its server stops mid-request, and it is the only way to reach
# the "closed without a response" arm that both commands read as `Unreachable`.
CLOSE = object()


class HerdrError(Exception):
    """An error reply, in the shape herdr sends one.

    Distinct from a dead connection on purpose: both commands treat a refusal as an
    answer about the thing asked after, and silence as an answer about nothing.
    """

    def __init__(self, code="refused", message="herdr refused it"):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


class FakeHerdr:
    """Scripted answers, served over a real socket.

    Constructed without a path, it binds one in a short directory of its own and
    cleans it up on `stop`.

    `reply(method, *answers)` queues answers for a method; the last one repeats, so
    a steady state is one answer and a sequence is as many as the test cares about.
    An answer is a result dict, a `HerdrError` to refuse with, `CLOSE`, or a callable
    taking the request's params - which is also the only clock a test has for making
    something happen while a command is mid-loop.
    """

    # A scripted loop that never terminates would hang the whole suite rather than
    # fail it, and the two commands under test both contain a `while`. So a run that
    # goes far past any real dispatch interrupts the main thread instead: a test that
    # fails is readable, and a suite that hangs is not.
    MAX_CALLS = 500

    def __init__(self, path=None):
        self._dir = (None if path else
                     tempfile.mkdtemp(prefix="herdr-", dir=socket_dir()))
        self.path = path or os.path.join(self._dir, "herdr.sock")
        self.calls = []                       # (method, params), in order
        self._answers = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = self._thread = None

    def reply(self, method, *answers):
        with self._lock:
            self._answers[method] = list(answers)
        return self

    def calls_to(self, method):
        return [params for name, params in self.calls if name == method]

    def _answer(self, method, params):
        with self._lock:
            queued = self._answers.get(method)
            # An unscripted method answers with an empty result rather than an error.
            # Several calls here are made for their effect alone - pane.close,
            # workspace.close, agent.start - and a test forced to script every one of
            # them would say least about the one thing it is actually asserting.
            if not queued:
                return {}
            answer = queued[0] if len(queued) == 1 else queued.pop(0)
        return answer(params) if callable(answer) else answer

    def start(self):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # A timeout rather than a blocking accept, so `stop` is never waiting on a
        # connection that is never coming.
        self._sock.settimeout(0.05)
        self._sock.bind(self.path)
        self._sock.listen(16)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        if self._sock:
            self._sock.close()
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(5)
                try:
                    self._serve_one(conn)
                except OSError:
                    pass          # The caller hung up. Not this server's business.

    def _serve_one(self, conn):
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
        request = json.loads(buf.split(b"\n", 1)[0])
        method, params = request.get("method"), request.get("params") or {}
        self.calls.append((method, params))
        if len(self.calls) > self.MAX_CALLS:
            _thread.interrupt_main()
            return
        try:
            answer = self._answer(method, params)
        except HerdrError as e:
            answer = e
        if answer is CLOSE:
            return
        if isinstance(answer, HerdrError):
            body = {"error": {"code": answer.code, "message": answer.message}}
        else:
            body = {"result": answer}
        conn.sendall((json.dumps({"id": request.get("id"), **body}) + "\n").encode())
