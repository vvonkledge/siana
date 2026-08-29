/**
 * A pi session, scripted: the extension host reduced to the four things `wake.ts`
 * is allowed to touch, driven a step at a time from `tests/test_wake.py`.
 *
 * Pi is the second boundary in this distro a test cannot drive for real, and it is
 * a harder one than herdr. A live pi wants a terminal, a model, and the captain's
 * credentials, so a suite that drove one would spend money on every run and would
 * still be unable to script the cases that matter: a keystroke landing between two
 * statements, a draft sitting in the editor for nine seconds, an `fs.watch` that
 * never fires. Those are exactly the paths that decide whether the captain's
 * half-written instruction survives a wake.
 *
 * So pi's extension API is scripted and nothing else is. The extension is loaded as
 * itself, from `template/wake.ts`, and it runs on the real node event loop against
 * a real filesystem: its atomic writes, its directory watch and its interval are
 * the ones that ship. What is faked is the six calls it makes back into pi, and
 * each of those records what it was given rather than answering from a script.
 *
 * Three of the recordings are the point rather than a convenience:
 *
 * - `sameTick` says whether the editor was read in the same synchronous block as
 *   the send. A microtask queued at read time has run by the time any `await`
 *   would resume, so an extension that yielded between the two records `false` -
 *   which is the atomic exclusion the whole design rests on, checked rather than
 *   asserted in a comment.
 * - `reads` is every path the extension asked the filesystem for. The extension
 *   must never learn about work from the queue itself, and a test can only hold
 *   that by watching what it actually opens.
 * - `queued` is pi's follow-up buffer, and the `interrupt` command below is what
 *   makes it dangerous the way the real one is. Modelled rather than asserted
 *   against, because "the extension passed no `deliverAs`" is a statement about
 *   one argument, and what has to hold is that nothing the extension does can put
 *   machine text in the captain's editor.
 *
 * Protocol: one JSON command per line on stdin, one JSON reply per line on stdout.
 * Every reply carries the whole state, so a test asserts on what it wants without
 * having to ask for it first.
 */

import fs from "node:fs";
import readline from "node:readline";

const extension = process.argv[2];
if (!extension) {
  console.error("usage: fake_pi.mjs <path to wake.ts>");
  process.exit(2);
}

// Every filesystem call the extension makes, recorded. Patched on the module
// object rather than wrapped around the extension, because these are the calls it
// really makes: a wrapper would only ever record what this file already believed.
const reads = [];
const writes = [];
const removed = [];
const realReadFileSync = fs.readFileSync;
const realWriteFileSync = fs.writeFileSync;
const realUnlinkSync = fs.unlinkSync;
const realRenameSync = fs.renameSync;
const realWatch = fs.watch;
const realSetInterval = globalThis.setInterval;
const realClearInterval = globalThis.clearInterval;

fs.readFileSync = (path, ...rest) => {
  reads.push(String(path));
  return realReadFileSync(path, ...rest);
};
fs.writeFileSync = (path, ...rest) => {
  writes.push(String(path));
  return realWriteFileSync(path, ...rest);
};
fs.unlinkSync = (path, ...rest) => {
  removed.push(String(path));
  return realUnlinkSync(path, ...rest);
};
// A disk that will not take the write. Wrapped at the rename because that is where
// a staged write becomes the file, so a failure here is the one that leaves the
// counter on disk behind what has actually been delivered.
let renameThrows = false;
fs.renameSync = (from, to, ...rest) => {
  if (renameThrows) throw new Error("nothing can be written here");
  return realRenameSync(from, to, ...rest);
};

const sent = [];
// Sends the session refused, which pi does for real when a message cannot be
// delivered. They are kept apart from `sent` so a test can say the extension tried
// and still did not record the wake as taken.
const refused = [];
let sendThrows = false;
// Pi's follow-up buffer: a message handed to a turn in flight waits here rather
// than being appended. It is a real queue here and not a counter because
// `interrupt` empties it back into the editor, which is what pi does.
const queued = [];
const editorWrites = [];
let editor = "";
let idle = true;
// True only between a read of the editor and the end of that synchronous block.
let sameTick = false;

const pi = {
  handlers: new Map(),
  on(event, handler) {
    pi.handlers.set(event, handler);
  },
  sendUserMessage(content, options) {
    if (sendThrows) {
      refused.push({ content, options: options ?? null });
      throw new Error("this session took no message");
    }
    sent.push({
      content,
      options: options ?? null,
      sameTick,
      editorAtSend: editor,
      idleAtSend: idle,
    });
    // A follow-up is queued, not appended. Recorded in `sent` as well, so a test
    // can still see that the extension tried to send something.
    if (options?.deliverAs === "followUp") queued.push(content);
  },
  // Present so that reaching for either is a recorded failure rather than a
  // TypeError that reads like a harness bug. The wake must never arrive as a
  // custom message: only `sendUserMessage` fires `before_agent_start`, which is
  // where the tasks package injects the ambient queue.
  sendMessage(message, options) {
    sent.push({ wrongApi: "sendMessage", message, options: options ?? null });
  },
  appendEntry() {},
  registerTool() {},
  registerCommand() {},
};

