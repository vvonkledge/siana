/**
 * What the app does with a snapshot that came off this device rather than off the
 * console, and what it never grows a control for.
 *
 * The service worker's own behaviour is in `serviceworker.test.mjs`. This is the other
 * half: the page has to say, loudly, that what is on screen is a saved copy - and it
 * has to offer nothing that could look like a way to answer any of it.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { bar, go, json, load, opened, settle, text, until } from './harness.mjs';
import { at, obligation, snapshot, task } from './fixtures.mjs';

/** What the service worker hands back when the console is unreachable. */
function cached(document) {
  return json(document, 200, { 'x-siana-cache': 'hit' });
}

const DAY = 86_400_000;

/** A saved copy of the fleet, observed however long ago.
 *
 * The whole document is aged and not only its top line: a body that came out of the
 * cache was written by one read, so a snapshot claiming to be three days old over
 * sources read two seconds ago is a shape the console never serves.
 */
function saved(msAgo, fields = {}) {
  const document = snapshot(fields);
  document.observed = at(msAgo);
  for (const source of Object.values(document.sources)) source.observed = at(msAgo);
  return document;
}

/** A page opened with the console already gone, and only this device's copy.
 *
 * The case this slice exists for and the ordinary one: the app is installed to a
 * home screen and opened on a train, and nothing has started the console since the
 * captain stopped it. Nothing here ever answers live, so `readAt` stays null for the
 * whole session and the saved copy is the only thing on screen.
 */
async function coldOffline(t, document) {
  const page = load({ snapshot: null, stream: false });
  if (t) t.after(() => page.close());
  page.answer = async () => cached(document);
  await until(page,
              (p) => bar(p)?.dataset.stale === 'yes' && /Saved copy/.test(text(p)),
              'the saved copy was never announced');
  await settle(page);
  return page;
}

test('a snapshot served off this device is said to be one, loudly', async (t) => {
  const document = snapshot({ tasks: [task('a-task', { status: 'blocked' })] });
  const page = await opened(t, { snapshot: document });
  page.answer = async () => cached(document);
  page.streams[0].connect();
  await page.tick(5_000);
  const said = text(page);
  assert.equal(bar(page).dataset.stale, 'yes');
  assert.match(said, /Saved copy from this device/);
  assert.match(said, /could not be reached/);
  assert.match(said, /a-task/, 'the saved fleet is still shown');
});

test('a saved copy does not restart the clock on the last read', async (t) => {
  const document = snapshot({ tasks: [task('a-task')] });
  const page = await opened(t, { snapshot: document });
  page.streams[0].connect();
  await settle(page);
  // From here the console is gone and the service worker is answering every read
  // out of its cache.
  page.answer = async () => cached(document);
  await page.tick(130_000);
  // Two minutes have passed and the console has not answered since. A cached body is
  // not a read, so the age on the bar is the age of the real one.
  assert.match(text(page), /2m ago/);
  assert.equal(bar(page).dataset.stale, 'yes');
});

test('nothing anywhere in the app can send, online or offline', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [task('a-task', { status: 'blocked', reason: 'ask the captain' })],
      obligations: [obligation('answer-me', { kind: 'decision' })],
    }),
  });
  for (const hash of ['#/', '#/obligations', '#/decisions', '#/projects',
                      '#/task/a-task']) {
    await go(page, hash);
    for (const selector of ['button', 'form', 'input', 'textarea', 'select',
                            '[contenteditable]', '[role=button]']) {
      assert.equal(page.document.body.querySelectorAll(selector).length, 0,
                   `${hash} has a ${selector}, which is a control this slice cannot`
                   + ' honour');
    }
  }
  await go(page, '#/');
  assert.match(text(page), /Answering these is done at the helm/);
});

