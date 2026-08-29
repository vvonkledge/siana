/**
 * Deliver the watcher's wake into SIANA's own session, without touching the editor.
 *
 * `siana-watch` used to call herdr's `agent.prompt`, which writes into the same
 * input editor the captain types in: a draft sitting there was concatenated with
 * the wake and submitted as one user message, so the captain's half-written
 * instruction was sent under their own name and could no longer be revised or
 * cancelled. Herdr has no conditional write and no idea what is in that editor, so
 * no amount of checking before the write can close that race.
 *
 * This closes it by moving delivery inside pi. `siana-watch` now only raises a
 * counter in `$SIANA_HOME/wake/pending`, and this extension delivers the wake with
 * `pi.sendUserMessage()`, which appends a user message and never goes near the
 * editor. Two writers, two files, and no shared surface: the watcher only ever
 * writes `pending`, and this only ever writes `consumed` and `consumer`.
 *
 * `sendUserMessage` rather than `sendMessage({customType}, {triggerTurn})`, which
 * was measured and is not a preference: only the first fires `before_agent_start`,
 * and that is the hook the tasks package injects the ambient queue view from. A
 * custom message would run SIANA's reconcile turn with the queue missing from it.
 *
 * This never reads `tasks.jsonl`. Starting `siana-watch` is the captain's autonomy
 * grant, given by starting that process and withdrawn by stopping it. An extension
 * that read the queue itself would advance the fleet whenever SIANA was running, so
 * merely opening a session would confer the grant and stopping the watcher would
 * not withdraw it. It stays a dumb consumer of a file only the watcher writes.
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import { join } from "node:path";

/** What the wake says, and the whole of what it knows. It carries no summary on
 *  purpose: a summary able to disagree with the queue would be a second source of
 *  truth about work SIANA is one command away from reading properly. */
export const WAKE = "The queue moved. Reconcile it.";

/** The backstop under the directory watch. `fs.watch` is the fast path and is
 *  allowed to miss an event, or to be unavailable on this filesystem entirely, so
 *  a wake's worst case has to be bounded by something that does not depend on it.
 *  Half a second against the watcher's two-second poll: invisible either way. */
export const POLL_MS = 500;

/** Where the two counters and the liveness record live. The watcher looks here by
 *  the same name, so this is a shared constant in spirit and neither side may
 *  rename it alone. */
export const WAKE_DIR = "wake";

/** The one name in that directory another process writes, and so the only one the
 *  watch below has anything to learn from. */
export const PENDING = "pending";

interface Paths {
  dir: string;
  pending: string;
  consumed: string;
  consumer: string;
}

/**
 * The number in a counter file, or 0 when there is not one.
 *
 * An absent file is the zero: no wake has ever been raised. Anything that will not
 * parse reads as the zero too, and that is not a silent loss: the watcher is the
 * only writer of `pending` and writes it whole with an atomic rename, so an
 * unreadable one is a hand-edit, and the watcher says so on its own held cadence
 * once its count has run ahead of what this records as taken.
 */
function counter(path: string): number {
  let text: string;
  try {
    text = fs.readFileSync(path, "utf8");
  } catch {
    return 0;
  }
  const n = Number.parseInt(text.trim(), 10);
  return Number.isSafeInteger(n) && n >= 0 ? n : 0;
}

/**
 * A counter written beside its destination, so it lands whole or not at all.
 *
 * The watcher reads `consumed` while this writes it, and a reader that caught it
 * half-written would see a number that was never true - which for a high-water
 * mark means reporting wakes as taken that were not.
 */
function writeCounter(path: string, value: number): void {
  const tmp = `${path}.${process.pid}`;
  fs.writeFileSync(tmp, `${value}\n`);
  fs.renameSync(tmp, path);
}

