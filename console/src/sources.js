/**
 * Reading one source out of a snapshot, without ever answering a question the
 * console could not answer.
 *
 * `/api/state` relays six `siana-read` documents whole, each with its own exit code,
 * and those documents do not share a refusal shape: a store refuses with `error` and
 * `code` beside no records at all, and `fleet` refuses with `state: "unknown"` and no
 * `error` key anywhere. A reader that indexed one field would render a healthy fleet
 * at the moment herdr went away, which is the single worst thing this console can
 * do - the captain looks at a phone, sees nothing wrong, and goes to bed.
 *
 * So every panel goes through here, and there are exactly three answers:
 *
 *   ok           the document answered, render it
 *   degraded     it answered, and part of it is damaged; render it and say so
 *   unavailable  nothing here knows the answer; render the refusal and no content
 *
 * `unavailable` never carries records, deliberately. An empty list from a source that
 * refused is indistinguishable on screen from a fleet with nothing in it.
 */

export const OK = 'ok';
export const DEGRADED = 'degraded';
export const UNAVAILABLE = 'unavailable';

/** The six sources this console reads, and what each one is called on screen.
 *
 * Fixed here rather than taken from the snapshot: a source that stopped being
 * answered is a fact to report, and a screen built from whatever keys arrived would
 * simply stop having that panel. */
export const SOURCES = {
  tasks: 'the queue',
  projects: 'the registry',
  obligations: 'what is owed',
  decisions: 'the decision log',
  fleet: 'herdr',
  health: 'the helm',
};

function refusalOf(document) {
  // `help` is `siana-read`'s own advice to whoever is reading it, and it is the part
  // that says what to do about a refusal, so it is carried through rather than
  // collapsed into the message.
  if (!document || typeof document !== 'object') return {};
  const help = Array.isArray(document.help) ? document.help.filter(isText) : [];
  return {
    code: isText(document.code) ? document.code : null,
    detail: isText(document.error) ? document.error : null,
    help,
  };
}

function isText(value) {
  return typeof value === 'string' && value.length > 0;
}

function unavailable(name, reason, extra) {
  return {
    name, level: UNAVAILABLE, reason, records: [], total: 0, badLines: [],
    code: null, detail: null, help: [], observed: null, exit: null, ...extra,
  };
}

/** Whatever the console said about one source, before any panel reads it. */
function raw(state, name) {
  const sources = state && typeof state === 'object' ? state.sources : null;
  if (!sources || typeof sources !== 'object') return null;
  const found = sources[name];
  return found && typeof found === 'object' && !Array.isArray(found) ? found : null;
}

/** The common half of every read: did this source answer at all.
 *
 * Returns a refusal to hand straight back, or the source record to go on reading. */
function answered(state, name) {
  const record = raw(state, name);
  if (!record) {
    return unavailable(name, `the console said nothing about ${name}`, {
      help: ['this console reads six sources; one of them is missing from the '
             + 'snapshot, so the version serving it is not the one this app '
             + 'was built against'],
    });
  }
  if (record.error && typeof record.error === 'object') {
    return unavailable(name, isText(record.error.message)
      ? record.error.message : `siana-read ${name} could not be run`, {
      code: isText(record.error.code) ? record.error.code : null,
      observed: record.observed ?? null,
      exit: record.exit ?? null,
    });
  }
  if (!record.document || typeof record.document !== 'object'
      || Array.isArray(record.document)) {
    return unavailable(name, `siana-read ${name} answered with no document`, {
      observed: record.observed ?? null,
      exit: record.exit ?? null,
    });
  }
  return { record };
}

/** One record store - tasks, projects, obligations, decisions.
 *
 * A nonzero exit is unavailable and never a short list. `siana-read` refuses a store
 * it could not read rather than returning an empty one precisely so that a console
 * cannot make that mistake, and reading its records anyway would put the mistake
 * back. `bad_lines` is the other half: the store was readable, some of it was not,
 * and both facts have to reach the screen. */
