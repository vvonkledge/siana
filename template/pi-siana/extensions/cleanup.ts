/**
 * Put the fleet cleanup loop in front of SIANA as two tools, and nothing else.
 *
 * The whole state machine lives in `siana-clean`: the lock, the run record, the
 * durable question, the runbook, the guard on the child's PATH, and the process
 * reaping. This is a shim over that command and is deliberately thin, because the
 * rule this distro is built on is that logic which can be exact belongs in a script
 * and never in an agent's harness. A second copy of the protocol here would be a
 * second set of rules, and the one in TypeScript would be the one nothing tests.
 *
 * So what this file adds is exactly one thing: it makes those operations
 * reachable by SIANA as tool calls rather than as shell it has to remember, and it
 * keeps the cleaner's output out of SIANA's context by returning only what the
 * command printed - which is a few lines, because the command is written to print a
 * few lines.
 *
 * **This factory starts nothing.** No process, no watcher, no timer, no model.
 * Loading the extension registers two tools and returns. A cleanup run begins when
 * something calls `siana_cleanup` with `action: "start"`, and never before: pi runs
 * extension factories in invocations that never open a session at all, and a factory
 * that started a cleaner would run one on `pi --list-models`.
 *
 * **Nothing here waits on SIANA.** `start` and `resume` run a child to completion and
 * return; a child that has a question writes it down and ends, and the command exits
 * 3 with the question in its output. There is no callback into this process and no
 * state held between calls, so the deadlock a nested agent invites - a parent
 * blocked on a child that is blocked on the parent - has nowhere to happen.
 */

import { spawn } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { StringEnum } from "@earendil-works/pi-ai";

/** What one call may return into SIANA's context. `siana-clean` prints a report
 *  bounded on its own side; this is the backstop for a command that has gone wrong,
 *  and it is small on purpose, because the point of delegating cleanup is that its
 *  output does not land here. */
const OUTPUT_CAP = 16 * 1024;

/** A round can legitimately take half an hour, which is `siana-clean`'s own bound.
 *  This is that plus a minute: the command kills its child and reports, so this
 *  firing at all means the command itself is wedged, and then killing it is right. */
const CALL_MS = 31 * 60 * 1000;

interface Ran {
	code: number;
	text: string;
	truncated: boolean;
}

/**
 * Run `siana-clean` and return what it said.
 *
 * Cancellation travels in two hops, and both are needed. `detached` puts
 * `siana-clean` in its own process group so the kill below reaches it whatever group
 * this host is in; `siana-clean` then handles that signal and kills the cleaner,
 * which is in a session of its own and receives nothing sent here. Without the
 * second hop a cancel killed the command and left the cleaner running as an orphan,
 * still holding its grant with nothing watching it.
 *
 * `signal` is pi's abort, which fires on Ctrl+C. A run cancelled that way records
 * the round as a failed one and releases its lock, because `siana-clean` gets to
 * run its own cleanup rather than dying where it stood.
 */
function sianaClean(args: string[], signal?: AbortSignal): Promise<Ran> {
	return new Promise((resolve) => {
		let text = "";
		let truncated = false;
		const proc = spawn("siana-clean", args, {
			shell: false,
			detached: true,
			stdio: ["ignore", "pipe", "pipe"],
		});
		const take = (chunk: Buffer) => {
			if (text.length >= OUTPUT_CAP) {
				truncated = true;
				return;
			}
			text += chunk.toString();
		};
		proc.stdout.on("data", take);
		proc.stderr.on("data", take);

		let timer: ReturnType<typeof setTimeout> | null = null;
		const kill = (why: string) => {
			text += `\n${why}\n`;
			try {
				process.kill(-proc.pid!, "SIGTERM");
			} catch {
				// Already gone. Nothing to do, and nothing to report: the close
				// handler below is what answers this promise either way.
			}
			setTimeout(() => {
				try {
					process.kill(-proc.pid!, "SIGKILL");
				} catch {
					// Same.
				}
			}, 5000);
		};
		timer = setTimeout(() => kill(`siana-clean did not return in ${CALL_MS}ms`), CALL_MS);
		const onAbort = () => kill("cancelled");
		signal?.addEventListener("abort", onAbort, { once: true });

		proc.on("error", (err) => {
			if (timer) clearTimeout(timer);
			signal?.removeEventListener("abort", onAbort);
			resolve({
				code: 1,
				text: `cannot run siana-clean: ${err.message}\n  it is installed by \`just init\` in the SIANA distro`,
				truncated: false,
			});
		});
		proc.on("close", (code) => {
			if (timer) clearTimeout(timer);
			signal?.removeEventListener("abort", onAbort);
			resolve({ code: code ?? 1, text: text.slice(0, OUTPUT_CAP), truncated });
		});
	});
}

