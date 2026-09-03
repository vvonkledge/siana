/**
 * The few derivations a screen needs that are not in any one record.
 *
 * Kept apart from the screens because each of them can be wrong in a way a captain
 * would act on: a task called ready that is not, a minion called idle that herdr
 * never mentioned.
 */

/** Every task by id, for resolving a dependency without scanning the store per
 * dependency. */
export function byId(records) {
  const index = new Map();
  for (const record of records) {
    if (record && typeof record.id === 'string') index.set(record.id, record);
  }
  return index;
}

/** Whether a `todo` task's dependencies are met, and what it is waiting on.
 *
 * A dependency naming a task the queue does not hold is never treated as met. It is
 * the queue disagreeing with itself, and calling that ready would hand a minion work
 * whose groundwork nobody can show was done. */
export function readiness(task, index) {
  const deps = Array.isArray(task.deps) ? task.deps : [];
  const waiting = [];
  for (const id of deps) {
    const dep = typeof id === 'string' ? index.get(id) : null;
    if (!dep) {
      waiting.push({ id: typeof id === 'string' ? id : JSON.stringify(id),
                     status: null });
    } else if (dep.status !== 'done') {
      waiting.push({ id, status: dep.status ?? null });
    }
  }
  return { ready: waiting.length === 0, waiting };
}

/** The pane an owner names, in the `<kind>@<pane>` shape `siana-dispatch` writes.
 *
 * An owner with no pane is reported rather than ignored: it is a task claimed outside
 * dispatch, and nothing can find that minion. */
export function ownerPane(owner) {
  if (typeof owner !== 'string' || !owner) return { kind: null, pane: null };
  const at = owner.indexOf('@');
  if (at < 0) return { kind: owner, pane: null };
  return { kind: owner.slice(0, at), pane: owner.slice(at + 1) || null };
}

/** The herdr agent holding a pane, or null when herdr never mentioned it.
 *
 * Null has two meanings and the caller has to keep them apart: herdr answered and
 * that pane is empty, or herdr did not answer at all. That is why this takes the
 * agents and the caller checks the source, rather than this returning "unknown". */
export function agentInPane(pane, agents) {
  if (!pane) return null;
  return agents.find((agent) => agent && (agent.pane_id === pane
    || agent.tab_id === pane || agent.terminal_id === pane)) || null;
}

/** Oldest first, on the stamp that field names, with unstamped records kept.
 *
 * An unstamped record sorts last rather than being dropped: a record whose clock was
 * hand-edited is still an obligation somebody is owed. */
export function oldestFirst(records, field) {
  return [...records].sort((a, b) => {
    const left = Date.parse(a?.[field]);
    const right = Date.parse(b?.[field]);
    const leftOk = Number.isFinite(left);
    const rightOk = Number.isFinite(right);
    if (leftOk && rightOk) return left - right;
    if (leftOk) return -1;
    if (rightOk) return 1;
    return 0;
  });
}

export function newestFirst(records, field) {
  return oldestFirst(records, field).reverse();
}
