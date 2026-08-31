/**
 * The link: what the app asks for, how often, and what it says about how current the
 * screen is.
 *
 * Three of these are about a cost the captain pays. The snapshot is the whole fleet,
 * and against a live herdr that is hundreds of kilobytes; the console honestly
 * announces a new revision every couple of seconds, because herdr's presentation
 * fields really do move that often. So an app that refetched on every announcement, or
 * that let two reads overlap, would spend a phone's battery on bodies it throws away.
 *
 * The rest are about the one thing worse than a slow console: a stale one that does
 * not say so.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { bar, json, load, opened, settle, text, until } from './harness.mjs';
import { at, health, snapshot, task } from './fixtures.mjs';

const FLOOR = 5_000;
const POLL = 10_000;
const STALE = 60_000;
const MINUTE = 60_000;

/** A fixture whose every age in `main` is measured in minutes, so that seconds
 * passing changes nothing on screen and a mutation really is a rerender. */
function steady(records, revision = 'rev-1') {
  return snapshot({
    revision,
    tasks: records,
    sources: { health: health({ at: at(5 * MINUTE) }) },
  });
}

function answering(document) {
  return async (url) => {
    const asked = new URL(url, 'http://127.0.0.1:8787').searchParams.get('rev');
    if (asked === document.revision) return new Response(null, { status: 204 });
    return json(document);
  };
}

/** A page that has read once and whose stream is up. */
async function connected(t, document) {
  const page = await opened(t, { snapshot: document });
  page.answer = answering(document);
  page.streams[0].connect();
  await settle(page);
  // The read the app makes when a stream opens, because the revision may have moved
  // while it was down. It answers 204 here, so nothing is on screen because of it.
  await page.tick(FLOOR);
  return page;
}

test('a revision announcement is refetched, and not before the floor', async (t) => {
  const page = await connected(t, steady([task('first-task')]));
  const before = page.requests.length;
  page.answer = answering(steady([task('second-task')], 'rev-2'));

  page.streams[0].announce('rev-2');
  await settle(page);
  assert.equal(page.requests.length, before,
               'an announcement inside the floor asked again straight away');

  await page.tick(FLOOR);
  assert.equal(page.requests.length, before + 1);
  assert.match(text(page), /second-task/);
});

test('a storm of announcements is one refetch', async (t) => {
  const page = await connected(t, steady([task('first-task')]));
  const before = page.requests.length;
  page.answer = answering(steady([task('second-task')], 'rev-9'));
  for (const revision of ['rev-2', 'rev-3', 'rev-4', 'rev-5', 'rev-9']) {
    page.streams[0].announce(revision);
    await settle(page, 1);
  }
  await page.tick(FLOOR);
  assert.equal(page.requests.length, before + 1,
               'five announcements produced more than one read');
  assert.match(text(page), /second-task/);
});

test('no two reads are ever in flight', async (t) => {
  const page = await connected(t, steady([task('first-task')]));
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  page.answer = async () => {
    await slow;
    return json(steady([task('second-task')], 'rev-2'));
  };
  page.streams[0].announce('rev-2');
  await page.tick(FLOOR);
  // The read is out. Everything the stream says while it is out has to wait for it.
  for (const revision of ['rev-3', 'rev-4', 'rev-5']) {
    page.streams[0].announce(revision);
    await settle(page, 1);
  }
  assert.equal(page.maxInFlight, 1, 'two reads overlapped');
  release();
  await settle(page);
  assert.equal(page.maxInFlight, 1, 'two reads overlapped after the first came back');
});

test('an announcement of the revision already on screen asks for nothing',
  async (t) => {
    const page = await connected(t, steady([task('first-task')]));
    const before = page.requests.length;
    page.streams[0].announce('rev-1');
    await page.tick(FLOOR * 4);
    assert.equal(page.requests.length, before);
  });

test('a stream frame that is not the protocol asks for nothing', async (t) => {
  const page = await connected(t, steady([task('first-task')]));
  const before = page.requests.length;
  page.streams[0].garble('not json at all');
  page.streams[0].garble('{"revision": 7}');
  await page.tick(FLOOR * 4);
  assert.equal(page.requests.length, before);
});

test('an unchanged fleet changes nothing on the screen', async (t) => {
  const page = await opened(t, { snapshot: steady([task('a-task')]) });
  page.answer = answering(steady([task('a-task')]));
  const changes = [];
  const watcher = new page.window.MutationObserver((records) => changes.push(...records));
  watcher.observe(page.document.querySelector('main'),
                  { childList: true, subtree: true, characterData: true,
                    attributes: true });
  page.streams[0].connect();
  await page.tick(FLOOR);
  assert.ok(page.requests.length > 1, 'nothing was reread, so nothing was proved');
  assert.deepEqual(changes.map((r) => r.type), [],
                   'a 204 rerendered the screen');
  watcher.disconnect();
});

test('a body carrying the revision already held changes nothing either', async (t) => {
  const page = await opened(t, { snapshot: steady([task('a-task')]) });
  // 200 with the same revision rather than 204, which is what a console that has just
  // restarted answers.
  page.answer = async () => json(steady([task('a-task')]));
  const changes = [];
  const watcher = new page.window.MutationObserver((records) => changes.push(...records));
  watcher.observe(page.document.querySelector('main'),
                  { childList: true, subtree: true, characterData: true });
  page.streams[0].connect();
  await page.tick(FLOOR);
  assert.deepEqual(changes.map((r) => r.type), []);
  watcher.disconnect();
});

