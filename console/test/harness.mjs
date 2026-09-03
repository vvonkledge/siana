/**
 * One page, loaded the way a phone loads it, with everything that leaves the device
 * replaced by something a test can drive.
 *
 * **It runs the built bundle, not the sources.** `dist/index.html` and the files it
 * names are the bytes the console serves and the bytes a captain's browser executes,
 * so those are what these tests execute too. A suite that imported `src/` would be
 * testing a build nobody ships, and would go on passing through a build step that had
 * broken.
 *
 * The window is jsdom, and only the transports are scripted: `fetch`, `EventSource`
 * and the clock. Nothing else is faked - the DOM, React, the router and every screen
 * are the real ones.
 */

import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { JSDOM, VirtualConsole } from 'jsdom';

import { NOW } from './fixtures.mjs';

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
export const DIST = join(ROOT, 'dist');
export const ORIGIN = 'http://127.0.0.1:8787';

/** The built files, or a refusal naming the build.
 *
 * A missing `dist/` is never a skip. These tests exist to check what is served, and a
 * suite that quietly passed with nothing built would report on an application that
 * does not exist. */
export function built() {
  let assets;
  try {
    assets = readdirSync(join(DIST, 'assets'));
  } catch {
    throw new Error(`${DIST} holds no build. Run \`just build\` in the distro, or `
      + '`npm run build` here.');
  }
  const js = assets.filter((name) => name.endsWith('.js'));
  const css = assets.filter((name) => name.endsWith('.css'));
  assert.equal(js.length, 1, `expected one built script, found ${js.join(', ')}`);
  assert.equal(css.length, 1, `expected one built stylesheet, found ${css.join(', ')}`);
  return {
    html: readFileSync(join(DIST, 'index.html'), 'utf8'),
    js: readFileSync(join(DIST, 'assets', js[0]), 'utf8'),
    css: readFileSync(join(DIST, 'assets', css[0]), 'utf8'),
    jsPath: `/assets/${js[0]}`,
    cssPath: `/assets/${css[0]}`,
    manifest: readFileSync(join(DIST, 'manifest.webmanifest'), 'utf8'),
    sw: readFileSync(join(DIST, 'sw.js'), 'utf8'),
  };
}

/** The stream, scripted.
 *
 * A real `EventSource` reconnects on its own schedule and reports nothing about it,
 * which is exactly the behaviour the app replaces with one it can show in the
 * connection bar. Driving it by hand is the only way to see that. */
class Stream {
  constructor(url, page) {
    this.url = url;
    this.readyState = 0;
    this.listeners = new Map();
    this.onopen = null;
    this.onerror = null;
    this.closed = false;
    page.streams.push(this);
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }

  removeEventListener(type, handler) {
    const found = this.listeners.get(type) || [];
    this.listeners.set(type, found.filter((h) => h !== handler));
  }

  close() {
    this.readyState = 2;
    this.closed = true;
  }

  /** The console's `: connected` frame and the open event a browser fires with it. */
  connect() {
    this.readyState = 1;
    if (this.onopen) this.onopen({ type: 'open' });
  }

  announce(revision) {
    for (const handler of this.listeners.get('state') || []) {
      handler({ type: 'state', data: JSON.stringify({ revision }) });
    }
  }

  /** A frame that is not the protocol. */
  garble(data) {
    for (const handler of this.listeners.get('state') || []) {
      handler({ type: 'state', data });
    }
  }

  drop() {
    this.readyState = 2;
    if (this.onerror) this.onerror({ type: 'error' });
  }
}

