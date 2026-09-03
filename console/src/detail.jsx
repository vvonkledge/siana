/**
 * Everything below the home screen: the registry, one project, one task, what is
 * owed, and the decision log.
 *
 * A record that is not there is said out loud on every one of these. A drilldown that
 * rendered nothing for an id the store does not hold is indistinguishable from one
 * whose store failed to load, and the captain would read both as "empty".
 */

import { DEGRADED, readFleet, readStore, UNAVAILABLE } from './sources.js';
import { agentInPane, byId, newestFirst, oldestFirst, ownerPane } from './model.js';
import { Age, Card, Chip, Empty, Field, Fields, List, Panel, Row, StatusChip,
  Trouble, Value } from './ui.jsx';

/** An id the store does not hold.
 *
 * Separate from `Empty` on purpose: "there is no task called this" and "this task has
 * nothing in it" are different answers and only one of them means the link was wrong.
 *
 * And the store's own state comes first, because the record the captain asked for may
 * be exactly the one a damaged line took with it. Telling them it was probably done
 * and folded away, over a store that could not be read whole, is this console
 * inventing the one thing it is here to report. */
function Missing({ what, id, source, of }) {
  const damaged = source && source.level === DEGRADED;
  return (
    <Panel title={what} tone="attention">
      <Trouble source={source} what={of} />
      <p role="alert" className="rounded-xl bg-amber-950/40 px-3 py-3 text-sm
        text-amber-100 ring-1 ring-amber-800">
        Nothing here is called <span className="font-mono"><Value value={id} /></span>.
        {damaged
          ? ' That store could not be read whole, so this may be one of the records'
            + ' it lost rather than one that is gone.'
          : ' It may have been done and folded away, or the link may be from an older'
            + ' screen.'}
      </p>
    </Panel>
  );
}