const ctx = {
  cwd: process.cwd(),
  hasUI: true,
  mode: "tui",
  isIdle: () => idle,
  ui: {
    getEditorText() {
      sameTick = true;
      queueMicrotask(() => {
        sameTick = false;
      });
      return editor;
    },
    setEditorText(text) {
      editorWrites.push({ call: "setEditorText", text });
      editor = text;
    },
    pasteToEditor(text) {
      editorWrites.push({ call: "pasteToEditor", text });
      editor += text;
    },
    notify() {},
    setStatus() {},
  },
};

function state(extra = {}) {
  return { pid: process.pid, sent, refused, queued, editor, editorWrites, reads,
           writes, removed, ...extra };
}

async function fire(event, payload) {
  const handler = pi.handlers.get(event);
  if (!handler) return { error: `the extension registered no ${event} handler` };
  await handler(payload, ctx);
  return {};
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Wait for the extension to have sent `count` messages, or give up and let the
 *  test assert on what did happen. Polling rather than a fixed wait: a fixed one
 *  is either slow or flaky, and under load it is both. */
async function settle(count, timeout) {
  const deadline = Date.now() + timeout;
  while (sent.length < count && Date.now() < deadline) await sleep(10);
  return {};
}

async function run(command) {
  switch (command.cmd) {
    case "break-watch":
      // An fs.watch this platform will not give. The interval backstop is the only
      // thing left, so a wake that still arrives arrived through it.
      fs.watch = () => {
        throw new Error("no watch here");
      };
      return {};
    case "restore-watch":
      fs.watch = realWatch;
      return {};
    case "break-interval":
      // The backstop taken away, so a wake that still arrives arrived through the
      // directory watch. That is the only way to hold the rule the watch exists
      // for: a watch on the file itself is dead after the first atomic rename, and
      // with the interval running the interval would cover for it.
      globalThis.setInterval = () => ({ unref() {} });
      globalThis.clearInterval = () => {};
      return {};
    case "restore-interval":
      globalThis.setInterval = realSetInterval;
      globalThis.clearInterval = realClearInterval;
      return {};
    case "start":
      return fire("session_start", { reason: command.reason ?? "startup" });
    case "shutdown":
      return fire("session_shutdown", { reason: command.reason ?? "quit" });
    case "refuse-writes":
      renameThrows = command.value;
      return {};
    case "refuse-sends":
      sendThrows = command.value;
      return {};
    case "editor":
      editor = command.text;
      return {};
    case "idle":
      idle = command.value;
      return {};
    case "interrupt":
      // Escape during a streaming turn. Pi's TUI calls
      // `restoreQueuedMessagesToEditor({abort: true})`, which sets the editor to
      // the queued text joined ahead of whatever the captain had already typed:
      // `setText([queuedText, currentText].filter(Boolean).join("\n\n"))`. That is
      // the whole reason a wake is never handed to a turn in flight, so it is
      // modelled here rather than described in a test name. Written straight to
      // `editor` and not through `ctx.ui`, because this is pi writing and
      // `editorWrites` is the record of what the extension wrote.
      editor = [queued.join("\n\n"), editor].filter(Boolean).join("\n\n");
      queued.length = 0;
      idle = true;
      return {};
    case "settle":
      return settle(command.sent, command.timeout ?? 10_000);
    case "quiet":
      // Long enough for the backstop to have fired, so "nothing was sent" is a
      // statement about the extension and not about the test being in a hurry.
      await sleep(command.ms ?? 1500);
      return {};
    case "state":
      return {};
    default:
      return { error: `no such command: ${command.cmd}` };
  }
}

const mod = await import(extension);
mod.default(pi);

const lines = readline.createInterface({ input: process.stdin });
for await (const line of lines) {
  if (!line.trim()) continue;
  let reply;
  try {
    reply = await run(JSON.parse(line));
  } catch (e) {
    reply = { error: `${e}` };
  }
  process.stdout.write(JSON.stringify(state(reply)) + "\n");
}

// Stdin closed, so the test is done with this session. Explicit, because a watch or
// a timer the extension left armed would otherwise hold this process open and the
// suite would hang where it should have failed.
process.exit(0);