function result(ran: Ran) {
	const note = ran.truncated ? "\n\n(output truncated)" : "";
	return {
		content: [{ type: "text" as const, text: (ran.text.trim() || "(no output)") + note }],
		details: { exit: ran.code, pending: ran.code === 3 },
	};
}

export default function (pi: ExtensionAPI): void {
	pi.registerTool({
		name: "siana_cleanup",
		label: "Fleet cleanup",
		description:
			"Drive a fleet cleanup run in its own context. `start` begins one under a named grant; " +
			"`status` says what it is doing; `answer` records your answer to the question it stopped " +
			"on; `resume` carries it on from there. A run that asks a question exits with the question " +
			"in its output and the cleaner already gone, so answering and resuming are two separate " +
			"calls and neither waits on the other. The cleaner reports to you and never to the captain.",
		promptSnippet:
			"Delegate fleet cleanup to a dedicated agent, and answer only the questions it stops on",
		promptGuidelines: [
			"Use siana_cleanup rather than retiring worktrees one at a time in this session.",
			"When siana_cleanup reports a question of kind `captain`, record it with `siana-owe decision` " +
				"and wait for the captain; answering it yourself is not something siana_cleanup can authorise.",
		],
		parameters: Type.Object({
			action: StringEnum(["start", "status", "answer", "resume", "abort"] as const),
			run: Type.Optional(
				Type.String({ description: "the run id, for status, answer, resume and abort" }),
			),
			project: Type.Optional(
				Type.String({ description: "one project handle; omitted means every project" }),
			),
			grants: Type.Optional(
				Type.Array(
					StringEnum(["inventory", "retire", "reap-report", "close-workspace"] as const),
					{
						description:
							"what this run may do. `inventory` is always in force. `close-workspace` " +
							"adds `siana-close-workspace <task-id>`, which closes a finished task's own " +
							"herdr workspace only after that task's worktree was retired",
					},
				),
			),
			text: Type.Optional(Type.String({ description: "the answer, for action `answer`" })),
			decision: Type.Optional(
				Type.String({
					description:
						"the obligation id the captain's answer was recorded under. Required to answer a question of kind `captain`",
				}),
			),
			reason: Type.Optional(Type.String({ description: "why, for action `abort`" })),
		}),
		async execute(_id, params, signal) {
			const p = params as {
				action: string;
				run?: string;
				project?: string;
				grants?: string[];
				text?: string;
				decision?: string;
				reason?: string;
			};
			// Argument assembly and nothing else. Every refusal - an unknown grant, a
			// run that is not waiting on a question, a captain question answered
			// without a decision - is `siana-clean`'s, so that there is one place the
			// rules are written and one place they are tested.
			const args: string[] = [p.action];
			if (p.action === "start") {
				if (p.project) args.push("--project", p.project);
				for (const grant of p.grants ?? []) args.push("--grant", grant);
			} else {
				if (!p.run && p.action !== "status") {
					return result({
						code: 1,
						text: `error: ${p.action} needs a run id\n  siana_cleanup with action "status" lists the runs there are`,
						truncated: false,
					});
				}
				if (p.run) args.push(p.run);
				if (p.action === "answer") {
					if (p.text) args.push("--text", p.text);
					if (p.decision) args.push("--captain-decided", p.decision);
				}
				if (p.action === "abort") args.push("--reason", p.reason ?? "no reason given");
			}
			return result(await sianaClean(args, signal));
		},
	});

	pi.registerTool({
		name: "siana_runbook",
		label: "Cleanup runbook",
		description:
			"Read the fleet's cleanup runbook: every gotcha a cleaner has stopped on and the answer it " +
			"was given. Read it before answering a cleanup question, so that the same one is not " +
			"answered twice differently.",
		promptSnippet: "Read the durable answers earlier cleanup runs were given",
		parameters: Type.Object({}),
		async execute(_id, _params, signal) {
			return result(await sianaClean(["runbook"], signal));
		},
	});
}
