/**
 * The home screen: is everything under control, answered in the order a captain
 * needs it.
 *
 * `Needs you` is first and is the most important line here. It is the only panel that
 * says something out loud when it is empty, because an empty panel reads as "fine"
 * and this one is only fine when it says so.
 */

import { readFleet, readHealth, readStore, unknownSources, UNAVAILABLE }
  from './sources.js';
import { agentInPane, byId, oldestFirst, ownerPane, readiness } from './model.js';
import { Age, Chip, Empty, Field, Fields, List, Panel, Row, StatusChip, Trouble,
  Value } from './ui.jsx';

function TaskRow({ task, trailing }) {
  return (
    <Row to={`/task/${encodeURIComponent(task.id)}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-100">
            <Value value={task.title} missing="no title" />
          </p>
          <p className="mt-0.5 truncate font-mono text-xs text-slate-400">
            <Value value={task.id} />
            {task.project ? <> · <Value value={task.project} /></> : null}
          </p>
        </div>
        <StatusChip status={task.status} />
      </div>
      {trailing ? <div className="mt-2 text-xs text-slate-300">{trailing}</div> : null}
      <p className="mt-1 text-xs text-slate-500">
        moved <Age stamp={task.updated} />
      </p>
    </Row>
  );
}

function NeedsYou({ tasks, obligations }) {
  const blocked = oldestFirst(
    tasks.level === UNAVAILABLE
      ? [] : tasks.records.filter((t) => t.status === 'blocked'), 'updated');
  const decisions = oldestFirst(
    obligations.level === UNAVAILABLE
      ? [] : obligations.records.filter((o) => o.kind === 'decision'), 'opened');
  const down = tasks.level === UNAVAILABLE || obligations.level === UNAVAILABLE;
  const count = blocked.length + decisions.length;
  return (
    <Panel title="Needs you" tone={count || down ? 'attention' : 'plain'}
      note={down ? 'incomplete' : `${count}`}>
      <Trouble source={obligations} what="What is owed" />
      <Trouble source={tasks} what="The queue" />
      {count === 0 && !down
        ? <Empty>Nothing needs you. No open decision, no blocked task.</Empty>
        : null}
      <List>
        {decisions.map((decision) => (
          <Row key={decision.id} to="/obligations">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm text-slate-100">
                <Value value={decision.body} missing="no body" />
              </p>
              <Chip tone="warn">decision</Chip>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              open <Age stamp={decision.opened} suffix="" />
              {decision.task ? <> · <Value value={decision.task} /></> : null}
            </p>
          </Row>
        ))}
        {blocked.map((task) => (
          <TaskRow key={task.id} task={task}
            trailing={<span className="text-amber-200">
              <Value value={task.reason} missing="blocked with no reason recorded" />
            </span>} />
        ))}
      </List>
      {count && !down
        ? <p className="mt-3 text-xs text-slate-500">
          This console reads. Answering these is done at the helm.
        </p>
        : null}
    </Panel>
  );
}

function InFlight({ tasks, fleet }) {
  const doing = tasks.level === UNAVAILABLE
    ? [] : oldestFirst(tasks.records.filter((t) => t.status === 'doing'), 'updated');
  return (
    <Panel title="In flight" note={tasks.level === UNAVAILABLE ? null : `${doing.length}`}>
      <Trouble source={tasks} what="The queue" />
      <Trouble source={fleet} what="Herdr" />
      {tasks.level !== UNAVAILABLE && doing.length === 0
        ? <Empty>No minion is working. Nothing is in flight.</Empty>
        : null}
      <List>
        {doing.map((task) => {
          const { kind, pane } = ownerPane(task.owner);
          const agent = fleet.level === UNAVAILABLE
            ? null : agentInPane(pane, fleet.agents);
          let said;
          if (fleet.level === UNAVAILABLE) {
            said = <Chip tone="warn">minion unknown</Chip>;
          } else if (!pane) {
            said = <Chip tone="stop">owner names no pane</Chip>;
          } else if (!agent) {
            said = <Chip tone="stop">herdr has no agent in {pane}</Chip>;
          } else {
            said = <Chip tone="live">
              <Value value={agent.agent_status} missing="status unknown" />
            </Chip>;
          }
          return (
            <TaskRow key={task.id} task={task}
              trailing={<span className="flex flex-wrap items-center gap-2">
                {said}
                <span className="font-mono text-slate-400">
                  <Value value={task.owner} missing="unowned" />
                </span>
                {agent && kind && agent.agent && agent.agent !== kind
                  ? <Chip tone="stop">
                    that pane now holds <Value value={agent.agent} />
                  </Chip>
                  : null}
              </span>} />
          );
        })}
      </List>
    </Panel>
  );
}

function Ready({ tasks }) {
  const index = byId(tasks.level === UNAVAILABLE ? [] : tasks.records);
  const todo = tasks.level === UNAVAILABLE
    ? [] : tasks.records.filter((t) => t.status === 'todo');
  const ready = [];
  const waiting = [];
  for (const task of todo) {
    const state = readiness(task, index);
    (state.ready ? ready : waiting).push({ task, state });
  }
  return (
    <Panel title="Ready" note={tasks.level === UNAVAILABLE ? null : `${ready.length}`}>
      <Trouble source={tasks} what="The queue" />
      {tasks.level !== UNAVAILABLE && ready.length === 0
        ? <Empty>
          Nothing is ready to start.
          {waiting.length
            ? ` ${waiting.length} waiting on work that is not done.`
            : ' The queue holds no unstarted task.'}
        </Empty>
        : null}
      <List>
        {oldestFirst(ready.map((r) => r.task), 'updated').map((task) => (
          <TaskRow key={task.id} task={task} />
        ))}
      </List>
      {waiting.length
        ? <details className="mt-3">
          <summary className="cursor-pointer text-xs text-slate-400">
            {waiting.length} waiting on a dependency
          </summary>
          <div className="mt-2 space-y-2">
            {waiting.map(({ task, state }) => (
              <TaskRow key={task.id} task={task}
                trailing={<span className="text-slate-400">
                  waits on {state.waiting.map((w) => (
                    <span key={w.id} className="font-mono">
                      {' '}<Value value={w.id} />
                      {w.status === null
                        ? <span className="text-red-300"> (not in the queue)</span>
                        : <span className="text-slate-500"> ({w.status})</span>}
                    </span>
                  ))}
                </span>} />
            ))}
          </div>
        </details>
        : null}
    </Panel>
  );
}

/** Evidence, with an age on it, and never a green tick.
 *
 * `siana-read health` deliberately reports the three parts apart and passes no
 * verdict, because which combination is healthy is a judgement about the fleet: no
 * SIANA running is the ordinary state between sessions. So this shows what was found
 * and when, and leaves the reading of it to the captain. */
function Coverage({ health, snapshot }) {
  const session = health.session;
  const watch = health.watch;
  const wake = health.wake;
  const unknown = unknownSources(snapshot);
  return (
    <Panel title="Coverage" note={health.at ? <Age stamp={health.at} /> : null}>
      <Trouble source={health} what="The helm" />
      {health.level === UNAVAILABLE ? null : (
        <Fields>
          <Field label="SIANA">
            {session
              ? <span>
                {session.alive
                  ? <Chip tone="live">a session is running</Chip>
                  : <Chip tone="plain">no session running</Chip>}
                <span className="ml-2 text-slate-300">
                  <Value value={session.why} missing="" />
                </span>
                {session.harness || session.pid
                  ? <span className="mt-1 block font-mono text-xs text-slate-500">
                    <Value value={session.harness} missing="no harness" />
                    {' · pid '}<Value value={session.pid} missing="none" />
                    {' · '}<Value value={session.pane} missing="no pane" />
                  </span>
                  : null}
                {session.error
                  ? <span className="mt-1 block text-red-300">
                    <Value value={session.error} />
                  </span>
                  : null}
              </span>
              : <span className="text-red-300">
                this answer carried no session record
              </span>}
          </Field>
          <Field label="Watcher">
            {watch
              ? <span>
                <Chip tone={watch.exit === 0 ? 'plain' : 'stop'}>
                  {'siana-watch --status exited '}
                  <Value value={watch.exit} missing="on a signal" />
                </Chip>
                {watch.stdout
                  ? <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-950
                    p-2 font-mono text-xs whitespace-pre-wrap text-slate-300">
                    <Value value={watch.stdout} />
                  </pre>
                  : <span className="mt-1 block text-amber-300">
                    it printed nothing
                  </span>}
                {watch.stderr
                  ? <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-950
                    p-2 font-mono text-xs whitespace-pre-wrap text-amber-200">
                    <Value value={watch.stderr} />
                  </pre>
                  : null}
                {watch.error
                  ? <span className="mt-1 block text-red-300">
                    <Value value={watch.error} />
                  </span>
                  : null}
              </span>
              : <span className="text-red-300">
                this answer carried no watcher record
              </span>}
          </Field>
          <Field label="Wakes">
            {wake
              ? <span>
                <Value value={wake.pending} missing="?" /> pending,{' '}
                <Value value={wake.consumed} missing="?" /> consumed
                {Array.isArray(wake.errors) && wake.errors.length
                  ? <span className="mt-1 block text-red-300">
                    {wake.errors.map((e, i) => (
                      <span key={i} className="block"><Value value={e} /></span>
                    ))}
                  </span>
                  : null}
              </span>
              : <span className="text-red-300">
                this answer carried no wake record
              </span>}
          </Field>
          {snapshot?.home
            ? <Field label="Home" value={snapshot.home} mono />
            : null}
        </Fields>
      )}
      {unknown.length
        ? <p role="alert" className="mt-3 rounded-xl bg-amber-950/40 px-3 py-2
          text-xs text-amber-100 ring-1 ring-amber-800">
          The console also answered about {unknown.join(', ')}, which this app does
          not know how to show. It is newer than this app.
        </p>
        : null}
    </Panel>
  );
}

export function Overview({ snapshot }) {
  const tasks = readStore(snapshot, 'tasks');
  const obligations = readStore(snapshot, 'obligations');
  const fleet = readFleet(snapshot);
  const health = readHealth(snapshot);
  return (
    <div className="space-y-4">
      <NeedsYou tasks={tasks} obligations={obligations} />
      <InFlight tasks={tasks} fleet={fleet} />
      <Ready tasks={tasks} />
      <Coverage health={health} snapshot={snapshot} />
    </div>
  );
}
