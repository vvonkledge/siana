"""The browser application: its toolchain, its build, and its own suite.

This distro had no npm in it before this slice, and adding one is the thing worth
guarding. So three rules are checked here rather than remembered:

**The install is reproducible.** A lockfile, pinned to exact versions, with an
integrity hash on every package. Without that, `npm ci` on a clean runner installs
something nobody reviewed into the process that serves the captain's queue.

**The build is deterministic.** The same source builds to the same bytes, twice,
which is what makes "the tests ran against what is served" mean anything.

**The application's own tests run here.** They are node's test runner driving the
built bundle in jsdom, and they are run from this suite so that `just test` is still
the one thing that has to be green. One case per file, so a failure names the file
it is in rather than a single red line covering the whole frontend.

Nothing here reaches the network beyond the package registry `npm ci` uses, which
`just build` has already done by the time this runs.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from helpers import DISTRO

APP = os.path.join(DISTRO, "console")
DIST = os.path.join(APP, "dist")
SUITE = os.path.join(APP, "test")
NODE = shutil.which("node")
NPM = shutil.which("npm")

# What the application's own tests are driven with. Node's runner, one file at a
# time: `--test` on a directory is not a thing it takes, and a file at a time is what
# gives each of them a name in this suite.
RUNNER = ["--test"]


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def manifest():
    return json.loads(read(APP, "package.json"))


def lockfile():
    return json.loads(read(APP, "package-lock.json"))


class Toolchain(unittest.TestCase):
    """The dependency boundary this slice introduced, as rules rather than as a
    habit."""

    def test_the_manifest_pins_every_dependency_to_an_exact_version(self):
        # A range is a different tree on a different day, in a process that reads the
        # captain's whole queue. The lockfile pins the tree; this pins what the
        # lockfile is allowed to be resolved from, so a `npm install` that refreshed
        # it cannot quietly move a major version.
        found = manifest()
        for section in ("dependencies", "devDependencies"):
            for name, version in found[section].items():
                self.assertRegex(version, r"^\d+\.\d+\.\d+$",
                                 f"{name} is not pinned to an exact version")

    def test_the_lockfile_is_committed_and_is_the_one_npm_ci_needs(self):
        found = lockfile()
        self.assertGreaterEqual(found["lockfileVersion"], 3)
        self.assertEqual(found["name"], manifest()["name"])

    def test_every_locked_package_carries_an_integrity_hash_and_a_registry(self):
        # `npm ci` verifies these. A package with no integrity is one whose contents
        # nothing checks, and a package resolved from anywhere but the registry is a
        # dependency this repository did not choose.
        missing, elsewhere = [], []
        for path, entry in lockfile()["packages"].items():
            if not path or entry.get("link"):
                continue
            if not entry.get("integrity"):
                missing.append(path)
            resolved = entry.get("resolved", "")
            if not resolved.startswith("https://registry.npmjs.org/"):
                elsewhere.append(f"{path} <- {resolved}")
        self.assertEqual(missing, [], "packages with no integrity hash")
        self.assertEqual(elsewhere, [], "packages resolved from somewhere else")

    def test_the_manifest_runs_nothing_at_install_time(self):
        # `preinstall`, `postinstall` and friends run whatever they like the moment
        # somebody types `npm ci`. This project's own manifest has two scripts and
        # both are things a person asks for.
        self.assertEqual(sorted(manifest()["scripts"]), ["build", "test"])

    def test_only_the_one_known_dependency_runs_anything_at_install_time(self):
        # The same rule, one level down: an install script in a transitive package is
        # arbitrary code executed by `just test` on every clean runner. npm records
        # which packages have one, so this is an exact set rather than an absence.
        #
        # `fsevents` is the one, and it is the one because it cannot be dropped
        # without dropping the bundler: it is an optional, macOS-only native binding
        # the file watcher uses, pulled in as an optional dependency of the build
        # tool. Nothing in this project runs a watcher - there is no dev server here,
        # only `vite build` - and on a Linux runner it is not installed at all. A
        # second name appearing in this list is a new dependency running code at
        # install time, and it is worth stopping to look at.
        with_scripts = sorted(path for path, entry in lockfile()["packages"].items()
                              if entry.get("hasInstallScript"))
        self.assertEqual(with_scripts, ["node_modules/fsevents"])

    def test_the_build_output_and_the_packages_are_not_committed(self):
        ignored = read(DISTRO, ".gitignore")
        self.assertIn("console/node_modules/", ignored)
        self.assertIn("console/dist/", ignored)

    def test_the_application_is_the_only_place_npm_reaches(self):
        # Two package.json in the repository, and they are not the same kind of
        # thing. The frontend's is an installed dependency tree, and the rest of this
        # class is what checks it. The pi package's is a manifest of resources a
        # harness discovers. It declares peers on packages pi already bundles and
        # nothing else - no `dependencies` and no `bundledDependencies`, which
        # `test_package.py` is what holds it to - and the justfile installs it with
        # `pi install -l -a` off a local path, which never runs npm. So not even
        # those peers are resolved here, and there is no lockfile to check.
        #
        # This asserted `["console"]` alone until that package landed, and the count
        # is not what it was guarding. A third manifest anywhere is a second
        # dependency tree nothing here checks, so the list stays exact rather than
        # becoming a rule about where manifests may live. The lockfile line is what
        # keeps "npm reaches" meaning an installed tree: without it a new root could
        # bring one and pass by being named.
        roots, locked = [], []
        for dirpath, dirnames, filenames in os.walk(DISTRO):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d != "node_modules"]
            here = os.path.relpath(dirpath, DISTRO)
            if "package.json" in filenames:
                roots.append(here)
            if "package-lock.json" in filenames:
                locked.append(here)
        self.assertEqual(sorted(roots), ["console", "template/pi-siana"])
        self.assertEqual(sorted(locked), ["console"])


@unittest.skipUnless(NPM and NODE, "the frontend toolchain runs on node and npm")
class TheBuild(unittest.TestCase):
    """What `just build` produced, and that producing it twice produces the same
    thing."""

    def setUp(self):
        if not os.path.isdir(DIST):
            self.fail(f"{DIST} holds no build, and npm is installed on this machine,"
                      " so `just build` should have made one. Run `just test` rather"
                      " than the suite directly, or run `just build` first.")

    def test_the_same_source_builds_to_the_same_bytes(self):
        # Reproducible, so that "the suite tested what the console serves" is a fact
        # about the source rather than about the machine that happened to build it.
        # Built into two temporary directories rather than over `dist/`, because the
        # rest of this suite is reading that one while this runs.
        first, second = self.build(), self.build()
        self.assertEqual(self.fingerprint(first), self.fingerprint(second))

    def test_the_build_is_the_one_the_console_is_serving(self):
        # The temporary build and the committed-in-place one have to agree, or
        # `just build` is not what made what is being tested.
        self.assertEqual(self.fingerprint(self.build()), self.fingerprint(DIST))

    def build(self):
        out = tempfile.mkdtemp(prefix="siana-app-build-")
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        done = subprocess.run(
            [NPM, "run", "build", "--", "--outDir", out, "--emptyOutDir"],
            cwd=APP, capture_output=True, text=True, timeout=600)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return out

    def fingerprint(self, root):
        """Every file in a build, by name and by contents."""
        found = {}
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                path = os.path.join(dirpath, name)
                with open(path, "rb") as fh:
                    found[os.path.relpath(path, root)] = fh.read()
        return found


class TheSource(unittest.TestCase):
    """What the application is not, read off its own source.

    Two of this slice's boundaries cannot be proved by running it: an origin nobody
    fetched today is still an origin in the bundle, and a control that does not exist
    yet is still one somebody could add without noticing what it implies.
    """

    def setUp(self):
        self.files = {}
        for where in ("src", "tools"):
            for name in sorted(os.listdir(os.path.join(APP, where))):
                if name.endswith((".js", ".jsx", ".css", ".mjs")):
                    self.files[f"{where}/{name}"] = read(APP, where, name)
        self.files["index.html"] = read(APP, "index.html")
        self.files["manifest"] = read(APP, "public", "manifest.webmanifest")

    def code(self, text):
        """The source with its comments taken out.

        The comments discuss exactly the things searched for below - that is what
        they are there to explain - and a grep over them is a check that can only be
        passed by never writing down why.
        """
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return "\n".join(line for line in text.splitlines()
                         if not line.lstrip().startswith("//"))

    def test_the_comments_are_the_only_thing_stripped(self):
        # A stripper that quietly emptied the files would make every check below pass
        # forever.
        self.assertIn("createRoot", self.code(self.files["src/main.jsx"]))
        self.assertNotIn("The entry point, and the one place",
                         self.code(self.files["src/main.jsx"]))

    def test_the_sources_hold_no_origin_but_this_console(self):
        # The build refuses to emit one (`tools/plugins.mjs`) and `console/test/
        # assets.test.mjs` proves the served bytes carry none. This is the third
        # place, and it is the one that catches a CDN pasted into a source file
        # before anybody runs a build.
        offenders = []
        for name, text in self.files.items():
            if name.startswith("tools/"):
                continue
            for found in re.findall(r"https?://[^\s\"'`)]*", self.code(text)):
                offenders.append(f"{name}: {found}")
        self.assertEqual(offenders, [])

    def test_no_fleet_string_can_be_rendered_as_markup(self):
        for name, text in self.files.items():
            for forbidden in ("dangerouslySetInnerHTML", "innerHTML", "outerHTML",
                              "insertAdjacentHTML", "document.write"):
                self.assertNotIn(forbidden, self.code(text),
                                 f"{name} reaches for {forbidden}")

    def test_it_asks_for_nothing_but_the_two_documented_routes(self):
        joined = "\n".join(self.code(text) for name, text in self.files.items()
                           if name.startswith("src/"))
        self.assertEqual(sorted(set(re.findall(r"'(/api/[a-z]+)'", joined))),
                         ["/api/state", "/api/stream"])
        for forbidden in ("XMLHttpRequest", "WebSocket", "sendBeacon", "importScripts"):
            self.assertNotIn(forbidden, joined, f"the app reaches for {forbidden}")

    def test_it_persists_nothing_but_the_one_cached_snapshot(self):
        # The service worker holds one `/api/state` response, and that is the whole of
        # what offline needs. A second copy in the page would be a second answer to
        # "what did we last know", free to disagree with the first.
        for name, text in self.files.items():
            if name == "src/sw.js":
                continue
            for forbidden in ("localStorage", "sessionStorage", "indexedDB",
                              "document.cookie", "caches"):
                self.assertNotIn(forbidden, self.code(text),
                                 f"{name} keeps state of its own in {forbidden}")

    def test_it_carries_nothing_from_a_later_slice(self):
        # This slice reads. A composer, a mailbox or a write path arriving early is
        # a control the captain would try to use and that nothing behind it honours.
        joined = " ".join(self.code(text) for text in self.files.values()).lower()
        for forbidden in ("jwt", "jwks", "cloudflare", "cloudflared", "composer",
                          "mailbox", "sendusermessage", "notification",
                          "'post'", '"post"', "websocket", "gtag", "analytics"):
            self.assertNotIn(forbidden, joined,
                             f"the application reaches for {forbidden!r}")

    def test_the_manifest_asks_for_no_permission_and_no_handler(self):
        # A web app manifest can register the app as a share target, a protocol
        # handler or a file handler. Every one of those is an inbound path into a
        # process that has no write endpoint to honour it with.
        found = json.loads(self.files["manifest"])
        for forbidden in ("share_target", "protocol_handlers", "file_handlers",
                          "shortcuts", "related_applications"):
            self.assertNotIn(forbidden, found)


@unittest.skipUnless(NODE, "the application's own tests run on node")
class TheApplication(unittest.TestCase):
    """The application's own suite, driven from here so that `just test` is still the
    one thing that has to be green.

    Each of those files loads `dist/` into jsdom and drives the real bundle. They are
    run as processes rather than reimplemented, because what they assert about is
    JavaScript running in a document and there is nothing in Python that could stand
    in for that.
    """

    maxDiff = None

    def drive(self, name):
        if not os.path.isdir(DIST):
            if not NPM:
                self.skipTest("no npm on this machine, so nothing was built to test")
            self.fail(f"{DIST} holds no build; run `just test`, which builds first")
        done = subprocess.run([NODE, *RUNNER, os.path.join(SUITE, name)],
                              cwd=APP, capture_output=True, text=True, timeout=600)
        self.assertEqual(done.returncode, 0,
                         f"{name}\n{done.stdout}\n{done.stderr}")


def _case(name):
    def test(self):
        self.drive(name)
    test.__doc__ = f"console/test/{name}"
    return test


# One case per file, discovered rather than listed: a test file nobody remembered to
# add to a list is exactly the one that stops being run.
for _name in sorted(n for n in os.listdir(SUITE) if n.endswith(".test.mjs")):
    setattr(TheApplication, f"test_{_name.replace('.test.mjs', '')}", _case(_name))


if __name__ == "__main__":
    unittest.main()
