/**
 * The pieces every screen is built from.
 *
 * Two of them carry a rule rather than a style. `Value` is the only thing that puts a
 * fleet string on screen, so there is one place to check that nothing from a store
 * can become markup. `Trouble` is the only thing that renders a source that did not
 * answer, so there is one place to check that a refusal never renders as an empty
 * list.
 */

import { useSyncExternalStore } from 'react';

import { ageOf } from './time.js';
import { DEGRADED, UNAVAILABLE } from './sources.js';

/** One clock for the whole app.
 *
 * Every age on screen has to go stale visibly rather than freezing at whatever it
 * said when the fleet last moved. One shared tick rather than a timer per component,
 * and read only by `Age`, so a second passing rerenders the handful of spans that
 * show an age and nothing else on the screen.
 */
const clock = (() => {
  let now = Date.now();
  let timer = null;
  const listeners = new Set();
  return {
    get: () => now,
    subscribe(listener) {
      listeners.add(listener);
      if (timer === null) {
        timer = setInterval(() => {
          now = Date.now();
          for (const l of [...listeners]) l();
        }, 1000);
      }
      return () => {
        listeners.delete(listener);
        if (!listeners.size) {
          clearInterval(timer);
          timer = null;
        }
      };
    },
  };
})();

export function useTick() {
  return useSyncExternalStore(clock.subscribe, clock.get, clock.get);
}

/** Any value out of a store, as text and never as anything else.
 *
 * Every string here was written by an agent, a captain, or herdr, and none of those
 * is trusted input. React escapes what it renders, and this file is the reason there
 * is nowhere in this app that opts out of that: no `dangerouslySetInnerHTML`, no
 * `innerHTML`, and no value ever reaching an `href` or a `src`.
 *
 * A value that is not a string is shown as JSON rather than as `[object Object]`,
 * because a contract that grew a field this app does not know should still show the
 * captain what is in it.
 */
export function Value({ value, missing = 'not set' }) {
  if (value === null || value === undefined || value === '') {
    return <span className="text-slate-500 italic">{missing}</span>;
  }
  if (typeof value === 'string') return <>{value}</>;
  if (typeof value === 'number' || typeof value === 'boolean') {
    return <>{String(value)}</>;
  }
  return <>{JSON.stringify(value)}</>;
}

/** How old a stamp is, ticking. */
export function Age({ stamp, suffix = ' ago' }) {
  const age = ageOf(stamp, useTick());
  if (!age.known) {
    return <span className="text-amber-400">age unknown</span>;
  }
  return <span>{age.said}{suffix}</span>;
}

