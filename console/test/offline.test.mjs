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

import { bar, go, json, opened, settle, text } from './harness.mjs';
import { obligation, snapshot, task } from './fixtures.mjs';

/** What the service worker hands back when the console is unreachable. */
function cached(document) {
  return json(document, 200, { 'x-siana-cache': 'hit' });
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
