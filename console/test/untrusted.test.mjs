/**
 * Every string in the fleet is untrusted text.
 *
 * A task title is written by an agent, an obligation body by SIANA, a terminal title
 * by whatever is running in a pane. None of those is a person typing into this app,
 * and the page they land on reads the captain's whole queue. So the rule is that no
 * fleet string ever becomes markup, an attribute, a URL or a script, and this file
 * puts the payloads that would prove otherwise through every screen.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { opened, go, html, text } from './harness.mjs';
import { agent, decision, obligation, project, snapshot, task } from './fixtures.mjs';

const PAYLOADS = [
  '<img src=x onerror="globalThis.__owned = true">',
  '</script><script>globalThis.__owned = true</script>',
  '<svg onload="globalThis.__owned = true"></svg>',
  '"><iframe src="https://evil.example.com"></iframe>',
];

const MARK = PAYLOADS.join(' ');

test('a record full of markup renders as text and runs nothing', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [task('nasty-task', {
        title: MARK,
        status: 'blocked',
        reason: MARK,
        verify: MARK,
        owner: MARK,
        context: [MARK],
      })],
      projects: [project('siana', { ship: MARK, orders: MARK })],
      obligations: [obligation('nasty-owe', { body: MARK, kind: 'decision' })],
      decisions: [decision('nasty-call', {
        action: MARK, reason: MARK, evidence: [MARK], alternatives: [MARK],
        principles: [MARK],
      })],
      agents: [agent({ pane: 'w3S:p2', status: MARK })],
    }),
  });
  for (const hash of ['#/', '#/obligations', '#/decisions', '#/decision/nasty-call',
                      '#/projects', '#/project/siana', '#/task/nasty-task']) {
    await go(page, hash);
    assert.equal(page.window.__owned, undefined,
                 `${hash} executed something out of a record`);
    // The application's own tree only. `head` holds the one script tag and the one
    // stylesheet link the build emitted, and those are the console's own.
    const body = page.document.body;
    assert.equal(body.querySelectorAll('script').length, 0,
                 `${hash} grew a script element`);
    assert.equal(body.querySelectorAll('iframe, object, embed').length, 0,
                 `${hash} grew an embedded document`);
    assert.equal(body.querySelectorAll('img, svg').length, 0,
                 `${hash} grew an element that fetches`);
  }
});

test('the payload is on screen, as the text it is', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({ tasks: [task('nasty-task', { title: PAYLOADS[0] })] }),
  });
  // Shown, and shown whole: a console that silently dropped what it could not render
  // safely would hide a task from the captain to protect itself.
  assert.match(text(page), /<img src=x onerror=/);
  assert.doesNotMatch(html(page), /<img/);
});

test('no fleet string reaches an attribute that could fetch or navigate',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        tasks: [task('nasty-task', { title: 'javascript:globalThis.__owned = true' })],
        obligations: [obligation('nasty-owe', {
          body: 'javascript:globalThis.__owned = true',
        })],
      }),
    });
    for (const hash of ['#/', '#/obligations']) {
      await go(page, hash);
      for (const node of page.document.body
        .querySelectorAll('[href], [src], [action]')) {
        const value = node.getAttribute('href') ?? node.getAttribute('src')
          ?? node.getAttribute('action');
        assert.match(value, /^#\//, `an attribute carries ${value}`);
      }
    }
  });

test('a task id full of route characters cannot rewrite the route', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [task('a-task', { title: 'ordinary' })],
    }),
  });
  // The id here is what a link is built from, so the encoding is what stops a record
  // from steering the router.
  const nasty = snapshot({ tasks: [task('a-task')] });
  nasty.sources.tasks.document.records[0].id = '../../decisions';
  nasty.revision = 'rev-2';
  page.answer = async () => new Response(JSON.stringify(nasty),
    { status: 200, headers: { 'content-type': 'application/json' } });
  page.streams[0].connect();
  page.streams[0].announce('rev-2');
  await go(page, '#/');
  const hrefs = [...page.document.querySelectorAll('main a')]
    .map((node) => node.getAttribute('href'));
  for (const href of hrefs) {
    assert.doesNotMatch(href, /\.\./, `a record steered the router: ${href}`);
  }
});