/**
 * What `ps` says this process is running, or "" when it says nothing.
 *
 * Identity, not liveness. Pids are reused, so the watcher cannot read a live pid
 * as a live consumer: the process wearing it has to still be the pi that recorded
 * it. `siana-watch` asks the same question of its own grant record and asks it the
 * same way, so the two strings are comparable by construction.
 */
function processCommand(pid: number): string {
  try {
    return execFileSync("ps", ["-p", String(pid), "-o", "command="], {
      encoding: "utf8",
      timeout: 10_000,
    }).trim();
  } catch {
    return "";
  }
}

export default function (pi: ExtensionAPI): void {
  let paths: Paths | null = null;
  let watcher: fs.FSWatcher | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;
  // Three marks, and the split between the first two is load-bearing. `consumed` is
  // what has been delivered and is what stops a wake going twice; `recorded` is what
  // the file says, which is what the watcher reads. They come apart when the write
  // fails - the directory gone, the disk full - and they have to, because a delivery
  // already made must never be retried on the strength of a write that did not land.
  // `held` is the highest count seen raised.
  let consumed = 0;
  let recorded = 0;
  let held = 0;

  /**
   * Deliver the wake, or hold it, in one synchronous block.
   *
   * This is the exclusion the whole design rests on. Pi's TUI is a single-threaded
   * event loop and keystrokes are callbacks on it, so a function that reads the
   * editor and sends without yielding cannot be interleaved with a keystroke. The
   * read and the send are therefore one operation, which is the thing `siana-watch`
   * could never have however carefully it checked first.
   *
   * Never make this `async` and never `await` anything inside it. A single await
   * turns the exclusion back into a window, and it would be a window nothing here
   * could detect.
   *
   * A wake goes out only into an idle session with an empty editor, and the idle
   * half is safety rather than manners. A busy session has no collision-free
   * delivery at all: pi's one way to hand a message to a turn in flight is a queued
   * follow-up, and its TUI restores queued messages into the input editor when the
   * captain interrupts with Escape - it sets the editor to the queued text joined
   * ahead of whatever they had started typing. That is machine text in the
   * captain's editor, which is the exact surface and the exact shape of the bug
   * this whole path removes. So a busy session holds, and the wake goes out as a
   * turn of its own once it is not. Holding costs a delay and never a wake: the
   * count is durable and nothing below moves until a send has actually gone.
   *
   * The editor half is the softer of the two: `sendUserMessage` leaves the draft
   * alone either way. What it buys is that a wake does not start a turn while the
   * captain is mid-sentence, which would force their draft to arrive after it as a
   * steer.
   */
  function flush(ctx: ExtensionContext): void {
    if (held > consumed) {
      // No editor at all - print mode, rpc - is an empty editor: there is no draft
      // to arrive in the wrong order behind the wake.
      const draft = ctx.hasUI ? ctx.ui.getEditorText() : "";
      if (ctx.isIdle() && draft.trim() === "") {
        const mark = held;
        pi.sendUserMessage(WAKE);
        // Here, and never after the write below. This is what makes delivery
        // once-only, and the wake has now been delivered: left behind `held` by a
        // write that threw, the next tick would send the same wake again half a
        // second later, and again, for as long as the write kept failing - and
        // `sendUserMessage` always triggers a turn, so that is a paid turn every
        // half second with nothing but a stderr line five minutes later to say so.
        consumed = mark;
      }
    }
    // Recorded separately, because delivery and recording fail for different
    // reasons and only this half is retryable. It is never reached before a send
    // has succeeded: `consumed` moves nowhere else. Until it lands the watcher
    // reads the wake as untaken and says so, which is the right thing for it to
    // be saying while this cannot write.
    if (recorded < consumed) {
      writeCounter(paths!.consumed, consumed);
      recorded = consumed;
    }
  }

  function drain(ctx: ExtensionContext): void {
    if (!paths) return;
    const pending = counter(paths.pending);
    if (pending > held) held = pending;
    flush(ctx);
  }

  /**
   * A drain that cannot take the session down with it.
   *
   * This runs from a timer and from a filesystem event, where an exception has
   * nobody to report to and would surface as a crash in the captain's session. A
   * wake that fails to go out stays held and is retried on the next tick, and the
   * watcher's own held warning is what says so out loud.
   */
  function tick(ctx: ExtensionContext): void {
    try {
      drain(ctx);
    } catch {
      /* held, and tried again on the next tick */
    }
  }

  function disarm(): void {
    watcher?.close();
    watcher = null;
    if (timer) clearInterval(timer);
    timer = null;
  }

  function arm(ctx: ExtensionContext): void {
    disarm();
    try {
      // The directory, never the file. The watcher replaces `pending` with an
      // atomic rename, and a watch on the file itself follows the replaced inode:
      // it fires for the first rename and is dead for every one after it. This was
      // measured, and it is the trap anyone who "simplifies" this back to a file
      // watch will re-break.
      // Filtered to `pending`, and that filter is load-bearing rather than tidy.
      // Everything else in here is this extension's own writing - `consumer`, and
      // `consumed` with the staging file it renames from - and a watch fires for
      // those too. A `consumed` write whose rename fails stages the file, is handed
      // its own event, retries, and stages it again: on Linux inotify delivers every
      // one of those and the loop measured ~14,200 writes a second, which took the
      // event loop away from stdin entirely and hung the suite until CI's guard
      // killed it. macOS coalesces the same storm to ~17 a second, which is why it
      // ran green here for as long as it did. The interval below is what retries a
      // write that failed; this only ever needs to hear about the watcher.
      watcher = fs.watch(paths!.dir, (_event, name) => {
        // A platform that reports no filename still gets every event: losing the
        // fast path outright is worse than the storm, which cannot start there
        // because such a platform is not one where this was ever observed.
        if (name === null || name === undefined || name === PENDING) tick(ctx);
      });
      // Never a reason for pi to stay up, the same as the interval below.
      watcher.unref?.();
    } catch {
      // A filesystem or a platform that will not give a watch is not a stop. The
      // interval below delivers every wake anyway, half a second later.
      watcher = null;
    }
    timer = setInterval(() => tick(ctx), POLL_MS);
    // Never a reason for pi to stay up.
    timer.unref?.();
  }

  pi.on("session_start", (_event, ctx) => {
    const home = process.env.SIANA_HOME || ctx.cwd;
    const dir = join(home, WAKE_DIR);
    // The watcher refuses to start without a consumer record here, so this
    // directory has to exist before it does rather than on the first wake.
    fs.mkdirSync(dir, { recursive: true });
    paths = {
      dir,
      pending: join(dir, PENDING),
      consumed: join(dir, "consumed"),
      consumer: join(dir, "consumer"),
    };
    consumed = recorded = counter(paths.consumed);
    held = consumed;
    // Rewritten on every session_start, which covers a record left behind by a pi
    // that was killed: the watcher verifies the pid and the command, so a stale
    // one is refused rather than believed, and replacing it is the recovery.
    fs.writeFileSync(
      paths.consumer,
      JSON.stringify(
        { pid: process.pid, command: processCommand(process.pid), started: new Date().toISOString() },
        null,
        2,
      ) + "\n",
    );
    arm(ctx);
    // A wake raised while pi was down is on disk and is read here. Restart
    // recovery is a read and never a replay.
    tick(ctx);
  });

  pi.on("session_shutdown", (event, _ctx) => {
    disarm();
    // `quit` is the only reason with no `session_start` behind it in the same
    // process. Removing the record on reload, new, resume or fork would tell the
    // watcher there is no consumer during a gap that closes milliseconds later,
    // and the watcher's refusal is a startup refusal it would never see.
    if (event.reason === "quit" && paths) {
      try {
        fs.unlinkSync(paths.consumer);
      } catch {
        /* already gone, which is the state this wanted */
      }
    }
  });
}
