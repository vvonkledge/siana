/**
 * The shell: one persistent bar saying how current the screen is, one screen under
 * it, and one row of thumb-reachable links.
 *
 * Routes are fragments. That is not a style choice: it means this whole app is served
 * from one path, so the console's route table stays a handful of exact strings with
 * no catch-all under them, and a link here can never become a request.
 *
 * There is no button anywhere in this app, and there is deliberately nowhere that one
 * could go. This slice reads the fleet and cannot act on it, and a control that looked
 * like it might - even a disabled one - is the difference between a captain who knows
 * they have to reach the helm and one who thinks they already have.
 */

import { useEffect, useSyncExternalStore } from 'react';

import { fleet, link, start, STALE_AFTER_MS } from './link.js';
import { Decision, Decisions, Obligations, Project, Projects, Task } from './detail.jsx';
import { Overview } from './overview.jsx';
import { ageOf } from './time.js';
import { Age, Go, useTick } from './ui.jsx';

/** The current route, out of the fragment.
 *
 * Everything after the first `/` is a segment, decoded. An unknown route is its own
 * answer rather than a redirect: a link that goes somewhere the app does not have is
 * worth seeing.
 */
export function parseRoute(hash) {
  const raw = String(hash || '').replace(/^#/, '');
  const parts = raw.split('/').filter(Boolean).map((part) => {
    try {
      return decodeURIComponent(part);
    } catch {
      // A fragment that is not valid percent-encoding is still a fragment somebody
      // typed, and it names no route either way.
      return part;
    }
  });
  if (!parts.length) return { name: 'overview' };
  const [head, ...rest] = parts;
  if (head === 'projects' && !rest.length) return { name: 'projects' };
  if (head === 'project' && rest.length === 1) {
    return { name: 'project', handle: rest[0] };
  }
  if (head === 'task' && rest.length === 1) return { name: 'task', id: rest[0] };
  if (head === 'obligations' && !rest.length) return { name: 'obligations' };
  if (head === 'decisions' && !rest.length) return { name: 'decisions' };
  if (head === 'decision' && rest.length === 1) {
    return { name: 'decision', id: rest[0] };
  }
  return { name: 'unknown', path: raw };
}

function useRoute() {
  const hash = useSyncExternalStore(
    (listener) => {
      globalThis.addEventListener('hashchange', listener);
      return () => globalThis.removeEventListener('hashchange', listener);
    },
    () => globalThis.location.hash,
    () => '',
  );
  return parseRoute(hash);
}

const TITLES = {
  overview: 'Fleet', projects: 'Projects', project: 'Project', task: 'Task',
  obligations: 'Owed', decisions: 'Decisions', decision: 'Decision',
  unknown: 'Not a screen',
};

const STATUS = {
  starting: { say: 'connecting', dot: 'bg-slate-400' },
  connected: { say: 'connected', dot: 'bg-emerald-400' },
  reconnecting: { say: 'reconnecting', dot: 'bg-amber-400' },
  polling: { say: 'polling', dot: 'bg-amber-400' },
  offline: { say: 'offline', dot: 'bg-red-400' },
};

/** How old the saved copy on screen is, said.
 *
 * The snapshot's own observation instant and never anything about this session: the
 * service worker hands back the same body however long it has been sitting there, so
 * the moment it was retrieved, and the moment the page was opened, are both answers
 * to a different question. Opened with the console already gone there is no last
 * successful read to date it by, and this is the only number the app holds.
 *
 * An instant that will not parse says so rather than becoming a length of time.
 * `ageOf` answers a phrase and not a duration for that and for a stamp ahead of this
 * clock, and neither of them takes an " ago".
 */
function savedAge(observed, now) {
  const age = ageOf(observed, now);
  if (age.duration) return `, read ${age.said} ago`;
  if (age.known) return `, read ${age.said}`;
  return ', of unknown age';
}

/** The bar that is always there.
 *
 * It says two things and never one: how the link is, and how old what you are looking
 * at is. A screen that is up to date and a screen frozen an hour ago look identical
 * without the second, and that is the failure mode this console is most able to cause:
 * a captain who checks their phone, sees no blocked task, and goes to sleep.
 */
function Bar({ state }) {
  const now = useTick();
  const shown = STATUS[state.status] || STATUS.starting;
  const known = state.readAt !== null;
  const age = known ? now - state.readAt : null;
  // Never over a first read that is still in the air. The console writes the
  // stream's response head at once while `/api/state` is still running six
  // processes, so on a cold open the link is `connected` for the whole of the first
  // read and there is nothing old on screen yet to warn about - `Waiting` below says
  // it is reading. A loud banner that flashes on every open is a banner the captain
  // learns to look past, which is the one thing this one cannot afford. With nothing
  // ever read, only a link that has actually failed is worth saying out loud.
  const stale = state.cached || (known && age > STALE_AFTER_MS)
    || (!known && state.status === 'offline');
  return (
    <div data-status={state.status} data-stale={stale ? 'yes' : 'no'}
      className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/95
        backdrop-blur">
      <div className="mx-auto flex max-w-2xl items-center gap-2 px-4 py-2 text-xs">
        <span className={`h-2 w-2 shrink-0 rounded-full ${shown.dot}`} />
        <span className="font-medium text-slate-200">{shown.say}</span>
        <span className="text-slate-500">
          {known
            ? <>last read <Age stamp={new Date(state.readAt).toISOString()} /></>
            : 'never read from the console in this session'}
        </span>
      </div>
      {stale
        ? <p role="alert" className="bg-amber-500 px-4 py-2 text-center text-sm
          font-semibold text-amber-950">
          {state.cached
            ? `Saved copy from this device${savedAge(state.observed, now)}. The `
              + 'console could not be reached.'
            : (known
              ? <>Not live. This is what was read <Age stamp={
                new Date(state.readAt).toISOString()} suffix=" ago" />.</>
              : 'Not live. Nothing has been read from the console yet.')}
          <span className="mt-0.5 block text-xs font-normal">
            This console only reads. Nothing can be sent from here.
          </span>
        </p>
        : null}
      {state.error
        ? <p role="alert" className="bg-red-950 px-4 py-1.5 text-center text-xs
          text-red-100">{state.error}</p>
        : null}
    </div>
  );
}

function Nav({ route }) {
  const items = [
    ['/', 'Fleet', ['overview', 'task']],
    ['/obligations', 'Owed', ['obligations']],
    ['/decisions', 'Decisions', ['decisions', 'decision']],
    ['/projects', 'Projects', ['projects', 'project']],
  ];
  return (
    <nav className="sticky bottom-0 z-10 border-t border-slate-800
      bg-slate-950/95 backdrop-blur">
      <div className="mx-auto flex max-w-2xl">
        {items.map(([to, label, names]) => (
          <Go key={to} to={to}
            className={`flex-1 py-3 text-center text-xs font-medium ${
              names.includes(route.name)
                ? 'text-sky-300' : 'text-slate-400'}`}>
            {label}
          </Go>
        ))}
      </div>
    </nav>
  );
}

function Screen({ route, snapshot }) {
  switch (route.name) {
    case 'projects': return <Projects snapshot={snapshot} />;
    case 'project': return <Project snapshot={snapshot} handle={route.handle} />;
    case 'task': return <Task snapshot={snapshot} id={route.id} />;
    case 'obligations': return <Obligations snapshot={snapshot} />;
    case 'decisions': return <Decisions snapshot={snapshot} />;
    case 'decision': return <Decision snapshot={snapshot} id={route.id} />;
    case 'unknown':
      return (
        <p role="alert" className="rounded-2xl bg-amber-950/40 px-4 py-4 text-sm
          text-amber-100 ring-1 ring-amber-800">
          This console has no screen at{' '}
          <span className="font-mono">{route.path}</span>.
        </p>
      );
    default: return <Overview snapshot={snapshot} />;
  }
}

/** The screen before the first snapshot arrives.
 *
 * Not an empty fleet, and it says so. This is the one moment where showing nothing
 * would be honest and would still read as "all clear". */
function Waiting({ state }) {
  return (
    <p className="rounded-2xl bg-slate-900/60 px-4 py-6 text-center text-sm
      text-slate-300 ring-1 ring-slate-800">
      {state.status === 'offline'
        ? 'The console could not be reached, and this device has no saved copy of '
          + 'the fleet. Nothing here is known.'
        : 'Reading the fleet.'}
    </p>
  );
}

export function App() {
  const state = useSyncExternalStore(link.subscribe, link.get, link.get);
  const held = useSyncExternalStore(fleet.subscribe, fleet.get, fleet.get);
  const route = useRoute();
  useEffect(() => {
    start();
  }, []);
  useEffect(() => {
    // A drilldown opened from a long list should start at the top of the screen it
    // opened, the way a page navigation would.
    globalThis.scrollTo?.(0, 0);
  }, [route.name, route.id, route.handle]);
  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <Bar state={state} />
      <header className="mx-auto w-full max-w-2xl px-4 pt-4">
        <h1 className="text-xl font-semibold text-slate-50">
          {TITLES[route.name] || 'Fleet'}
        </h1>
      </header>
      <main className="mx-auto w-full max-w-2xl grow px-4 py-4">
        {held.snapshot
          ? <Screen route={route} snapshot={held.snapshot} />
          : <Waiting state={state} />}
      </main>
      <Nav route={route} />
    </div>
  );
}
