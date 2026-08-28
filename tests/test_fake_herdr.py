"""The one rule the scripted herdr has of its own: its socket has to bind.

Every herdr-facing test in this suite builds a `FakeHerdr` in `setUp`, so a socket
that cannot be bound is not a failure in one test - it is every one of them erroring
before a behaviour is reached, with a message about path lengths that says nothing
about the fleet. That happened once, from a long `$TMPDIR`, and the suite could not
catch it because the suite was what broke. So it is held here.
"""

import os
import tempfile
import unittest
from unittest import mock

from fake_herdr import FakeHerdr
from helpers import HomeTest, script

w = script("siana-watch")


class Socket(HomeTest):

    def test_a_long_tmpdir_does_not_put_the_socket_out_of_reach(self):
        # An `AF_UNIX` path is capped near 104 bytes. A `$TMPDIR` that spends more
        # than that leaves no room for a socket under it, so the socket gets a short
        # directory of its own instead of following the environment down.
        long = self.at(*["a-directory-with-a-name-of-some-length"] * 3)
        os.makedirs(long)
        self.assertGreater(len(long), 104, "this test needs a $TMPDIR past the cap")

        # `tempfile` settles on a temp directory the first time it is asked and
        # remembers it, so unsetting that is what makes this the environment the
        # suite would really be run under rather than the one it started in.
        with mock.patch.dict(os.environ, {"TMPDIR": long}), \
                mock.patch.object(tempfile, "tempdir", None):
            herdr = FakeHerdr().start()
        self.addCleanup(herdr.stop)

        # Bound is not enough on its own: a path the kernel silently truncated would
        # bind and then be reachable at an address nobody can name. So it is asked.
        herdr.reply("agent.get", {"agent": {"agent": "pi"}})
        agent = w.Herdr(herdr.path, timeout=5.0).call("agent.get", target="w1:p1")
        self.assertEqual(agent["agent"]["agent"], "pi")


if __name__ == "__main__":
    unittest.main()
