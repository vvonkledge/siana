/**
 * The link to the console: one snapshot, kept current, and an honest account of how
 * current it actually is.
 *
 * `/api/state` is complete on its own and `/api/stream` only says that a new revision
 * exists, so this is deliberately not a second source of fleet truth: the stream is a
 * hint to refetch and nothing here reconstructs state from events.
 *
 * Four rules shape it, and each one exists because of a way this goes wrong on a
 * phone:
 *
 *   - **One request in flight, ever.** The snapshot is the whole fleet, and against a
 *     live herdr that is hundreds of kilobytes. A second request started while the
 *     first is out is a phone spending its battery on a body it will throw away.
 *   - **Announcements are coalesced and bounded.** Herdr's presentation fields move
 *     constantly - a spinner in a terminal title is enough - so the console honestly
 *     announces a new revision every couple of seconds and would have the client
 *     refetch just as often. `MIN_REFETCH_MS` is the floor. It changes nothing about
 *     which revision is authoritative; it only decides how often this asks.
 *   - **An unchanged revision replaces nothing.** The snapshot object is kept by
 *     identity, so a `204`, or a `200` that came back with the revision already held,
 *     leaves every screen exactly as it was rather than rerendering it.
 *   - **Nothing is persisted here.** The service worker holds the one cached
 *     `/api/state` response, which is the whole of what offline needs. A second copy
 *     in this process would be a second answer to "what did we last know", free to
 *     disagree with the first.
 */

const STATE_URL = '/api/state';
const STREAM_URL = '/api/stream';

/** Past this, a snapshot is called stale on screen rather than shown as current. */
export const STALE_AFTER_MS = 60_000;

/** The floor between two refetches. See the header: this bounds how often a fleet
 * that is genuinely changing is asked for, and never what counts as a change. */
const MIN_REFETCH_MS = 5_000;

/** How often to ask when there is no stream to be told by. */
const POLL_MS = 10_000;

/** How long a quiet stream goes before the app reads anyway.
 *
 * Comfortably under `STALE_AFTER_MS` and not equal to it: the read has to have come
 * back before the screen would be called stale, not started at that moment.
 * `/api/state` runs six `siana-read` processes and is not instant, so a refresh armed
 * at the threshold would leave the loud banner up for the whole round trip, once a
 * minute, on a console that is connected and current - which is exactly how a captain
 * learns to look past it. */
const QUIET_MS = 30_000;

/** The ceiling a failing link backs off to. Bounded, because a captain who fixes
 * their network wants the screen back in under a minute. */
const BACKOFF_MAX_MS = 60_000;

/** How long to wait before opening the stream again, and how many goes at it before
 * this stops calling itself reconnecting and admits it is polling. */
const STREAM_RETRY_MS = 5_000;
const RECONNECT_ATTEMPTS = 3;

/** The header the service worker adds to a response it served from its cache. The
 * app cannot otherwise tell a cached snapshot from a fresh one, and showing a
 * snapshot from an hour ago as current is the failure this console exists to
 * prevent. */
const CACHE_HEADER = 'x-siana-cache';

// ------------------------------------------------------------------- the stores

