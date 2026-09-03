/**
 * The service worker, run as itself.
 *
 * The generated `dist/sw.js` is loaded into a context holding a scripted `caches` and
 * a scripted `fetch` and nothing else, and then driven through the three events a
 * browser sends it. That is the only way to see the behaviour that matters here, all
 * of which happens when the console is gone: what is precached, what an upgrade
 * deletes, and whether a snapshot handed back off this device is marked as one.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';
import vm from 'node:vm';

import { built, ORIGIN } from './harness.mjs';

/** A cache store with the same handful of methods the worker uses. */
class Store {
  constructor() {
    this.entries = new Map();
  }

  static key(request) {
    const url = typeof request === 'string' ? request : request.url;
    return new URL(url, ORIGIN).pathname;
  }

  async addAll(requests) {
    for (const request of requests) {
      const response = await this.fetcher(request);
      if (!response.ok) throw new TypeError('addAll got a bad response');
      this.entries.set(Store.key(request), response);
    }
  }

  async put(request, response) {
    this.entries.set(Store.key(request), response);
  }

  async match(request) {
    return this.entries.get(Store.key(request));
  }
}

function worker({ network = null } = {}) {
  const files = built();
  const caches = new Map();
  const said = { skipWaiting: 0, claim: 0 };
  const listeners = new Map();
  const served = [];

  const fetcher = async (request) => {
    const url = new URL(typeof request === 'string' ? request : request.url, ORIGIN);
    served.push(url.pathname);
    if (network) return network(url, request);
    return new Response('from the console', { status: 200 });
  };

  // A worker resolves a relative request against its own scope. Node's `Request`
  // insists on an absolute URL, so this is the scope the worker would have had.
  class ScopedRequest extends Request {
    constructor(input, init) {
      super(typeof input === 'string' ? new URL(input, ORIGIN).href : input, init);
    }
  }

  const context = {
    URL,
    Request: ScopedRequest,
    Response,
    Headers,
    console,
    fetch: fetcher,
    caches: {
      async open(name) {
        if (!caches.has(name)) {
          const store = new Store();
          store.fetcher = fetcher;
          caches.set(name, store);
        }
        return caches.get(name);
      },
      async keys() {
        return [...caches.keys()];
      },
      async delete(name) {
        return caches.delete(name);
      },
    },
    self: {
      location: { origin: ORIGIN },
      addEventListener(type, handler) {
        listeners.set(type, handler);
      },
      async skipWaiting() {
        said.skipWaiting += 1;
      },
      clients: {
        async claim() {
          said.claim += 1;
        },
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(files.sw, context);

  const shell = JSON.parse(files.sw.match(/const SHELL = (\[[^\]]*\]);/)[1]);
  const name = JSON.parse(files.sw.match(/const CACHE = ("[^"]*");/)[1]);

  return {
    caches, said, served, shell, name, files,
    async install() {
      const waits = [];
      listeners.get('install')({ waitUntil: (p) => waits.push(p) });
      await Promise.all(waits);
    },
    async activate() {
      const waits = [];
      listeners.get('activate')({ waitUntil: (p) => waits.push(p) });
      await Promise.all(waits);
    },
    /** One fetch event, and whether the worker answered it at all. */
    async request(path, init = {}) {
      const url = new URL(path, ORIGIN).href;
      let answer;
      let answered = false;
      listeners.get('fetch')({
        request: new Request(url, init),
        respondWith(promise) {
          answered = true;
          answer = promise;
        },
      });
      if (!answered) return { answered, response: null, failure: null };
      try {
        return { answered, response: await answer, failure: null };
      } catch (failure) {
        // A worker that cannot answer lets the failure through to the page, and the
        // page is what says "offline" over what it last knew. So a rejection here is
        // an outcome to assert on and not an error in the test.
        return { answered, response: null, failure };
      }
    },
  };
}

test('installing precaches the shell that was actually built', async (t) => {
  const sw = worker();
  await sw.install();
  const store = sw.caches.get(sw.name);
  assert.ok(store, 'the worker cached nothing');
  assert.deepEqual([...store.entries.keys()].sort(), [...sw.shell].sort());
  assert.ok(sw.shell.includes('/'), 'the application path is not in the shell');
  assert.ok(sw.shell.includes('/manifest.webmanifest'));
  assert.ok(sw.shell.some((path) => path.endsWith('.js')));
  assert.ok(sw.shell.some((path) => path.endsWith('.css')));
  assert.ok(!sw.shell.includes('/sw.js'),
            'a worker that cached itself is one an upgrade cannot replace');
  assert.equal(sw.said.skipWaiting, 1);
});

test('the shell is taken off the console rather than out of the browser cache',
  async (t) => {
    // `cache: 'reload'` is what makes an upgrade fetch the new index.html instead of
    // the one the browser is still holding, and it is the difference between an
    // upgrade and an upgrade that looks like it worked.
    assert.match(built().sw, /cache: 'reload'/);
  });

test('activating deletes every older cache and no other application', async (t) => {
  const sw = worker();
  await sw.install();
  sw.caches.set('siana-console-older', new Store());
  sw.caches.set('somebody-elses-cache', new Store());
  await sw.activate();
  assert.deepEqual([...sw.caches.keys()].sort(),
                   [sw.name, 'somebody-elses-cache'].sort());
  assert.equal(sw.said.claim, 1);
});

test('the fleet comes off the console first and is kept for when it does not',
  async (t) => {
    const sw = worker({
      network: async (url) => (url.pathname === '/api/state'
        ? new Response('{"revision":"rev-1"}', { status: 200 })
        : new Response('shell', { status: 200 })),
    });
    await sw.install();
    const { answered, response } = await sw.request('/api/state?rev=old');
    assert.ok(answered, 'the worker let the fleet request past it');
    assert.equal(await response.text(), '{"revision":"rev-1"}');
    assert.equal(response.headers.get('x-siana-cache'), null,
                 'a live answer must never be marked as a cached one');
    const held = await sw.caches.get(sw.name).match('/api/state');
    assert.ok(held, 'the snapshot was not kept, so there is nothing to open offline');
  });

test('an unchanged fleet does not overwrite what is kept', async (t) => {
  // A `204` carries no body, so a worker that cached every answer would replace the
  // snapshot it is holding with nothing, and the next time the console went away
  // there would be nothing to open.
  let unchanged = false;
  const sw = worker({
    network: async (url) => {
      if (url.pathname !== '/api/state') return new Response('shell', { status: 200 });
      return unchanged
        ? new Response(null, { status: 204 })
        : new Response('{"revision":"rev-1"}', { status: 200 });
    },
  });
  await sw.install();
  await sw.request('/api/state');
  unchanged = true;
  const { response } = await sw.request('/api/state?rev=rev-1');
  assert.equal(response.status, 204);
  const held = await sw.caches.get(sw.name).match('/api/state');
  assert.equal(await held.text(), '{"revision":"rev-1"}');
});

test('with the console gone the kept snapshot comes back, marked as kept',
  async (t) => {
    let reachable = true;
    const sw = worker({
      network: async (url) => {
        if (!reachable) throw new TypeError('Failed to fetch');
        return url.pathname === '/api/state'
          ? new Response('{"revision":"rev-1"}', { status: 200 })
          : new Response('shell', { status: 200 });
      },
    });
    await sw.install();
    await sw.request('/api/state');
    reachable = false;
    const { answered, response } = await sw.request('/api/state?rev=rev-1');
    assert.ok(answered);
    assert.equal(response.headers.get('x-siana-cache'), 'hit');
    assert.equal(await response.text(), '{"revision":"rev-1"}');
  });

test('with the console gone and nothing kept, the failure is passed through',
  async (t) => {
    const sw = worker({
      network: async (url) => {
        if (url.pathname === '/api/state') throw new TypeError('Failed to fetch');
        return new Response('shell', { status: 200 });
      },
    });
    await sw.install();
    const { answered, failure } = await sw.request('/api/state');
    assert.ok(answered);
    assert.match(String(failure), /Failed to fetch/);
  });

test('the shell opens with no console to ask', async (t) => {
  let reachable = true;
  const sw = worker({
    network: async () => {
      if (!reachable) throw new TypeError('Failed to fetch');
      return new Response('the app shell', { status: 200 });
    },
  });
  await sw.install();
  reachable = false;
  const { answered, response } = await sw.request('/');
  assert.ok(answered);
  assert.equal(await response.text(), 'the app shell');
});

test('it handles nothing it has no business handling', async (t) => {
  const sw = worker();
  await sw.install();
  for (const [what, path, init] of [
    ['a write', '/api/state', { method: 'POST' }],
    ['the stream', '/api/stream', {}],
    ['a path this console does not serve', '/etc/passwd', {}],
    ['another origin', 'https://evil.example.com/api/state', {}],
  ]) {
    const { answered } = await sw.request(path, init);
    assert.equal(answered, false, `the worker answered for ${what}`);
  }
});

test('the worker holds no queue, no retry and nothing that could send', async (t) => {
  // Read with the comments taken out, because the comments discuss exactly the things
  // being searched for. A grep over them is a check that can only be passed by never
  // writing down why.
  const source = built().sw
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').filter((line) => !line.trim().startsWith('//')).join('\n');
  for (const forbidden of ['sync', 'push', 'postMessage', 'POST', 'indexedDB',
                           'localStorage', 'Notification']) {
    assert.doesNotMatch(source, new RegExp(`\\b${forbidden}\\b`),
                        `the worker reaches for ${forbidden}`);
  }
});