test('the app makes no request that is not a read of the two documented routes',
  async (t) => {
    const page = await opened(t, { snapshot: snapshot({ tasks: [task('a-task')] }) });
    page.streams[0].connect();
    await page.tick(60_000);
    for (const request of page.requests) {
      assert.match(request.url, /^\/api\/state(\?rev=[^&]*)?$/);
      assert.ok(!request.options?.method || request.options.method === 'GET',
                `a request was made with method ${request.options.method}`);
      assert.equal(request.options?.body, undefined);
    }
    for (const stream of page.streams) {
      assert.equal(stream.url, '/api/stream');
    }
  });

test('the offline banner says what cannot be done rather than leaving it to be '
  + 'assumed', async (t) => {
  const document = snapshot();
  const page = await opened(t, { snapshot: document });
  page.answer = async () => { throw new TypeError('Failed to fetch'); };
  page.streams[0].connect();
  await page.tick(70_000);
  assert.match(text(page), /This console only reads\. Nothing can be sent from here\./);
});

test('a saved copy opened with no console ever reached says how old it is, on every '
  + 'screen', async (t) => {
  // The failure this is named after: `Owed` reads `Nothing is waiting on you` under a
  // banner saying only that this came off the device. On every screen but the
  // overview there was then no age anywhere, so a three day old cache read as a fleet
  // with nothing waiting.
  const page = await coldOffline(t, saved(3 * DAY, {
    tasks: [task('a-task', { status: 'blocked', reason: 'ask the captain' })],
  }));
  for (const hash of ['#/', '#/obligations', '#/decisions', '#/projects',
                      '#/task/a-task']) {
    await go(page, hash);
    const said = bar(page).textContent.replace(/\s+/g, ' ').trim();
    assert.match(said, /Saved copy from this device, read 3d ago/,
                 `${hash} shows a saved copy it does not date`);
    assert.match(said, /never read from the console in this session/,
                 `${hash} stopped saying the console has not been reached`);
  }
  await go(page, '#/obligations');
  const said = text(page);
  assert.match(said, /Nothing is waiting on you/);
  assert.match(said, /read 3d ago/,
               'an empty obligations screen off a saved copy has to carry its age');
});

test('the age of a saved copy is the age of the fleet in it, not of the page or the '
  + 'cache hit', async (t) => {
  // Opened just under a day after the snapshot was observed, and left open past it.
  // Neither the moment the page opened nor the moment the service worker handed the
  // body back is an answer to how old the fleet is - it hands back the same bytes
  // every time - so the age has to keep climbing off the stamp in them.
  const page = await coldOffline(t, saved(DAY - 2 * 60_000));
  assert.match(text(page), /Saved copy from this device, read 23h 58m ago/);
  await page.tick(4 * 60_000);
  assert.match(text(page), /Saved copy from this device, read 1d ago/);
  assert.match(text(page), /never read from the console in this session/,
               'a saved copy that aged is still not a read');
});

test('a saved copy whose own stamp will not parse is of unknown age, never of none',
  async (t) => {
    for (const observed of [undefined, null, '', 'the other day', 12345]) {
      const document = saved(3 * DAY);
      document.observed = observed;
      const page = await coldOffline(t, document);
      const said = text(page);
      assert.match(said, /Saved copy from this device, of unknown age/,
                   `an \`observed\` of ${JSON.stringify(observed)} was not said`);
      assert.doesNotMatch(said, /read 0s ago/,
                          'an unreadable stamp became a fresh read');
      page.close();
    }
  });

test('a live read after a saved copy is a read again, and dates itself as one',
  async (t) => {
    const document = saved(3 * DAY, { tasks: [task('a-task')] });
    const page = await coldOffline(t, document);
    assert.match(text(page), /read 3d ago/);
    // The console comes back. From here the bar is dating a real read of this
    // session, and the saved copy it was standing in for is gone from it.
    page.answer = async () => json(snapshot({ revision: 'rev-2',
                                              tasks: [task('a-task')] }));
    await page.tick(60_000);
    assert.equal(bar(page).dataset.stale, 'no');
    const said = text(page);
    assert.match(said, /last read 0s/);
    assert.doesNotMatch(said, /Saved copy from this device/);
    assert.doesNotMatch(said, /never read from the console in this session/);
  });