test('losing the stream falls back to polling and says so', async (t) => {
  const page = await connected(t, steady([task('a-task')]));
  page.streams[0].drop();
  await settle(page);
  assert.equal(bar(page).dataset.status, 'reconnecting');

  for (let attempt = 0; attempt < 5; attempt += 1) {
    await page.tick(POLL * 8);
    page.streams.at(-1).drop();
    await settle(page);
  }
  assert.equal(bar(page).dataset.status, 'polling');
  assert.match(text(page), /polling/);

  const before = page.requests.length;
  await page.tick(POLL * 8);
  assert.ok(page.requests.length > before,
            'it said it was polling and then asked for nothing');
});

test('a stream that comes back is streaming again', async (t) => {
  const page = await connected(t, steady([task('a-task')]));
  page.streams[0].drop();
  await settle(page);
  await page.tick(POLL * 8);
  page.streams.at(-1).connect();
  await settle(page);
  assert.equal(bar(page).dataset.status, 'connected');
  assert.match(text(page), /connected/);
});

test('a browser with no event stream polls from the start and says so', async (t) => {
  const page = await opened(t, { snapshot: steady([task('a-task')]), stream: false });
  await settle(page);
  assert.equal(page.streams.length, 0);
  assert.equal(bar(page).dataset.status, 'polling');
  const before = page.requests.length;
  await page.tick(POLL * 2);
  assert.ok(page.requests.length > before, 'it polls, or it never reads again');
});

test('a read that never comes back is called stale, with its age', async (t) => {
  const page = await connected(t, steady([task('a-task')]));
  assert.equal(bar(page).dataset.stale, 'no');
  // The console accepted the request and never answered. Nothing failed, so nothing
  // says offline; what is on screen is simply no longer current, and that is the one
  // thing this bar exists to say.
  page.answer = () => new Promise(() => {});
  await page.tick(STALE + 10_000);
  assert.equal(bar(page).dataset.stale, 'yes');
  const said = text(page);
  assert.match(said, /Not live/);
  assert.match(said, /1m ago/);
  assert.match(said, /Nothing can be sent from here/);
});

test('a quiet stream is read again with room to spare, and the banner never flashes',
  async (t) => {
    // The read has to come back before the threshold rather than start at it.
    // `/api/state` runs six `siana-read` processes and is not instant, so a refresh
    // scheduled at exactly the staleness threshold would leave the loud banner up
    // for the whole round trip - once a minute, on a console that is connected and
    // current, which is how a captain learns to look past it.
    const page = await connected(t, steady([task('a-task')]));
    const slow = 10_000;
    page.answer = (url) => new Promise((resolve) => {
      page.window.setTimeout(() => resolve(answering(steady([task('a-task')]))(url)),
                             slow);
    });
    const loud = [];
    const watcher = new page.window.MutationObserver(() => {
      if (bar(page).dataset.stale === 'yes') loud.push(page.clock);
    });
    watcher.observe(bar(page),
                    { attributes: true, childList: true, subtree: true });
    await page.tick(5 * STALE);
    watcher.disconnect();
    assert.deepEqual(loud, [], 'the stale banner appeared on a healthy console');
    assert.ok(page.requests.length > 3, 'nothing was read across five minutes');
  });

test('a stream that goes quiet is still read again before it goes stale',
  async (t) => {
    // A connection that wedged without ever firing an error would otherwise leave
    // the screen behind a stale banner with nothing left that would ask again.
    const page = await connected(t, steady([task('first-task')]));
    const before = page.requests.length;
    page.answer = answering(steady([task('second-task')], 'rev-2'));
    await page.tick(STALE + 5_000);
    assert.ok(page.requests.length > before, 'nothing asked again');
    assert.match(text(page), /second-task/);
    assert.equal(bar(page).dataset.stale, 'no');
  });

test('a console that cannot be reached is offline, over what was last known',
  async (t) => {
    const page = await connected(t, steady([task('a-task')]));
    page.answer = async () => { throw new TypeError('Failed to fetch'); };
    page.streams[0].announce('rev-2');
    await page.tick(FLOOR);
    assert.equal(bar(page).dataset.status, 'offline');
    assert.match(text(page), /offline/);
    assert.match(text(page), /Failed to fetch/);
    assert.match(text(page), /a-task/, 'what was last known is still on screen');
  });

test('a console answering with something that is not a snapshot is not believed',
  async (t) => {
    const page = await connected(t, steady([task('a-task')]));
    page.answer = async () => json({ not: 'a snapshot' });
    page.streams[0].announce('rev-2');
    await page.tick(FLOOR);
    assert.equal(bar(page).dataset.status, 'offline');
    assert.match(text(page), /not a snapshot/);
    assert.match(text(page), /a-task/);
  });

test('the first read carries no revision and later reads carry the one held',
  async (t) => {
    const page = await connected(t, steady([task('a-task')]));
    assert.equal(page.requests[0].url, '/api/state');
    assert.match(page.requests[1].url, /^\/api\/state\?rev=rev-1$/);
    for (const request of page.requests) {
      assert.equal(request.options.cache, 'no-store');
    }
  });

test('nothing before the first snapshot is rendered as an empty fleet', async (t) => {
  const page = load({ snapshot: null });
  t.after(() => page.close());
  page.answer = async () => { throw new TypeError('Failed to fetch'); };
  await until(page, (p) => /no saved copy/.test(text(p)),
              'an unreachable console with nothing cached said something else');
  assert.doesNotMatch(text(page), /Nothing needs you/);
});
