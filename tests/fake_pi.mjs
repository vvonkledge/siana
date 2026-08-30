/**
 * A pi session, scripted: the extension host reduced to what `wake.ts` is allowed to
 * touch, driven a step at a time from `tests/test_wake.py`.
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
 * the ones that ship. What is faked is the calls it makes back into pi and the
 * events pi hands it, and each of those records what it was given rather than
 * answering from a script.
 *
 * The one thing modelled here rather than recorded is how a send is answered, and
 * it is modelled because the truthful answer is what the extension has to be held
 * to. `sendUserMessage` is `void`: it hands the prompt to `prompt()`, whose
 * rejection pi catches into an error channel with no extension event behind it. So
 * a refused send throws nothing back, returns exactly like an accepted one, and
 * reaches the captain's chat rather than the extension. What does come back is a
 * pair of events in a fixed order - `input`, carrying the source, from inside the
 * send call; then `before_agent_start`, carrying the text, a task later and only
 * for a prompt pi kept. `prompt()` below is that order, and it is the whole of what
 * an extension can tell about whether its wake landed.
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
// The clock the extension reads, which is deliberately not the one the event loop
// runs on. Two of its rules are ages - how long a send pi never took is left alone,
// and how long one pi took and never started is waited for - and the second is
// minutes, because the legitimate gap it must not cut short is an automatic
// compaction's round trip. Sleeping through that would put minutes into a suite
// that has to stay in seconds, and would still be a race rather than a rule. So a
// test moves this offset and leaves the timers alone: the poll goes on firing at
// its real cadence and reads an age that has really passed as far as the extension
// can tell.
const realNow = Date.now;
let clockOffset = 0;
Date.now = () => realNow() + clockOffset;

// A disk that will not take the write. Wrapped at the rename because that is where
// a staged write becomes the file, so a failure here is the one that leaves the
// counter on disk behind what has actually been delivered.
let renameThrows = false;
fs.renameSync = (from, to, ...rest) => {
  if (renameThrows) throw new Error("nothing can be written here");
  return realRenameSync(from, to, ...rest);
};

const sent = [];
// Sends pi's `prompt()` threw on before it reached its input gate, which is what a
// manual compaction does for the whole of its duration while `isIdle()` is still
// true. They are kept apart from `sent` so a test can say the extension tried and
// still did not record the wake as taken.
//
// Nothing is thrown back at the extension here, because nothing is thrown back at
// it there. `ExtensionAPI.sendUserMessage` is declared `void` and implemented as
// `this.sendUserMessage(...).catch(err => runner.emitError(...))`, and there is no
// `extension_error` event to subscribe to: the rejection reaches the captain's
// chat transcript as a red line and reaches the extension not at all. A fake that
// threw would let an extension pass here by catching something the real host never
// gives it.
const refused = [];
let sendRejects = false;
// A prompt pi took past its input gate and then threw on: no model selected,
// credentials that expired, a run that began between the gate and the streaming
// check, an automatic compaction that failed. The extension has seen its own send
// go in and will never be told what became of it, which is a different shape from
// `sendRejects` and not covered by it.
let startRejects = false;
// Every `before_agent_start` pi emitted, which is the first moment a prompt is
// known to have been accepted: `prompt()` emits it after all four of its throw
// paths and immediately before it starts the run.
const starts = [];
// The start of a prompt this extension sent, held rather than emitted, so a test
// can stand inside the window between an accepted send and its confirmation and
// drive what else the session does there. Only this extension's own sends are
// held: a session where nothing else could start a turn would make every question
// about telling turns apart unanswerable.
let holdStarts = false;
const heldStarts = [];
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
    if (sendRejects) {
      // `prompt()` throws here, before its input gate, and the throw goes nowhere
      // the extension can see. The call still returns normally, which is the whole
      // reason a void send cannot be read as an acceptance. Recorded apart from
      // `sent`, so that `sent` stays what pi took rather than what it was handed.
      refused.push({ content, options: options ?? null, sameTick });
      return;
    }
    sent.push({
      content,
      options: options ?? null,
      sameTick,
      editorAtSend: editor,
      idleAtSend: idle,
    });
    prompt(typeof content === "string" ? content : "", "extension",
           options?.deliverAs, holdStarts);
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

/**
 * One trip through pi's `prompt()`, reduced to the two events an extension can see
 * and to the order it really sees them in.
 *
 * `input` comes first and carries where the prompt came from: "extension" for a
 * send like the one above, "interactive" for the captain typing. It is emitted
 * from inside the send call, because `prompt()` calls `emitInput` before it awaits
 * anything and the runner calls the first handler synchronously.
 *
 * `before_agent_start` comes after every one of `prompt()`'s throw paths - model,
 * auth, streaming, the compaction re-check - and immediately before the run starts,
 * so it is the first evidence a prompt was accepted. It carries no source, which is
 * why an extension needs both events and not this one. It is emitted a task later
 * because that gap is real: the auth check and the compaction re-check between the
 * two are awaited, and one of them is a round trip.
 *
 * A streaming send with a `deliverAs` is queued after the input event and never
 * reaches a start, which is pi's order too.
 */
function prompt(text, source, deliverAs, holdable) {
  const input = pi.handlers.get("input");
  if (input) input({ type: "input", text, source, streamingBehavior: deliverAs },
                   ctx);
  if (deliverAs) {
    queued.push(text);
    return;
  }
  if (startRejects) return;
  // Asked when the start would fire and not when the prompt was made: a test
  // reaches for the hold after seeing the send, which is already a task late.
  setTimeout(() => {
    if (holdable && holdStarts) heldStarts.push(text);
    else start(text);
  }, 0);
}

function start(text) {
  starts.push(text);
  const handler = pi.handlers.get("before_agent_start");
  if (handler) handler({ type: "before_agent_start", prompt: text, systemPrompt: "" },
                       ctx);
}

function state(extra = {}) {
  return { pid: process.pid, sent, refused, starts, queued, editor, editorWrites,
           reads, writes, removed, ...extra };
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
      // A session that reports idle and refuses the prompt anyway. Manual
      // compaction is the shape that matters: `isIdle()` is `!_isAgentRunActive`
      // and `compact()` settles the run before it starts, so the whole of a
      // `/compact` is a stable window where the gate says yes and the send is
      // thrown away.
      sendRejects = command.value;
      return {};
    case "refuse-starts":
      // Refused after the input gate rather than before it. Every throw path in
      // `prompt()` except the compaction one is this shape, and from the
      // extension's side it is a send that went in and never came out.
      startRejects = command.value;
      return {};
    case "advance":
      clockOffset += command.ms;
      return {};
    case "hold-starts":
      // Stand inside the window between an accepted send and its confirmation.
      // Releasing emits what was held, in the order pi would have.
      holdStarts = command.value;
      if (!holdStarts) {
        const release = heldStarts.splice(0);
        for (const text of release) start(text);
      }
      return {};
    case "prompt":
      // A prompt from somewhere that is not this extension's wake: the captain
      // typing, or another extension sending. Same two events in the same order,
      // which is the point - nothing about the shape of a turn says whose it is.
      prompt(command.text, command.source ?? "interactive");
      return {};
    case "agent-start":
      // A run starting with no input event of its own in front of it. Nothing in
      // pi does this, and that is why it is here: `before_agent_start` alone must
      // never be read as an acceptance.
      start(command.text ?? "");
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