export function readStore(state, name) {
  const first = answered(state, name);
  if (first.level) return first;
  const { record } = first;
  const doc = record.document;
  const refusal = refusalOf(doc);
  const observed = record.observed ?? null;
  if (record.exit !== 0) {
    return unavailable(name, refusal.detail
      || `siana-read ${name} exited ${record.exit}`, {
      ...refusal, observed, exit: record.exit,
    });
  }
  if (!Array.isArray(doc.records)) {
    return unavailable(name, `siana-read ${name} answered without a records list`, {
      observed, exit: record.exit,
      help: ['the shape of this source has changed, and reading it half-way '
             + 'would invent a fleet'],
    });
  }
  const badLines = Array.isArray(doc.bad_lines) ? doc.bad_lines : [];
  const records = doc.records.filter((r) => r && typeof r === 'object');
  return {
    name,
    level: badLines.length || records.length !== doc.records.length
      ? DEGRADED : OK,
    reason: badLines.length
      ? `${badLines.length} line${badLines.length === 1 ? '' : 's'} in this store `
        + 'could not be read'
      : (records.length !== doc.records.length
        ? 'this store answered with something that is not a record' : null),
    records,
    total: Number.isInteger(doc.total) ? doc.total : records.length,
    badLines,
    code: null, detail: null, help: [],
    revision: isText(doc.revision) ? doc.revision : null,
    observed, exit: record.exit,
  };
}

/** What herdr said, or that herdr said nothing.
 *
 * `state: "unknown"` is the answer this whole file exists for. It is not an empty
 * fleet and it is not a healthy one; it is herdr being unreachable, and the console
 * has to say exactly that over whatever it last knew. */
export function readFleet(state) {
  const first = answered(state, 'fleet');
  if (first.level) return { ...first, agents: [] };
  const { record } = first;
  const doc = record.document;
  const refusal = refusalOf(doc);
  const observed = record.observed ?? null;
  if (record.exit !== 0 || doc.state !== 'ok') {
    return {
      ...unavailable('fleet', refusal.detail
        || `herdr is ${isText(doc.state) ? doc.state : 'not answering'}`, {
        ...refusal, observed, exit: record.exit,
      }),
      agents: [],
    };
  }
  if (!Array.isArray(doc.agents)) {
    return {
      ...unavailable('fleet', 'herdr answered without a list of agents', {
        observed, exit: record.exit,
      }),
      agents: [],
    };
  }
  const agents = doc.agents.filter((a) => a && typeof a === 'object');
  return {
    name: 'fleet', level: agents.length === doc.agents.length ? OK : DEGRADED,
    reason: agents.length === doc.agents.length
      ? null : 'herdr listed something that is not an agent',
    agents, records: [], total: agents.length, badLines: [],
    code: null, detail: null, help: [], observed, exit: record.exit,
  };
}

/** The helm: is anyone at it, and what is the evidence.
 *
 * A nonzero exit here is degraded and not unavailable, because `siana-read health`
 * still answers with all three parts and marks the one it could not read. Losing the
 * other two would be throwing away the answer to make room for the failure. */
export function readHealth(state) {
  const first = answered(state, 'health');
  if (first.level) {
    return { ...first, session: null, wake: null, watch: null };
  }
  const { record } = first;
  const doc = record.document;
  const part = (value) => (value && typeof value === 'object'
    && !Array.isArray(value) ? value : null);
  const session = part(doc.session);
  const wake = part(doc.wake);
  const watch = part(doc.watch);
  const missing = [['session', session], ['wake', wake], ['watch', watch]]
    .filter(([, value]) => value === null).map(([key]) => key);
  const unread = [
    session && isText(session.error) ? 'the session record' : null,
    watch && isText(watch.error) ? 'the watcher record' : null,
    wake && Array.isArray(wake.errors) && wake.errors.length ? 'the wake directory'
      : null,
  ].filter(Boolean);
  return {
    name: 'health',
    level: missing.length || unread.length || record.exit !== 0 ? DEGRADED : OK,
    reason: missing.length
      ? `this answer is missing ${missing.join(', ')}`
      : (unread.length ? `${unread.join(' and ')} could not be read` : null),
    session, wake, watch,
    at: isText(doc.at) ? doc.at : null,
    home: isText(doc.home) ? doc.home : null,
    records: [], total: 0, badLines: [],
    code: null, detail: null, help: [],
    observed: record.observed ?? null, exit: record.exit,
  };
}

/** Source names the console answered about that this app does not know.
 *
 * A console newer than the app is not an error, and it is not nothing either: it is
 * fleet state on screen that nobody is rendering, and the captain should be told
 * rather than left to assume the six panels are all of it. */
export function unknownSources(state) {
  const sources = state && typeof state === 'object' ? state.sources : null;
  if (!sources || typeof sources !== 'object') return [];
  return Object.keys(sources).filter((name) => !(name in SOURCES)).sort();
}
