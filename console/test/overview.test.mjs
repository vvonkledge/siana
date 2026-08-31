/**
 * The home screen, against the built bundle.
 *
 * `Needs you` is the one panel here that is worth a suite of its own. Every other
 * panel being wrong costs the captain a second look; this one being wrong costs them
 * a night, because an empty `Needs you` is what they go to bed on.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { opened, go, text } from './harness.mjs';
import { NOW, agent, at, decision, health, obligation, snapshot, store, task }
  from './fixtures.mjs';

const MINUTE = 60_000;

test('an open decision and a blocked task are both what needs the captain',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        tasks: [task('stuck-task', { status: 'blocked',
                                     reason: 'the contract has no field for this' }),
                task('quiet-task', { status: 'todo' })],
        obligations: [obligation('pick-a-name', { kind: 'decision',
                                                  body: 'name the new store' })],
      }),
    });
    const said = text(page);
    assert.match(said, /Needs you/);
    assert.match(said, /name the new store/);
    assert.match(said, /the contract has no field for this/);
    // The one task that needs nothing from the captain is not in this panel.
    const panel = [...page.document.querySelectorAll('section')]
      .find((node) => /Needs you/.test(node.textContent));
    assert.doesNotMatch(panel.textContent, /quiet-task/);
  });

test('a blocked task in Needs you is still reachable, reason and all', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [task('stuck-task', { status: 'blocked', reason: 'herdr never answered' })],
    }),
  });
  const link = [...page.document.querySelectorAll('a')]
    .find((node) => /stuck-task/.test(node.textContent));
  assert.ok(link, 'a blocked task is not a dead row; it links to its detail');
  assert.equal(link.getAttribute('href'), '#/task/stuck-task');
  await go(page, '#/task/stuck-task');
  assert.match(text(page), /Blocked/);
  assert.match(text(page), /herdr never answered/);
});

test('nothing needing the captain is said out loud and never left blank',
  async (t) => {
    const page = await opened(t, { snapshot: snapshot({ tasks: [task('a-task')] }) });
    assert.match(text(page), /Nothing needs you\. No open decision, no blocked task\./);
  });

test('a blocked task with no reason recorded says that rather than nothing',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({ tasks: [task('bare-task', { status: 'blocked' })] }),
    });
    assert.match(text(page), /blocked with no reason recorded/);
  });

test('in flight shows what herdr says each minion is doing', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [task('live-task', { status: 'doing', owner: 'claude@w3S:p2' })],
      agents: [agent({ pane: 'w3S:p2', kind: 'claude', status: 'working' })],
    }),
  });
  const panel = [...page.document.querySelectorAll('section')]
    .find((node) => /In flight/.test(node.textContent));
  assert.match(panel.textContent, /live-task/);
  assert.match(panel.textContent, /working/);
});

test('a doing task whose pane herdr does not hold is called out', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [task('orphan-task', { status: 'doing', owner: 'claude@w9Z:p9' })],
      agents: [agent({ pane: 'w3S:p2' })],
    }),
  });
  assert.match(text(page), /herdr has no agent in w9Z:p9/);
});

test('a doing task whose owner names no pane is called out', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [task('hand-claimed', { status: 'doing', owner: 'somebody' })],
    }),
  });
  assert.match(text(page), /owner names no pane/);
});

test('nothing in flight is said out loud', async (t) => {
  const page = await opened(t, { snapshot: snapshot({ tasks: [task('a-task')] }) });
  assert.match(text(page), /No minion is working\. Nothing is in flight\./);
});

test('ready is todo work whose dependencies are done', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [
        task('groundwork', { status: 'done' }),
        task('can-start', { deps: ['groundwork'] }),
        task('must-wait', { deps: ['can-start'] }),
      ],
    }),
  });
  const panel = [...page.document.querySelectorAll('section')]
    .find((node) => /^\s*Ready/.test(node.textContent));
  assert.match(panel.textContent, /can-start/);
  assert.match(panel.textContent, /1 waiting on a dependency/);
  assert.match(panel.textContent, /must-wait/);
  assert.match(panel.textContent, /waits on/);
});

test('a dependency the queue does not hold is never counted as met', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({ tasks: [task('orphaned', { deps: ['nowhere-task'] })] }),
  });
  assert.match(text(page), /nowhere-task/);
  assert.match(text(page), /not in the queue/);
  const panel = [...page.document.querySelectorAll('section')]
    .find((node) => /^\s*Ready/.test(node.textContent));
  assert.match(panel.textContent, /Nothing is ready to start/);
});

test('nothing ready is said out loud, with what is waiting', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({ tasks: [task('a-task', { status: 'done' })] }),
  });
  assert.match(text(page), /Nothing is ready to start\./);
  assert.match(text(page), /The queue holds no unstarted task/);
});

test('coverage is evidence with an age on it and never a green tick', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      sources: {
        health: health({
          alive: true,
          watch: {
            command: ['siana-watch', '--status'],
            exit: 0,
            stdout: '  ok      watching (pid 900) since 2026-08-31T09:00:00Z\n',
            stderr: '',
            error: null,
          },
        }),
      },
    }),
  });
  const panel = [...page.document.querySelectorAll('section')]
    .find((node) => /Coverage/.test(node.textContent));
  assert.match(panel.textContent, /a session is running/);
  assert.match(panel.textContent, /pid 4242 is pi/);
  assert.match(panel.textContent, /watching \(pid 900\)/);
  assert.match(panel.textContent, /siana-watch --status exited 0/);
  assert.match(panel.textContent, /ago/, 'coverage says when it was observed');
});

test('a watcher that stopped is reported with what it said', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      sources: {
        health: health({
          exit: 1,
          watch: {
            command: ['siana-watch', '--status'],
            exit: 1,
            stdout: '  STOPPED the watcher exited on a failed dispatch\n',
            stderr: 'dispatch refused: no pane\n',
            error: null,
          },
        }),
      },
    }),
  });
  assert.match(text(page), /STOPPED the watcher exited on a failed dispatch/);
  assert.match(text(page), /dispatch refused: no pane/);
});

test('an age on screen moves as the clock does', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({ tasks: [task('a-task', { updated: at(MINUTE) })] }),
  });
  assert.match(text(page), /moved 1m ago/);
  await page.tick(4 * MINUTE);
  assert.match(text(page), /moved 5m ago/);
});

test('the queue and the decision log are reachable from the home screen',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({ decisions: [decision('publish-a-thing')] }),
    });
    const targets = [...page.document.querySelectorAll('nav a')]
      .map((node) => node.getAttribute('href'));
    assert.deepEqual(targets, ['#/', '#/obligations', '#/decisions', '#/projects']);
  });

test('a store that answered with something that is not a record still renders',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        sources: { tasks: store('tasks', [task('a-task'), 'not a record']) },
      }),
    });
    assert.match(text(page), /is damaged/);
    assert.match(text(page), /a-task/);
  });

test('the observed clock is the fixture clock', async (t) => {
  // The whole suite reads ages, so a page whose clock had drifted off the fixtures
  // would fail in ways that look like the app and are not.
  const page = await opened(t, { snapshot: snapshot() });
  assert.equal(page.window.Date.now(), NOW);
});