export function Chip({ tone = 'plain', children }) {
  const tones = {
    plain: 'bg-slate-800 text-slate-300 ring-slate-700',
    live: 'bg-sky-950 text-sky-200 ring-sky-800',
    warn: 'bg-amber-950 text-amber-200 ring-amber-800',
    stop: 'bg-red-950 text-red-200 ring-red-800',
    done: 'bg-emerald-950 text-emerald-200 ring-emerald-800',
  };
  return (
    <span className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5
      text-xs font-medium ring-1 ring-inset ${tones[tone] || tones.plain}`}>
      {children}
    </span>
  );
}

const STATUS_TONE = {
  todo: 'plain', doing: 'live', blocked: 'stop', done: 'done',
};

export function StatusChip({ status }) {
  return <Chip tone={STATUS_TONE[status] || 'warn'}>
    <Value value={status} missing="no status" />
  </Chip>;
}

export function Panel({ title, note, children, tone = 'plain' }) {
  const rings = {
    plain: 'ring-slate-800',
    attention: 'ring-amber-700/60',
  };
  return (
    <section className={`rounded-2xl bg-slate-900/60 ring-1 ${rings[tone]}
      overflow-hidden`}>
      <header className="flex items-baseline justify-between gap-3 px-4 pt-4 pb-2">
        <h2 className="text-sm font-semibold tracking-wide text-slate-200 uppercase">
          {title}
        </h2>
        {note ? <span className="text-xs text-slate-400">{note}</span> : null}
      </header>
      <div className="px-4 pb-4">{children}</div>
    </section>
  );
}

/** An empty list, said out loud.
 *
 * A panel that renders nothing is a panel the captain reads as "fine", and the whole
 * point of `Needs you` is that it is only fine when it says so. */
export function Empty({ children }) {
  return <p className="rounded-xl bg-slate-950/60 px-3 py-3 text-sm text-slate-400">
    {children}
  </p>;
}

/** A source that could not be read, or could only be half read.
 *
 * Never collapsed into "unavailable" alone: `siana-read` says which source, what
 * went wrong, and what to do about it, and every one of those is what turns a red box
 * into something the captain can act on.
 */
export function Trouble({ source, what }) {
  if (!source || (source.level !== UNAVAILABLE && source.level !== DEGRADED)) {
    return null;
  }
  const down = source.level === UNAVAILABLE;
  return (
    <div role="alert" data-degraded={source.level} data-source={source.name}
      className={`mb-3 rounded-xl px-3 py-3 text-sm ring-1 ring-inset ${down
        ? 'bg-red-950/50 text-red-100 ring-red-800'
        : 'bg-amber-950/40 text-amber-100 ring-amber-800'}`}>
      <p className="font-semibold">
        {down ? `${what} could not be read` : `${what} is damaged`}
      </p>
      {source.reason ? <p className="mt-1"><Value value={source.reason} /></p> : null}
      {source.code
        ? <p className="mt-1 font-mono text-xs opacity-80">
          <Value value={source.code} />
        </p>
        : null}
      {source.help && source.help.length
        ? <ul className="mt-2 list-disc space-y-1 pl-4 text-xs opacity-90">
          {source.help.map((line, i) => <li key={i}><Value value={line} /></li>)}
        </ul>
        : null}
      {source.badLines && source.badLines.length
        ? <ul className="mt-2 space-y-1 font-mono text-xs opacity-90">
          {source.badLines.slice(0, 10).map((bad, i) => (
            <li key={i}>
              <Value value={typeof bad === 'object' && bad
                ? [bad.line ?? bad.offset ?? '?', bad.error ?? bad.reason ?? '']
                  .filter((p) => p !== '').join(': ')
                : bad} />
            </li>
          ))}
        </ul>
        : null}
      {down
        ? <p className="mt-2 text-xs opacity-80">
          Nothing is being shown here. This is not an empty fleet.
        </p>
        : null}
    </div>
  );
}

/** One labelled fact. */
export function Field({ label, value, mono = false, children }) {
  return (
    <div className="border-b border-slate-800/70 py-2 last:border-0">
      <dt className="text-xs tracking-wide text-slate-400 uppercase">{label}</dt>
      <dd className={`mt-0.5 text-sm break-words text-slate-100
        ${mono ? 'font-mono text-xs' : ''}`}>
        {children ?? <Value value={value} />}
      </dd>
    </div>
  );
}

export function Fields({ children }) {
  return <dl className="mt-1">{children}</dl>;
}

/** A link into this app.
 *
 * Every route is a fragment, so there is exactly one served application path and no
 * request a link here can make. `to` is built from an id out of a store, encoded, so
 * a record whose id somehow carried a `#` cannot rewrite the route.
 */
export function Go({ to, children, className = '' }) {
  return <a href={`#${to}`} className={className}>{children}</a>;
}

/** A tappable row, sized for a thumb. */
export function Row({ to, children }) {
  return (
    <Go to={to} className="block rounded-xl bg-slate-950/60 px-3 py-3
      ring-1 ring-slate-800 active:bg-slate-800">
      {children}
    </Go>
  );
}

export function List({ children }) {
  return <div className="space-y-2">{children}</div>;
}