/** A page, with the transports in the test's hands. */
export function load({
  snapshot = null,
  hash = '',
  clock = NOW,
  stream = true,
  prepare = null,
} = {}) {
  const files = built();
  const page = {
    requests: [],
    streams: [],
    clock,
    /** What `/api/state` answers next. Replaced by a test between reads. */
    answer: null,
    inFlight: 0,
    maxInFlight: 0,
  };
  page.answer = defaultAnswer(snapshot);

  // jsdom has no layout, so `scrollTo` is a "not implemented" notice rather than a
  // failure. Everything else a page says is forwarded, because a React error logged
  // and swallowed is exactly the failure these tests are for.
  const speaker = new VirtualConsole();
  for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
    speaker.on(level, (...said) => console[level](...said));
  }
  speaker.on('jsdomError', (error) => {
    if (!/Not implemented/.test(error.message)) console.error(error);
  });
  const dom = new JSDOM(files.html, {
    url: `${ORIGIN}/${hash}`,
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    virtualConsole: speaker,
  });
  const { window } = dom;
  page.window = window;
  page.document = window.document;

  // The clock is the test's. Ages, staleness and the refetch floor are all read off
  // `Date.now`, and a suite that waited out real minutes for a staleness threshold
  // would be a suite nobody runs.
  const RealDate = window.Date;
  class FakeDate extends RealDate {
    constructor(...args) {
      super(...(args.length ? args : [page.clock]));
    }

    static now() {
      return page.clock;
    }
  }
  window.Date = FakeDate;

  window.fetch = async (input, options) => {
    const url = String(input);
    page.requests.push({ url, options });
    page.inFlight += 1;
    page.maxInFlight = Math.max(page.maxInFlight, page.inFlight);
    try {
      // A microtask hop, so a second request started while this one is out really is
      // concurrent rather than merely later.
      await Promise.resolve();
      return await page.answer(url, page);
    } finally {
      page.inFlight -= 1;
    }
  };

  if (stream) {
    window.EventSource = function EventSource(url) {
      return new Stream(url, page);
    };
  } else {
    delete window.EventSource;
  }

  // Timers, moved onto the test's clock.
  //
  // Every wait this app makes is a product decision - five seconds before refetching
  // on an announcement, ten before polling, a minute before a snapshot is called
  // stale - and a suite that waited them out in real time would take minutes and
  // still be timing-dependent. So a timer of `SLOW` or more goes into a queue this
  // drives, and anything shorter stays a real timer: React's own scheduler falls back
  // to `setTimeout(fn, 0)` when it has no `MessageChannel`, and a render parked
  // behind `page.tick` would deadlock every test here.
  const SLOW = 50;
  const realSetTimeout = window.setTimeout.bind(window);
  const realClearTimeout = window.clearTimeout.bind(window);
  const realSetInterval = window.setInterval.bind(window);
  const realClearInterval = window.clearInterval.bind(window);
  const pending = new Map();
  let nextTimer = 1;

  const arm = (fn, delay, args, every) => {
    // Negative, so `clearTimeout` can tell a handle of this queue's from one of
    // jsdom's without keeping a second table to look it up in.
    const id = -(nextTimer += 1);
    pending.set(id, { at: page.clock + delay, fn, args, every });
    return id;
  };
  window.setTimeout = (fn, delay = 0, ...args) => (delay >= SLOW
    ? arm(fn, delay, args, null) : realSetTimeout(fn, delay, ...args));
  window.setInterval = (fn, delay = 0, ...args) => (delay >= SLOW
    ? arm(fn, delay, args, delay) : realSetInterval(fn, delay, ...args));
  window.clearTimeout = (id) => (typeof id === 'number' && id < 0
    ? pending.delete(id) : realClearTimeout(id));
  window.clearInterval = (id) => (typeof id === 'number' && id < 0
    ? pending.delete(id) : realClearInterval(id));

  page.pending = pending;
  page.tick = async (ms) => {
    const target = page.clock + ms;
    for (;;) {
      let soonest = null;
      for (const [id, timer] of pending) {
        if (timer.at <= target && (soonest === null || timer.at < soonest[1].at)) {
          soonest = [id, timer];
        }
      }
      if (!soonest) break;
      const [id, timer] = soonest;
      // Never backwards. A test whose scripted answer takes time takes it off this
      // same clock, so a timer armed before that must not rewind it when it fires.
      page.clock = Math.max(page.clock, timer.at);
      if (timer.every === null) pending.delete(id);
      else timer.at = page.clock + timer.every;
      timer.fn(...timer.args);
      await settle(page, 2);
    }
    page.clock = target;
    await settle(page, 2);
    return page;
  };

  // Every page holds a one second interval for the ages on screen, so a test that
  // did not close its window would hold the whole runner open after it passed.
  page.close = () => window.close();
  // The last thing before the bundle runs, so a test can watch a global the app is
  // about to reach for.
  if (prepare) prepare(window, page);
  window.eval(files.js);
  page.files = files;
  return page;
}

function defaultAnswer(snapshot) {
  return async (url) => {
    if (!snapshot) return json({ error: 'no snapshot', code: 'NONE' }, 500);
    const asked = new URL(url, ORIGIN).searchParams.get('rev');
    if (asked && asked === snapshot.revision) return new Response(null, { status: 204 });
    return json(snapshot);
  };
}

export function json(body, status = 200, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', ...headers },
  });
}

/** Let the page settle: microtasks, then whatever timers are due. */
export async function settle(page, rounds = 6) {
  for (let i = 0; i < rounds; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  return page;
}

/** Wait for something to become true of the page, or fail saying what it says now. */
export async function until(page, predicate, what, timeout = 4000) {
  const deadline = Date.now() + timeout;
  for (;;) {
    if (predicate(page)) return page;
    if (Date.now() > deadline) {
      assert.fail(`${what}\n--- the page says ---\n${text(page).slice(0, 4000)}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

export function text(page) {
  return page.document.body.textContent.replace(/\s+/g, ' ').trim();
}

export function main(page) {
  return page.document.querySelector('main');
}

export function html(page) {
  return page.document.body.innerHTML;
}

export function bar(page) {
  return page.document.querySelector('[data-status]');
}

export function go(page, hash) {
  page.window.location.hash = hash;
  return settle(page);
}

/** Every alert on screen, which is where every degraded source ends up. */
export function alerts(page) {
  return [...page.document.querySelectorAll('[role="alert"]')]
    .map((node) => node.textContent.replace(/\s+/g, ' ').trim());
}

/** A page that has read one snapshot and is showing it, closed when the test ends.
 *
 * Closing matters: every page holds a one second interval for the ages on screen, and
 * a window left open holds the whole runner open behind a test that already passed. */
export async function opened(t, options = {}) {
  const page = load(options);
  if (t) t.after(() => page.close());
  await until(page, (p) => p.document.querySelector('main section, main [role=alert]'),
              'the first snapshot never rendered');
  return page;
}
