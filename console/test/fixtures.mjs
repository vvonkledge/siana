/**
 * Snapshots in the shape `siana-console` actually serves.
 *
 * Built here rather than captured, so a test can say exactly which source failed and
 * how. The shapes are the ones `bin/siana-console` composes and `bin/siana-read`
 * emits: every source carries its own document, its exit code, its stderr and the
 * time it was observed, and the refusals do not share a field. That last part is the
 * whole reason this file exists - a fixture that gave every failure the same `error`
 * key would let the app get herdr wrong and still pass.
 */

/** The clock every fixture is written against. */
export const NOW = Date.parse('2026-08-31T12:00:00Z');

export function at(msAgo) {
  return new Date(NOW - msAgo).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

const MINUTE = 60_000;
const HOUR = 3_600_000;

/** One store source that answered. */
export function store(name, records, { badLines = [], total = null } = {}) {
  return {
    source: name,
    command: ['siana-read', name],
    observed: at(2000),
    exit: 0,
    signal: null,
    stderr: null,
    error: null,
    document: {
      source: name,
      revision: `${name}-1`,
      filter: {},
      total: total === null ? records.length : total,
      matched: records.length,
      records,
      bad_lines: badLines,
    },
  };
}

/** A source `siana-read` refused: a document, a nonzero exit, and no records. */
export function refused(name, { error, code, help = [] }) {
  return {
    source: name,
    command: ['siana-read', name],
    observed: at(2000),
    exit: 1,
    signal: null,
    stderr: null,
    error: null,
    document: { source: name, error, code, help },
  };
}

/** A source the console could not run at all. */
export function unrunnable(name, { code = 'NO_SIANA_READ', message }) {
  return {
    source: name,
    command: ['siana-read', name],
    observed: at(2000),
    exit: null,
    signal: null,
    stderr: null,
    error: { code, message },
    document: null,
  };
}

export function fleetOk(agents) {
  return {
    source: 'fleet',
    command: ['siana-read', 'fleet'],
    observed: at(2000),
    exit: 0,
    signal: null,
    stderr: null,
    error: null,
    document: { source: 'fleet', at: at(2000), state: 'ok', agents },
  };
}

/** Herdr unreachable, in the exact shape `siana-read fleet` refuses in: `unknown`,
 * and no top-level `error` key for a console to index. */
export function fleetUnknown() {
  return {
    source: 'fleet',
    command: ['siana-read', 'fleet'],
    observed: at(2000),
    exit: 1,
    signal: null,
    stderr: null,
    error: null,
    document: {
      source: 'fleet',
      at: at(2000),
      state: 'unknown',
      agents: null,
      error: 'herdr never answered on ~/.config/herdr/herdr.sock',
      code: 'HERDR_UNREACHABLE',
      help: ['the fleet runs in herdr; start it, then ask again'],
    },
  };
}

export function health({ alive = false, exit = 0, ...rest } = {}) {
  return {
    source: 'health',
    command: ['siana-read', 'health'],
    observed: at(2000),
    exit,
    signal: null,
    stderr: null,
    error: null,
    document: {
      source: 'health',
      at: at(2000),
      home: '/Users/captain/.siana',
      session: {
        path: '/Users/captain/.siana/session',
        present: alive,
        alive,
        pid: alive ? 4242 : null,
        pane: alive ? 'w3S:p1' : null,
        harness: alive ? 'pi' : null,
        command: alive ? 'pi' : null,
        why: alive ? 'pid 4242 is pi' : 'no SIANA session is recorded',
        error: null,
      },
      wake: { dir: '/Users/captain/.siana/wake', pending: 0, consumed: 3,
              consumer: null, errors: [] },
      watch: {
        command: ['siana-watch', '--status'],
        exit: 0,
        stdout: '  ok      no watcher (the fleet does not advance unattended)\n',
        stderr: '',
        error: null,
      },
      ...rest,
    },
  };
}

export function agent({ pane = 'w3S:p2', kind = 'claude', status = 'working' } = {}) {
  return {
    terminal_id: `term_${pane.replace(':', '_')}`,
    agent: kind,
    agent_status: status,
    terminal_title: `${kind} | siana`,
    workspace_id: pane.split(':')[0],
    tab_id: `${pane.split(':')[0]}:t1`,
    pane_id: pane,
    focused: false,
    cwd: '/Users/captain/work',
  };
}

export function task(id, fields = {}) {
  return {
    id,
    title: `do ${id}`,
    status: 'todo',
    verify: 'just test',
    verify_kind: 'cmd',
    deps: [],
    context: [],
    project: 'siana',
    cwd: null,
    base: null,
    owner: null,
    reason: null,
    updated: at(10 * MINUTE),
    ...fields,
  };
}

export function project(handle, fields = {}) {
  return {
    handle,
    path: `/Users/captain/work/${handle}`,
    ship: 'just test',
    pipeline: false,
    qa: null,
    target: null,
    orders: null,
    worktree: true,
    automerge: null,
    ...fields,
  };
}

export function obligation(id, fields = {}) {
  return {
    id,
    kind: 'promise',
    body: `remember ${id}`,
    status: 'open',
    task: null,
    opened: at(HOUR),
    closed: null,
    answer: null,
    ...fields,
  };
}

export function decision(id, fields = {}) {
  return {
    id,
    at: at(2 * HOUR),
    class: 'publish',
    action: `siana-publish ${id}`,
    verdict: 'proposed',
    reason: 'no grant was in force',
    task: null,
    project: 'siana',
    grant: null,
    policy: null,
    evidence: ['task:x done'],
    alternatives: ['waiting until morning'],
    principles: ['Never publish what a second pair of eyes has not seen.'],
    confidence: 'high',
    reversibility: 'R2',
    ...fields,
  };
}

/** A whole `/api/state` document. Every source defaults to a healthy empty one, so a
 * test names only the source it is about. */
export function snapshot({
  revision = 'rev-1',
  tasks = [],
  projects = [],
  obligations = [],
  decisions = [],
  agents = [],
  sources = {},
} = {}) {
  return {
    console: 'siana-console',
    home: '/Users/captain/.siana',
    observed: at(2000),
    revision,
    sources: {
      tasks: store('tasks', tasks),
      projects: store('projects', projects),
      obligations: store('obligations', obligations),
      decisions: store('decisions', decisions),
      fleet: fleetOk(agents),
      health: health(),
      ...sources,
    },
  };
}