function createStore(initial) {
  let value = initial;
  const listeners = new Set();
  return {
    get: () => value,
    /** Replaces the value only when it is a different one, so `useSyncExternalStore`
     * sees a stable reference and React does no work for a fleet that did not
     * move. */
    set(next) {
      if (next === value) return;
      value = next;
      for (const listener of [...listeners]) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** What the console last said, by identity. */
export const fleet = createStore({ snapshot: null, revision: null });

/** How that answer was come by: whether the link is up, when it last worked, and
 * whether what is on screen came off this device rather than off the console.
 *
 * `observed` is the console's own read stamp out of a cached body, and is set only
 * while one is on screen. It is the answer to "how old is this saved copy", which
 * `readAt` cannot give: opened with the console already gone there has been no
 * successful read this session, and the moment the service worker handed the body
 * back says nothing about when the fleet in it was true. */
export const link = createStore({
  status: 'starting',
  readAt: null,
  cached: false,
  observed: null,
  error: null,
});

// ------------------------------------------------------------------- the machine

let started = false;
let source = null;
let streamFailures = 0;
let inFlight = false;
let announced = null;
let pending = false;
let lastAsked = 0;
let refreshTimer = null;
let refreshAt = null;
let streamTimer = null;
let failures = 0;
let error = null;
let answeredOnce = false;

function now() {
  return Date.now();
}

/** The one status the bar shows, derived rather than assigned.
 *
 * Assigned from four places it would disagree with itself: a stream that reopened
 * while a fetch was failing would report `connected` over a screen nothing had
 * refreshed. So the machine keeps the facts and this reads them.
 */
function statusNow() {
  if (error !== null) return 'offline';
  if (source && source.readyState === 1) return 'connected';
  // A stream still opening for the first time is connecting, not reconnecting. The
  // difference matters on the bar: `reconnecting` says something was lost.
  if (!answeredOnce || (streamFailures === 0 && source && source.readyState === 0)) {
    return 'starting';
  }
  if (streamFailures <= RECONNECT_ATTEMPTS) return 'reconnecting';
  return 'polling';
}

function publish(extra) {
  const previous = link.get();
  const next = { ...previous, ...extra, status: statusNow(), error };
  if (next.status === previous.status && next.readAt === previous.readAt
      && next.cached === previous.cached && next.observed === previous.observed
      && next.error === previous.error) {
    return;
  }
  link.set(next);
}

function planRefresh(delay) {
  const at = now() + delay;
  // The soonest plan wins. Without this a poll scheduled a second ago would be
  // pushed back by every announcement that arrived after it.
  if (refreshAt !== null && refreshAt <= at) return;
  clearTimeout(refreshTimer);
  refreshAt = at;
  refreshTimer = setTimeout(() => {
    refreshAt = null;
    refresh();
  }, delay);
}

/** How long to wait before asking again when nothing is streaming. */
function pollDelay() {
  if (!failures) return POLL_MS;
  return Math.min(POLL_MS * 2 ** Math.min(failures, 6), BACKOFF_MAX_MS);
}

/** Ask again, once the last ask has finished and not before.
 *
 * The pending flag is the coalescing: any number of asks arriving during a read
 * produce exactly one read after it. */
async function refresh() {
  if (inFlight) {
    pending = true;
    return;
  }
  inFlight = true;
  lastAsked = now();
  const held = fleet.get().revision;
  try {
    // The held revision as a cache validator, so an unchanged fleet costs a `204`
    // and no body at all. `cache: 'no-store'` keeps the browser's own HTTP cache out
    // of it; the only cache in this design is the service worker's, and it says so
    // in a header.
    const url = held ? `${STATE_URL}?rev=${encodeURIComponent(held)}` : STATE_URL;
    const response = await fetch(url, {
      cache: 'no-store', headers: { accept: 'application/json' },
    });
    // Set here rather than at the end: the status derived under it is published by
    // every branch that follows, and one of them would otherwise report a link that
    // has answered as one that has not started. A read that threw never reaches this
    // and does not need it - `fail` below sets an error, and an error is `offline`
    // whatever else is true.
    answeredOnce = true;
    const cached = response.headers.get(CACHE_HEADER) === 'hit';
    if (response.status === 204) {
      // Nothing moved. The screen is already right, so nothing is replaced; only the
      // age of the last successful read moves.
      failures = 0;
      error = null;
      publish({ readAt: now(), cached: false, observed: null });
      return;
    }
    if (!response.ok) {
      fail(`the console answered ${response.status}`);
      return;
    }
    const document = await response.json();
    if (!document || typeof document !== 'object' || Array.isArray(document)
        || typeof document.revision !== 'string') {
      fail('the console answered with something that is not a snapshot');
      return;
    }
    failures = 0;
    error = null;
    if (document.revision !== fleet.get().snapshot?.revision) {
      fleet.set({ snapshot: document, revision: document.revision });
    }
    // A cached response is not a read. Leaving `readAt` where it was is what makes
    // the bar say how old this really is instead of restarting its clock every time
    // the service worker hands back the same hour-old body.
    //
    // The body's own `observed` travels with the flag, because on a cold open -
    // installed to a home screen, nothing running to serve it - `readAt` is null for
    // the whole session and the bar had no number at all to show. Anything but a
    // string stays null: an instant that will not parse has to read as unknown, and
    // a saved copy dated `0s ago` is worse than an undated one.
    publish(cached
      ? { cached: true,
          observed: typeof document.observed === 'string' ? document.observed : null }
      : { readAt: now(), cached: false, observed: null });
  } catch (e) {
    fail(e && e.message ? e.message : 'the console could not be reached');
  } finally {
    inFlight = false;
    settle();
  }
}

function fail(message) {
  failures += 1;
  error = message;
  publish();
}

/** What to do once a read has finished, whatever it answered. */
function settle() {
  const outstanding = pending
    || (announced !== null && announced !== fleet.get().revision);
  pending = false;
  announced = null;
  if (outstanding) {
    planRefresh(Math.max(0, MIN_REFETCH_MS - (now() - lastAsked)));
    return;
  }
  // A read even while the stream is up, before the screen would be old enough to be
  // called stale. A connection that wedged without ever firing an error - a proxy
  // holding the socket open, a laptop that slept - would otherwise leave the app
  // behind a `Not live` banner with nothing left that would ever ask again. An
  // unchanged fleet costs a `204` and no body, so the price of this is one empty
  // response every half minute per open page.
  const streaming = source && source.readyState === 1 && !failures;
  planRefresh(streaming ? QUIET_MS : pollDelay());
}

/** A revision the console says exists. Never state: the event carries a revision and
 * this fetches the document, so a client that missed events has missed nothing. */
function announce(data) {
  let revision = null;
  try {
    revision = JSON.parse(data).revision;
  } catch {
    // A frame this cannot read says nothing about the fleet, and the next read is
    // already scheduled. Refetching on it would let a malformed stream drive the
    // request rate.
    return;
  }
  if (typeof revision !== 'string' || revision === fleet.get().revision) return;
  announced = revision;
  if (inFlight) return;
  planRefresh(Math.max(0, MIN_REFETCH_MS - (now() - lastAsked)));
}

function closeStream() {
  if (!source) return;
  try {
    source.close();
  } catch {
    // Already closed, which is the state this was asking for.
  }
  source = null;
}

function openStream() {
  const Stream = globalThis.EventSource;
  // No `EventSource` at all is a browser this console still has to work on, and it
  // is also every test that never stubs one. Polling is the whole fallback: the
  // state endpoint is complete without the stream.
  if (typeof Stream !== 'function') {
    streamFailures = RECONNECT_ATTEMPTS + 1;
    publish();
    planRefresh(pollDelay());
    return;
  }
  if (source) return;
  try {
    source = new Stream(STREAM_URL);
  } catch {
    streamFailures += 1;
    publish();
    retryStream();
    return;
  }
  source.onopen = () => {
    streamFailures = 0;
    publish();
    // The revision may have moved while the stream was down, and the console only
    // announces changes it sees from now on.
    planRefresh(Math.max(0, MIN_REFETCH_MS - (now() - lastAsked)));
  };
  source.addEventListener('state', (event) => announce(event.data));
  source.onerror = () => {
    // `EventSource` reconnects on its own, but it does so silently and with no
    // bound this console chose. Closing it and driving the retry here is what makes
    // the bar able to say `reconnecting` and then `polling` rather than `connected`
    // over a socket that is not there.
    closeStream();
    streamFailures += 1;
    publish();
    retryStream();
    planRefresh(pollDelay());
  };
}

function retryStream() {
  clearTimeout(streamTimer);
  const delay = Math.min(STREAM_RETRY_MS * 2 ** Math.min(streamFailures - 1, 4),
                         BACKOFF_MAX_MS);
  streamTimer = setTimeout(openStream, delay);
}

/** Start reading. Idempotent: a second call attaches nothing. */
export function start() {
  if (started) return;
  started = true;
  if (typeof globalThis.addEventListener === 'function') {
    // The browser saying the network came back is the one signal worth acting on at
    // once: a captain who walked back into signal should not wait out a backoff.
    globalThis.addEventListener('online', () => {
      failures = 0;
      streamFailures = 0;
      error = null;
      publish();
      closeStream();
      openStream();
      planRefresh(0);
    });
    globalThis.addEventListener('offline', () => {
      error = 'this device says it is offline';
      publish();
    });
  }
  refresh();
  openStream();
}

