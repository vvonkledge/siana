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
  // The high-water mark this has delivered, and the one it has seen raised. Held
  // in memory as well as on disk so a wake that cannot go out yet is not re-read
  // from a file the watcher may have moved on from.
  let consumed = 0;
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
   * The editor check is politeness rather than safety: `sendUserMessage` leaves the
   * draft alone either way. What it buys is that a wake does not start a turn while
   * the captain is mid-sentence, which would force their draft to arrive after it
   * as a steer.
   */
  function flush(ctx: ExtensionContext): void {
    if (held <= consumed) return;
    // No editor at all - print mode, rpc - is an empty editor: there is no draft
    // to arrive in the wrong order behind the wake.
    const draft = ctx.hasUI ? ctx.ui.getEditorText() : "";
    if (draft.trim() !== "") return;
    const mark = held;
    if (ctx.isIdle()) pi.sendUserMessage(WAKE);
    // Mid-turn. `followUp` waits for the agent to finish its tool calls rather
    // than steering the turn already in flight, which is the same hazard the
    // watcher used to guard against by refusing to poke a working agent.
    else pi.sendUserMessage(WAKE, { deliverAs: "followUp" });
    // Only after the send. `consumed` written first would swallow the wake for
    // good if the send threw, and the watcher would have no way to know.
    writeCounter(paths!.consumed, mark);
    consumed = mark;
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
      watcher = fs.watch(paths!.dir, () => tick(ctx));
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
      pending: join(dir, "pending"),
      consumed: join(dir, "consumed"),
      consumer: join(dir, "consumer"),
    };
    consumed = counter(paths.consumed);
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