function Strings({ values, empty }) {
  const list = Array.isArray(values) ? values : [];
  if (!list.length) return <span className="text-slate-500 italic">{empty}</span>;
  return (
    <ul className="space-y-1">
      {list.map((value, i) => (
        <li key={i} className="font-mono text-xs break-all text-slate-200">
          <Value value={value} />
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------ the registry

export function Projects({ snapshot }) {
  const projects = readStore(snapshot, 'projects');
  const tasks = readStore(snapshot, 'tasks');
  const records = [...projects.records].sort((a, b) => String(a.handle)
    .localeCompare(String(b.handle)));
  return (
    <Panel title="Projects" note={projects.level === UNAVAILABLE ? null
      : `${records.length}`}>
      <Trouble source={projects} what="The registry" />
      {projects.level !== UNAVAILABLE && !records.length
        ? <Empty>The registry holds no project.</Empty>
        : null}
      <List>
        {records.map((project) => {
          const open = tasks.level === UNAVAILABLE ? null
            : tasks.records.filter((t) => t.project === project.handle
              && t.status !== 'done').length;
          return (
            <Row key={project.handle}
              to={`/project/${encodeURIComponent(project.handle)}`}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-mono text-sm text-slate-100">
                  <Value value={project.handle} />
                </span>
                <Chip tone={open ? 'live' : 'plain'}>
                  {open === null ? 'queue unknown' : `${open} open`}
                </Chip>
              </div>
              <p className="mt-1 truncate font-mono text-xs text-slate-400">
                <Value value={project.path} />
              </p>
            </Row>
          );
        })}
      </List>
      {tasks.level === UNAVAILABLE
        ? <p className="mt-3 text-xs text-amber-300">
          The queue could not be read, so no count here is a count of open work.
        </p>
        : null}
    </Panel>
  );
}

export function Project({ snapshot, handle }) {
  const projects = readStore(snapshot, 'projects');
  const tasks = readStore(snapshot, 'tasks');
  const record = projects.records.find((p) => p.handle === handle);
  if (projects.level === UNAVAILABLE) {
    return <Panel title="Project"><Trouble source={projects} what="The registry" />
    </Panel>;
  }
  if (!record) {
    return <Missing what="Project" id={handle} source={projects}
      of="The registry" />;
  }
  const mine = tasks.level === UNAVAILABLE
    ? [] : tasks.records.filter((t) => t.project === handle);
  const states = ['blocked', 'doing', 'todo', 'done'];
  return (
    <div className="space-y-4">
      <Panel title={`Project ${record.handle}`}>
        <Trouble source={projects} what="The registry" />
        <Fields>
          <Field label="Path" value={record.path} mono />
          <Field label="Ship" value={record.ship} mono />
          <Field label="Pipeline">
            {record.pipeline
              ? <Chip tone="live">a run validates ship work here</Chip>
              : <Chip tone="plain">the verify runs once, at done</Chip>}
          </Field>
          <Field label="QA" value={record.qa} mono missing="no QA minion" />
          <Field label="Target" value={record.target}
            missing="not published; no target branch" />
          <Field label="Worktree">
            {record.worktree === false
              ? <Chip tone="warn">no isolation; minions work in place</Chip>
              : <Chip tone="plain">each minion gets a worktree</Chip>}
          </Field>
          <Field label="Automerge" value={record.automerge}
            missing="the captain merges in person" />
          <Field label="Orders" value={record.orders} mono
            missing="no extra standing orders" />
        </Fields>
      </Panel>
      <Panel title="Its tasks" note={tasks.level === UNAVAILABLE ? null
        : `${mine.length}`}>
        <Trouble source={tasks} what="The queue" />
        {tasks.level !== UNAVAILABLE && !mine.length
          ? <Empty>No task in the queue names this project.</Empty>
          : null}
        <div className="space-y-4">
          {states.map((state) => {
            const group = oldestFirst(mine.filter((t) => t.status === state),
                                      'updated');
            if (!group.length) return null;
            return (
              <div key={state}>
                <p className="mb-2 text-xs tracking-wide text-slate-400 uppercase">
                  {state} ({group.length})
                </p>
                <List>
                  {group.map((task) => (
                    <Row key={task.id} to={`/task/${encodeURIComponent(task.id)}`}>
                      <div className="flex items-start justify-between gap-3">
                        <span className="min-w-0 text-sm text-slate-100">
                          <Value value={task.title} missing="no title" />
                        </span>
                        <StatusChip status={task.status} />
                      </div>
                      <p className="mt-0.5 font-mono text-xs text-slate-400">
                        <Value value={task.id} />
                      </p>
                    </Row>
                  ))}
                </List>
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

// ---------------------------------------------------------------------- one task

export function Task({ snapshot, id }) {
  const tasks = readStore(snapshot, 'tasks');
  const fleet = readFleet(snapshot);
  if (tasks.level === UNAVAILABLE) {
    return <Panel title="Task"><Trouble source={tasks} what="The queue" /></Panel>;
  }
  const task = tasks.records.find((t) => t.id === id);
  if (!task) return <Missing what="Task" id={id} source={tasks} of="The queue" />;
  const index = byId(tasks.records);
  const { kind, pane } = ownerPane(task.owner);
  const agent = fleet.level === UNAVAILABLE ? null : agentInPane(pane, fleet.agents);
  return (
    <div className="space-y-4">
      <Panel title="Task" note={<StatusChip status={task.status} />}>
        <Trouble source={tasks} what="The queue" />
        <h1 className="text-lg leading-snug font-semibold text-slate-50">
          <Value value={task.title} missing="no title" />
        </h1>
        <p className="mt-1 font-mono text-xs text-slate-400">
          <Value value={task.id} />
        </p>
        {task.status === 'blocked'
          ? <div role="alert" className="mt-3 rounded-xl bg-red-950/50 px-3 py-3
            text-sm text-red-100 ring-1 ring-red-800">
            <p className="font-semibold">Blocked</p>
            <p className="mt-1">
              <Value value={task.reason}
                missing="no reason was recorded, which is itself worth chasing" />
            </p>
          </div>
          : null}
        <Fields>
          <Field label="Project">
            {task.project
              ? <a href={`#/project/${encodeURIComponent(task.project)}`}
                className="font-mono text-sky-300 underline">
                <Value value={task.project} />
              </a>
              : <Value value={null} missing="no project" />}
          </Field>
          <Field label="Verify" value={task.verify} mono />
          <Field label="Verify kind" value={task.verify_kind} />
          <Field label="Owner">
            {task.owner
              ? <span className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs"><Value value={task.owner} /></span>
                {fleet.level === UNAVAILABLE
                  ? <Chip tone="warn">minion unknown</Chip>
                  : (!pane ? <Chip tone="stop">this owner names no pane</Chip>
                    : (agent
                      ? <Chip tone="live">
                        <Value value={agent.agent_status} missing="status unknown" />
                      </Chip>
                      : <Chip tone="stop">herdr has no agent in {pane}</Chip>))}
                {agent && kind && agent.agent && agent.agent !== kind
                  ? <Chip tone="stop">
                    that pane now holds <Value value={agent.agent} />
                  </Chip>
                  : null}
              </span>
              : <Value value={null} missing="unowned" />}
          </Field>
          <Field label="Working directory" value={task.cwd} mono />
          <Field label="Base" value={task.base} mono />
          <Field label="Moved">
            <Age stamp={task.updated} />
            <span className="ml-2 font-mono text-xs text-slate-500">
              <Value value={task.updated} />
            </span>
          </Field>
        </Fields>
      </Panel>
      <Panel title="Depends on">
        {!Array.isArray(task.deps) || !task.deps.length
          ? <Empty>Nothing. This task waits on no other work.</Empty>
          : <List>
            {task.deps.map((dep) => {
              const found = typeof dep === 'string' ? index.get(dep) : null;
              return (
                <Row key={String(dep)} to={`/task/${encodeURIComponent(String(dep))}`}>
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-xs text-slate-200">
                      <Value value={dep} />
                    </span>
                    {found
                      ? <StatusChip status={found.status} />
                      : <Chip tone="stop">not in the queue</Chip>}
                  </div>
                  {found
                    ? <p className="mt-1 truncate text-sm text-slate-300">
                      <Value value={found.title} missing="no title" />
                    </p>
                    : null}
                </Row>
              );
            })}
          </List>}
      </Panel>
      <Panel title="Where to read more">
        <Fields>
          <Field label="Context it was given">
            <Strings values={task.context} empty="none" />
          </Field>
          <Field label="Brief" mono
            value={`$SIANA_HOME/briefs/${task.id}.md`} />
          <Field label="Report" mono
            value={`$SIANA_HOME/reports/${task.id}.md`}>
            <span className="font-mono text-xs break-all text-slate-200">
              {'$SIANA_HOME/reports/'}<Value value={task.id} />{'.md'}
            </span>
            <span className="mt-1 block text-xs text-slate-500">
              where a scout or QA task writes what it found. This console serves no
              file off disk, so read it at the helm.
            </span>
          </Field>
        </Fields>
      </Panel>
    </div>
  );
}

// -------------------------------------------------------------- what is owed

export function Obligations({ snapshot }) {
  const obligations = readStore(snapshot, 'obligations');
  // Oldest first, because an obligation's age is the thing that makes it urgent, and
  // the oldest open promise is the one most likely to have been forgotten.
  const records = oldestFirst(obligations.records, 'opened');
  const decisions = records.filter((o) => o.kind === 'decision');
  const promises = records.filter((o) => o.kind === 'promise');
  const other = records.filter((o) => o.kind !== 'decision' && o.kind !== 'promise');
  const group = (title, list, tone, empty) => (
    <Panel title={title} tone={tone}
      note={obligations.level === UNAVAILABLE ? null : `${list.length}`}>
      <Trouble source={obligations} what="What is owed" />
      {obligations.level !== UNAVAILABLE && !list.length
        ? <Empty>{empty}</Empty> : null}
      <List>
        {list.map((record) => (
          <Card key={record.id} id={record.id}>
            <p className="text-sm text-slate-100">
              <Value value={record.body} missing="no body" />
            </p>
            <p className="mt-1 flex flex-wrap gap-x-2 text-xs text-slate-500">
              <span>open <Age stamp={record.opened} suffix="" /></span>
              <span className="font-mono"><Value value={record.id} /></span>
              {record.task
                ? <a className="font-mono text-sky-300 underline"
                  href={`#/task/${encodeURIComponent(record.task)}`}>
                  <Value value={record.task} />
                </a>
                : null}
              {record.kind !== 'decision' && record.kind !== 'promise'
                ? <span className="text-amber-300">
                  kind <Value value={record.kind} missing="missing" />
                </span>
                : null}
            </p>
          </Card>
        ))}
      </List>
    </Panel>
  );
  return (
    <div className="space-y-4">
      {group('Open decisions', decisions, decisions.length ? 'attention' : 'plain',
             'Nothing is waiting on you.')}
      {group('Open promises', promises, 'plain', 'SIANA owes you nothing.')}
      {other.length
        ? group('Other', other, 'attention', 'none')
        : null}
    </div>
  );
}

// ------------------------------------------------------------- the decision log

export function Decisions({ snapshot }) {
  const decisions = readStore(snapshot, 'decisions');
  const records = newestFirst(decisions.records, 'at');
  return (
    <Panel title="Decisions" note={decisions.level === UNAVAILABLE ? null
      : `${records.length}`}>
      <Trouble source={decisions} what="The decision log" />
      {decisions.level !== UNAVAILABLE && !records.length
        ? <Empty>
          Nothing has been proposed or refused. The log is empty, which is the
          ordinary state before an advisory night.
        </Empty>
        : null}
      <List>
        {records.map((record) => (
          <Row key={record.id} to={`/decision/${encodeURIComponent(record.id)}`}>
            <div className="flex items-start justify-between gap-3">
              <span className="min-w-0 font-mono text-sm break-all text-slate-100">
                <Value value={record.action} missing="no action recorded" />
              </span>
              <Chip tone={record.verdict === 'proposed' ? 'warn' : 'stop'}>
                <Value value={record.verdict} missing="no verdict" />
              </Chip>
            </div>
            <p className="mt-1 flex flex-wrap gap-x-2 text-xs text-slate-500">
              <Age stamp={record.at} />
              <span className="font-mono"><Value value={record.class} /></span>
              {record.task
                ? <span className="font-mono"><Value value={record.task} /></span>
                : null}
            </p>
          </Row>
        ))}
      </List>
    </Panel>
  );
}

export function Decision({ snapshot, id }) {
  const decisions = readStore(snapshot, 'decisions');
  if (decisions.level === UNAVAILABLE) {
    return <Panel title="Decision">
      <Trouble source={decisions} what="The decision log" />
    </Panel>;
  }
  const record = decisions.records.find((d) => d.id === id);
  if (!record) {
    return <Missing what="Decision" id={id} source={decisions}
      of="The decision log" />;
  }
  return (
    <div className="space-y-4">
      <Panel title="Decision" note={
        <Chip tone={record.verdict === 'proposed' ? 'warn' : 'stop'}>
          <Value value={record.verdict} missing="no verdict" />
        </Chip>}>
        <Trouble source={decisions} what="The decision log" />
        <p className="font-mono text-sm break-all text-slate-50">
          <Value value={record.action} missing="no action recorded" />
        </p>
        <Fields>
          <Field label="What the gate said" value={record.reason}
            missing="nothing was recorded" />
          <Field label="When">
            <Age stamp={record.at} />
            <span className="ml-2 font-mono text-xs text-slate-500">
              <Value value={record.at} />
            </span>
          </Field>
          <Field label="Class" value={record.class} mono />
          <Field label="Task">
            {record.task
              ? <a href={`#/task/${encodeURIComponent(record.task)}`}
                className="font-mono text-sky-300 underline">
                <Value value={record.task} />
              </a>
              : <Value value={null} missing="not about a task" />}
          </Field>
          <Field label="Project" value={record.project} mono />
          <Field label="Confidence" value={record.confidence}
            missing="not recorded" />
          <Field label="Reversibility" value={record.reversibility}
            missing="not recorded" />
        </Fields>
      </Panel>
      <Panel title="Evidence">
        <Strings values={record.evidence} empty="none recorded" />
      </Panel>
      <Panel title="Alternatives it rejected">
        <Strings values={record.alternatives} empty="none recorded" />
      </Panel>
      <Panel title="Principles it cited">
        {Array.isArray(record.principles) && record.principles.length
          ? <ul className="space-y-2">
            {record.principles.map((line, i) => (
              <li key={i} className="border-l-2 border-slate-700 pl-3 text-sm
                text-slate-200">
                <Value value={line} />
              </li>
            ))}
          </ul>
          : <Empty>None recorded.</Empty>}
        <Fields>
          <Field label="Grant" value={record.grant} mono
            missing="decided with nothing in force" />
          <Field label="Policy" value={record.policy} mono
            missing="no principles hash recorded" />
        </Fields>
      </Panel>
    </div>
  );
}
