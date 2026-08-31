/**
 * Every screen under the home screen, and the one rule they share: an id the store
 * does not hold is said out loud.
 *
 * A drilldown that rendered nothing for a missing record is indistinguishable from
 * one whose store failed to load, and the captain reads both as "empty".
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { opened, go, text } from './harness.mjs';
import { agent, at, decision, obligation, project, snapshot, task }
  from './fixtures.mjs';

const HOUR = 3_600_000;

test('the registry lists projects and each one opens', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      projects: [project('siana', { pipeline: true, qa: 'a QA minion',
                                    target: 'main' }),
                 project('datafile')],
      tasks: [task('one-task', { project: 'siana' })],
    }),
    hash: '#/projects',
  });
  assert.match(text(page), /siana/);
  assert.match(text(page), /datafile/);
  assert.match(text(page), /1 open/);
  await go(page, '#/project/siana');
  const said = text(page);
  assert.match(said, /a run validates ship work here/);
  assert.match(said, /main/);
  assert.match(said, /one-task/);
});

test('a project the registry does not hold is named rather than left blank',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({ projects: [project('siana')] }),
      hash: '#/project/nowhere',
    });
    assert.match(text(page), /Nothing here is called nowhere/);
  });

test('a project with no tasks says so rather than showing an empty list',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({ projects: [project('siana')] }),
      hash: '#/project/siana',
    });
    assert.match(text(page), /No task in the queue names this project/);
  });

test('task detail carries what a captain needs to act at the helm', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [
        task('groundwork', { status: 'done', title: 'the thing before' }),
        task('the-task', {
          status: 'doing',
          title: 'build the console',
          verify: 'siana-pipeline check',
          deps: ['groundwork', 'never-existed'],
          context: ['/Users/captain/.siana/reports/a-scout.md'],
          owner: 'claude@w3S:p2',
          cwd: '/Users/captain/work/siana',
        }),
      ],
      agents: [agent({ pane: 'w3S:p2', kind: 'claude', status: 'working' })],
    }),
    hash: '#/task/the-task',
  });
  const said = text(page);
  assert.match(said, /build the console/);
  assert.match(said, /siana-pipeline check/);
  assert.match(said, /claude@w3S:p2/);
  assert.match(said, /working/);
  assert.match(said, /the thing before/);
  assert.match(said, /never-existed/);
  assert.match(said, /not in the queue/);
  assert.match(said, /reports\/a-scout\.md/);
  assert.match(said, /\$SIANA_HOME\/briefs\/the-task\.md/);
  assert.match(said, /\$SIANA_HOME\/reports\/the-task\.md/);
  assert.match(said, /This console serves no file off disk/);
});

test('a task with no dependencies says so', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({ tasks: [task('a-task')] }),
    hash: '#/task/a-task',
  });
  assert.match(text(page), /This task waits on no other work/);
});

test('a task the queue does not hold is named rather than left blank', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({ tasks: [task('a-task')] }),
    hash: '#/task/gone-task',
  });
  assert.match(text(page), /Nothing here is called gone-task/);
});

test('open obligations are oldest first, with an age', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      obligations: [
        obligation('newer-one', { body: 'the newer promise', opened: at(HOUR) }),
        obligation('oldest-one', { body: 'the oldest promise', opened: at(9 * HOUR) }),
        obligation('middle-one', { body: 'the middle promise', opened: at(5 * HOUR) }),
      ],
    }),
    hash: '#/obligations',
  });
  const bodies = [...page.document.querySelectorAll('main a')]
    .map((node) => node.textContent)
    .filter((said) => /promise/.test(said));
  assert.deepEqual(bodies.map((said) => said.match(/the \w+ promise/)[0]),
                   ['the oldest promise', 'the middle promise', 'the newer promise']);
  assert.match(text(page), /open 9h/);
});

test('decisions and promises are kept apart, and empty is said out loud',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        obligations: [obligation('answer-me', { kind: 'decision',
                                                body: 'a question for you' })],
      }),
      hash: '#/obligations',
    });
    assert.match(text(page), /a question for you/);
    assert.match(text(page), /SIANA owes you nothing/);
  });

test('an obligation of a kind this app does not know is shown and flagged',
  async (t) => {
    const page = await opened(t, {
      snapshot: snapshot({
        obligations: [obligation('odd-one', { kind: 'wager', body: 'something new' })],
      }),
      hash: '#/obligations',
    });
    assert.match(text(page), /something new/);
    assert.match(text(page), /kind wager/);
  });

test('the decision log opens onto the whole reasoning', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      decisions: [decision('publish-a-thing', {
        action: 'siana-publish a-task',
        verdict: 'refused',
        reason: 'no grant was in force',
        evidence: ['task:a-task done', 'reports/qa-a-task.md'],
        alternatives: ['waiting for the morning'],
        principles: ['Nothing lands that a second pair of eyes has not seen.'],
      })],
    }),
    hash: '#/decisions',
  });
  assert.match(text(page), /siana-publish a-task/);
  await go(page, '#/decision/publish-a-thing');
  const said = text(page);
  assert.match(said, /no grant was in force/);
  assert.match(said, /reports\/qa-a-task\.md/);
  assert.match(said, /waiting for the morning/);
  assert.match(said, /Nothing lands that a second pair of eyes has not seen/);
  assert.match(said, /R2/);
});

test('an empty decision log says what empty means here', async (t) => {
  const page = await opened(t, { snapshot: snapshot(), hash: '#/decisions' });
  assert.match(text(page), /Nothing has been proposed or refused/);
});

test('a decision the log does not hold is named rather than left blank', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({ decisions: [decision('one-decision')] }),
    hash: '#/decision/another',
  });
  assert.match(text(page), /Nothing here is called another/);
});

test('a route this app does not have says so rather than falling back', async (t) => {
  const page = await opened(t, { snapshot: snapshot(), hash: '#/nowhere/at/all' });
  assert.match(text(page), /This console has no screen at/);
});

test('every link in the app is a fragment, so no link is a request', async (t) => {
  const page = await opened(t, {
    snapshot: snapshot({
      tasks: [task('a-task', { status: 'doing', owner: 'claude@w3S:p2' })],
      projects: [project('siana')],
      obligations: [obligation('owe-one', { task: 'a-task' })],
      decisions: [decision('one-decision', { task: 'a-task' })],
    }),
  });
  for (const hash of ['#/', '#/projects', '#/project/siana', '#/task/a-task',
                      '#/obligations', '#/decisions', '#/decision/one-decision']) {
    await go(page, hash);
    const hrefs = [...page.document.querySelectorAll('a')]
      .map((node) => node.getAttribute('href'));
    assert.ok(hrefs.length, `${hash} rendered no links at all`);
    for (const href of hrefs) {
      assert.match(href, /^#\//, `${hash} has a link that is not a fragment: ${href}`);
    }
  }
});
