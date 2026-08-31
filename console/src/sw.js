/**
 * The offline shell, and the one copy of the fleet this device keeps.
 *
 * Two jobs, and no third. It precaches the application shell so the app opens with no
 * console to reach, and it keeps the last successful `/api/state` response so that
 * what opens has something true in it. Everything else is passed through untouched.
 *
 * **A cached snapshot is marked as one.** The response handed back offline carries a
 * header the app reads, and the app turns that into a banner across the top of the
 * screen. A console that showed an hour-old fleet as though it were current would be
 * worse than one that showed nothing: the captain would look at "nothing needs you"
 * and go to bed.
 *
 * **Nothing here can be written.** Only `GET` is handled at all, there is no request
 * queue, no background sync and no retry of anything the captain did, because this
 * slice has nothing the captain can do. An offline control that queued an action
 * would be a message that looks sent and never was.
 *
 * `SHELL` and `CACHE` are written at build time by `tools/plugins.mjs`, from the
 * bundle that was actually emitted. The asset names carry content hashes, so a new
 * build is a new cache name: installing it precaches the new shell, activating it
 * deletes every older one, and no page is ever left holding half of one build and
 * half of another.
 */

const SHELL = '__SIANA_SHELL__';
const CACHE = '__SIANA_CACHE__';
const FAMILY = 'siana-console-';

const STATE = '/api/state';
const STREAM = '/api/stream';
const CACHE_HEADER = 'x-siana-cache';

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // `reload` so an upgrade takes the new shell off the console rather than out of
    // the browser's own HTTP cache, which may still be holding the old one.
    await cache.addAll(SHELL.map((path) => new Request(path, { cache: 'reload' })));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter((name) => name.startsWith(FAMILY) && name !== CACHE)
      .map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (url.pathname === STREAM) return;
  if (url.pathname === STATE) {
    event.respondWith(snapshot(request));
    return;
  }
  if (SHELL.includes(url.pathname)) {
    event.respondWith(shell(url.pathname, request));
  }
});

/** The fleet: the console first, this device's copy only when the console is gone.
 *
 * Network first and never cache first. A stale fleet served in preference to a live
 * one is the whole failure this console exists to avoid, and the cache here is a
 * fallback and not an optimisation.
 */
async function snapshot(request) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response.status === 200) {
      // Stored under the bare path. The app asks with `?rev=` so that an unchanged
      // fleet costs a `204`, and a cache keyed on the query would hold one entry per
      // revision and answer none of them when the console is unreachable.
      await cache.put(STATE, response.clone());
    }
    return response;
  } catch (unreachable) {
    const held = await cache.match(STATE);
    // Nothing cached and no console is genuinely nothing known, and the app says so.
    // Answering with an empty document instead would be this worker inventing a
    // fleet.
    if (!held) throw unreachable;
    return marked(held);
  }
}

/** The same body, saying where it came from. */
async function marked(response) {
  const headers = new Headers(response.headers);
  headers.set(CACHE_HEADER, 'hit');
  return new Response(await response.blob(), {
    status: response.status, statusText: response.statusText, headers,
  });
}

async function shell(path, request) {
  const cache = await caches.open(CACHE);
  const held = await cache.match(path);
  return held || fetch(request);
}
