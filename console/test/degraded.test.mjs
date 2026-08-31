/**
 * A source that could not be read has to look like one.
 *
 * This is the file that matters most in this app. `siana-read` refuses rather than
 * answering an empty store precisely so that a console cannot report "SIANA owes you
 * nothing" over an unreadable `obligations.jsonl`, and the six refusals do not share a
 * shape: a store carries `error` and `code`, and `fleet` carries `state: "unknown"`
 * and no `error` key at all. Each of those is driven here in the shape the real
 * command emits it, because a fixture that normalised them would let the app read one
 * field and pass.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { alerts, opened, go, text } from './harness.mjs';
import { agent, decision, fleetUnknown, health, obligation, project, refused,
  snapshot, store, task, unrunnable } from './fixtures.mjs';

test('an unreadable queue is a refusal on screen and never an empty queue',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        sources: {
          tasks: refused('tasks', {
            error: "tasks.jsonl cannot be read: [Errno 13] Permission denied",
            code: 'STORE_UNREADABLE',
            help: ['check the permissions on that file, then ask again'],
          }),
        },
      }),
    });
    const said = text(page);
    assert.match(said, /The queue could not be read/);
    assert.match(said, /Permission denied/);
    assert.match(said, /STORE_UNREADABLE/);
    assert.match(said, /check the permissions on that file/);
    assert.match(said, /This is not an empty fleet/);
    assert.doesNotMatch(said, /Nothing needs you/);
    assert.doesNotMatch(said, /Nothing is ready to start/);
    assert.doesNotMatch(said, /No minion is working/);
  });

test('an unreadable obligations store never reads as owing nothing', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      sources: {
        obligations: refused('obligations', {
          error: 'obligations.jsonl cannot be read',
          code: 'STORE_UNREADABLE',
        }),
      },
    }),
    hash: '#/obligations',
  });
  const said = text(page);
  assert.match(said, /What is owed could not be read/);
  assert.doesNotMatch(said, /SIANA owes you nothing/);
  assert.doesNotMatch(said, /Nothing is waiting on you/);
});

test('a corrupt store shows its bad lines beside the records it could read',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        sources: {
          tasks: store('tasks', [task('a-task')], {
            badLines: [{ line: 4, error: 'Expecting value: line 1 column 1' },
                       { line: 9, error: 'no key' }],
            total: 1,
          }),
        },
      }),
    });
    const said = text(page);
    assert.match(said, /The queue is damaged/);
    assert.match(said, /2 lines in this store could not be read/);
    assert.match(said, /Expecting value/);
    assert.match(said, /a-task/, 'what could be read is still shown');
  });

test('a drilldown over a damaged store says so rather than looking healthy',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        sources: {
          tasks: store('tasks', [task('a-task')], {
            badLines: [{ line: 4, error: 'Expecting value: line 1 column 1' }],
            total: 2,
          }),
        },
      }),
      hash: '#/task/a-task',
    });
    const said = text(page);
    assert.match(said, /a-task/);
    assert.match(said, /The queue is damaged/);
    assert.match(said, /1 line in this store could not be read/);
  });

test('a record lost to a damaged line is never called done and folded away',
  async (t) => {
    // The sharpest case in this file. The record the captain asked for may be
    // exactly the one an unreadable line took with it, and a screen that says it was
    // probably finished is this console inventing the one thing it exists to report.
    const page = await opened(t, {
      snapshot: snapshot({
        sources: {
          tasks: store('tasks', [task('a-task')], {
            badLines: [{ line: 4, error: 'Expecting value: line 1 column 1' }],
            total: 2,
          }),
        },
      }),
      hash: '#/task/fix-the-gate',
    });
    const said = text(page);
    assert.match(said, /The queue is damaged/);
    assert.match(said, /Nothing here is called fix-the-gate/);
    assert.match(said, /may be one of the records it lost/);
    assert.doesNotMatch(said, /done and folded away/);
  });

test('a damaged registry and a damaged log say so on their drilldowns too',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        projects: [project('siana')],
        decisions: [decision('one-decision')],
        sources: {
          projects: store('projects', [project('siana')], {
            badLines: [{ line: 2, error: 'no key' }],
          }),
          decisions: store('decisions', [decision('one-decision')], {
            badLines: [{ line: 7, error: 'no key' }],
          }),
        },
      }),
      hash: '#/project/siana',
    });
    assert.match(text(page), /The registry is damaged/);
    await go(page, '#/project/gone');
    assert.match(text(page), /The registry is damaged/);
    await go(page, '#/decision/one-decision');
    assert.match(text(page), /The decision log is damaged/);
    await go(page, '#/decision/gone');
    assert.match(text(page), /The decision log is damaged/);
    assert.match(text(page), /may be one of the records it lost/);
  });

test('an unreachable herdr is unknown and never a fleet with no minions',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        tasks: [task('live-task', { status: 'doing', owner: 'claude@w3S:p2' })],
        sources: { fleet: fleetUnknown() },
      }),
    });
    const said = text(page);
    assert.match(said, /Herdr could not be read/);
    assert.match(said, /HERDR_UNREACHABLE/);
    assert.match(said, /start it, then ask again/);
    assert.match(said, /minion unknown/);
    assert.doesNotMatch(said, /herdr has no agent/);
  });

test('an unknown herdr leaves task detail saying unknown and not idle', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [task('live-task', { status: 'doing', owner: 'claude@w3S:p2' })],
      agents: [agent({ pane: 'w3S:p2' })],
      sources: { fleet: fleetUnknown() },
    }),
    hash: '#/task/live-task',
  });
  assert.match(text(page), /minion unknown/);
});

test('siana-read missing altogether is named on every panel', async (t) => {
  const missing = (name) => unrunnable(name, {
    message: 'siana-read is not on PATH, so nothing here can read the fleet',
  });
  const page = await opened(t, {
    snapshot: snapshot({
      sources: {
        tasks: missing('tasks'), projects: missing('projects'),
        obligations: missing('obligations'), decisions: missing('decisions'),
        fleet: missing('fleet'), health: missing('health'),
      },
    }),
  });
  const said = alerts(page).join(' | ');
  for (const what of ['The queue', 'What is owed', 'Herdr', 'The helm']) {
    assert.match(said, new RegExp(`${what} could not be read`));
  }
  assert.match(said, /not on PATH/);
  assert.doesNotMatch(text(page), /Nothing needs you/);
});

test('a store answering without a records list is malformed and not empty',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        sources: {
          decisions: {
            source: 'decisions',
            command: ['siana-read', 'decisions'],
            observed: '2026-08-31T11:59:58Z',
            exit: 0,
            signal: null,
            stderr: null,
            error: null,
            document: { source: 'decisions', revision: 'x', entries: [] },
          },
        },
      }),
      hash: '#/decisions',
    });
    const said = text(page);
    assert.match(said, /The decision log could not be read/);
    assert.match(said, /without a records list/);
    assert.doesNotMatch(said, /Nothing has been proposed or refused/);
  });

test('a source the console did not answer about at all is named', async (t) => {
  const document = snapshot();
  delete document.sources.health;
  const page = await opened(t, { snapshot: document });
  assert.match(text(page), /The helm could not be read/);
  assert.match(text(page), /the console said nothing about health/);
});

test('a source this app does not know is reported rather than ignored', async (t) => {
  const document = snapshot();
  document.sources.transcript = store('transcript', []);
  const page = await opened(t, { snapshot: document });
  assert.match(text(page), /also answered about transcript/);
});

test('health reports the part it could not read and keeps the parts it could',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        sources: {
          health: health({
            exit: 1,
            watch: { command: ['siana-watch', '--status'], exit: null, stdout: '',
                     stderr: '', error: 'siana-watch is not on PATH' },
          }),
        },
      }),
    });
    const said = text(page);
    assert.match(said, /The helm is damaged/);
    assert.match(said, /the watcher record could not be read/);
    assert.match(said, /siana-watch is not on PATH/);
    assert.match(said, /no SIANA session is recorded/, 'the other parts survive');
  });

test('a snapshot that is not a document at all leaves nothing looking healthy',
  async (t) => {
    const page = await opened(t, {
      snapshot: { console: 'siana-console', revision: 'rev-1', sources: null },
    });
    const said = text(page);
    assert.match(said, /could not be read/);
    assert.doesNotMatch(said, /Nothing needs you/);
  });

test('an unavailable source never contributes a count', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      obligations: [obligation('one-promise')],
      sources: { tasks: refused('tasks', { error: 'gone', code: 'STORE_UNREADABLE' }) },
    }),
  });
  const panel = [...page.document.querySelectorAll('section')]
    .find((node) => /Needs you/.test(node.textContent));
  assert.match(panel.textContent, /incomplete/,
               'a panel drawing on a source that failed says so instead of counting');
  await go(page, '#/projects');
  assert.match(text(page), /no count here is a count of open work/);
});
