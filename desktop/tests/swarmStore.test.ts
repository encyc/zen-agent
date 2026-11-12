/** The swarm store: global frames land in the room without project/session
 * filtering, and every command is one `swarm` request over the shared client. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { _useSwarmSender, swarmActiveCount, swarmLoadPlan, swarmNoticeText, useSwarmStore } from '../src/state/swarmStore.ts'
import type {
  SwarmAgentFrame,
  SwarmAgentState,
  SwarmMessage,
  SwarmMessageFrame,
  SwarmNoticeFrame,
  SwarmRole,
  SwarmTask,
  SwarmTaskFrame,
} from '../src/rpc/types.ts'

function messageFrame(id: string, text: string): SwarmMessageFrame {
  return {
    type: 'swarm_message',
    swarm: true,
    id,
    ts: 1,
    author: { kind: 'agent', id: 'research' },
    text,
    mentions: [],
    taskId: null,
    chainDepth: 1,
  }
}

function noticeFrame(text: string): SwarmNoticeFrame {
  return { type: 'swarm_notice', agent: 'research', text }
}

function taskFrame(id: string, state: SwarmTask['state']): SwarmTaskFrame {
  return {
    type: 'swarm_task',
    swarm: true,
    id,
    assignee: 'research',
    state,
    sourceMessageId: 'm_0',
    createdBy: { kind: 'user', id: 'you' },
    chainId: 'c_1',
    chainDepth: 1,
    chainCost: 0,
    createdAt: 1,
    updatedAt: 2,
    resultMessageId: null,
    reason: null,
    derivedFrom: null,
  }
}

function agentFrame(state: SwarmAgentState['state']): SwarmAgentFrame {
  return {
    type: 'swarm_agent',
    swarm: true,
    agent: 'research',
    name: 'Research',
    state,
    task: null,
    pendingApply: false,
  }
}

function reset(): void {
  useSwarmStore.setState({
    messages: [] as SwarmMessage[],
    tasks: [] as SwarmTask[],
    agents: [] as SwarmAgentState[],
    roles: [] as SwarmRole[],
    warnings: [] as string[],
    loaded: false,
    selectedTask: null,
    loadedProject: null,
    runRows: {},
  })
}

function listAnswer(tag = 'a') {
  return {
    type: 'result',
    id: '1',
    ok: true,
    roles: [],
    agents: [],
    tasks: [],
    messages: [messageFrame(`m_${tag}`, tag)],
    warnings: [],
  }
}

function fakeClient(script: (request: Record<string, unknown>) => unknown) {
  const sent: Record<string, unknown>[] = []
  _useSwarmSender({
    async send(request) {
      sent.push(request)
      return script(request) as Record<string, unknown>
    },
  })
  return sent
}

test('applyFrame appends a message', () => {
  reset()
  useSwarmStore.getState().applyFrame(messageFrame('m_1', 'hello'))

  const { messages } = useSwarmStore.getState()
  assert.equal(messages.length, 1)
  assert.equal(messages[0].text, 'hello')
  assert.equal(messages[0].author.id, 'research')
})

test('applyFrame replaces a task by id', () => {
  reset()
  useSwarmStore.getState().applyFrame(taskFrame('t_1', 'queued'))
  useSwarmStore.getState().applyFrame(taskFrame('t_1', 'running'))

  const { tasks } = useSwarmStore.getState()
  assert.equal(tasks.length, 1)
  assert.equal(tasks[0].state, 'running')
})

test('applyFrame upserts one agent row', () => {
  reset()
  useSwarmStore.getState().applyFrame(agentFrame('idle'))
  useSwarmStore.getState().applyFrame(agentFrame('running'))

  const { agents } = useSwarmStore.getState()
  assert.equal(agents.length, 1)
  assert.equal(agents[0].state, 'running')
})

test('applying a duplicate message id is a no-op', () => {
  reset()
  useSwarmStore.getState().applyFrame(messageFrame('m_1', 'hello'))
  useSwarmStore.getState().applyFrame(messageFrame('m_1', 'hello'))

  assert.equal(useSwarmStore.getState().messages.length, 1)
})

test('post sends a swarm request', async () => {
  reset()
  const sent = fakeClient(() => ({ type: 'result', id: '7', ok: true, tasks: [] }))

  await useSwarmStore.getState().post('@research hi')

  assert.deepEqual(sent, [{ type: 'swarm', op: 'post', text: '@research hi' }])
})

test('post forwards the ref and images only when given', async () => {
  reset()
  const calls: Record<string, unknown>[] = []
  _useSwarmSender({
    send: async (request) => {
      calls.push(request)
      return { type: 'result', id: 'r1', ok: true, tasks: [] }
    },
  })

  await useSwarmStore.getState().post('@research hi')
  await useSwarmStore.getState().post('@research hi', { ref: 'm1', images: ['/tmp/a.png'] })

  assert.deepEqual(calls[0], { type: 'swarm', op: 'post', text: '@research hi' })
  assert.deepEqual(calls[1], {
    type: 'swarm',
    op: 'post',
    text: '@research hi',
    ref: 'm1',
    images: ['/tmp/a.png'],
  })
})

test('load seeds the room from the list answer', async () => {
  reset()
  fakeClient(() => ({
    type: 'result',
    id: '8',
    ok: true,
    roles: [{ id: 'research', name: 'Research' }],
    agents: [{ agent: 'research', name: 'Research', state: 'idle', task: null, pendingApply: false }],
    tasks: [taskFrame('t_1', 'done')],
    messages: [messageFrame('m_1', 'hi')],
    warnings: [],
  }))

  await useSwarmStore.getState().load(null)

  const state = useSwarmStore.getState()
  assert.equal(state.loaded, true)
  assert.equal(state.roles[0].id, 'research')
  assert.equal(state.agents.length, 1)
  assert.equal(state.tasks[0].id, 't_1')
  assert.equal(state.messages[0].id, 'm_1')
})

test('cancel retry and resetAgent send their task and agent ids', async () => {
  reset()
  const sent = fakeClient(() => ({ type: 'result', id: '9', ok: true }))

  await useSwarmStore.getState().cancel('t_1')
  await useSwarmStore.getState().retry('t_1')
  await useSwarmStore.getState().resetAgent('research')

  assert.deepEqual(sent, [
    { type: 'swarm', op: 'cancel', taskId: 't_1' },
    { type: 'swarm', op: 'retry', taskId: 't_1' },
    { type: 'swarm', op: 'reset', agentId: 'research' },
  ])
})

test('reset clears the room and marks it unloaded', () => {
  reset()
  useSwarmStore.setState({
    messages: [messageFrame('m_1', 'hi')],
    tasks: [taskFrame('t_1', 'running')],
    agents: [agentFrame('running')],
    roles: [{ id: 'research' } as SwarmRole],
    warnings: ['broken role'],
    runRows: { t_1: [{ kind: 'assistant', id: 'v1', text: 'x' } as never] },
    loaded: true,
  })

  useSwarmStore.getState().reset()

  const state = useSwarmStore.getState()
  assert.deepEqual(state.messages, [])
  assert.deepEqual(state.tasks, [])
  assert.deepEqual(state.agents, [])
  assert.deepEqual(state.roles, [])
  assert.deepEqual(state.warnings, [])
  assert.deepEqual(state.runRows, {})
  assert.equal(state.loaded, false)
})

test('libraryRoles lists the global role library', async () => {
  reset()
  const sent = fakeClient(() => ({
    type: 'result',
    id: '12',
    ok: true,
    roles: [
      { id: 'research', name: 'Research' },
      { id: 'builder', name: 'Builder' },
    ],
    warnings: [],
  }))

  const roles = await useSwarmStore.getState().libraryRoles()

  assert.deepEqual(sent, [{ type: 'swarm', op: 'roles', action: 'library' }])
  assert.deepEqual(
    roles.map((role) => role.id),
    ['research', 'builder'],
  )
})

test('copyRole copies a library role then reloads the room', async () => {
  reset()
  const sent = fakeClient((request) =>
    request.action === 'copy'
      ? { type: 'result', id: '13', ok: true, roles: [], warnings: [] }
      : {
          type: 'result',
          id: '14',
          ok: true,
          roles: [{ id: 'research', name: 'Research' }],
          agents: [],
          tasks: [],
          messages: [],
          warnings: [],
        },
  )

  const answer = await useSwarmStore.getState().copyRole('research')

  assert.deepEqual(sent, [
    { type: 'swarm', op: 'roles', action: 'copy', roleId: 'research' },
    { type: 'swarm', op: 'list' },
  ])
  assert.equal(answer.ok, true)
  assert.deepEqual(
    useSwarmStore.getState().roles.map((role) => role.id),
    ['research'],
  )
})

test('copyRole surfaces a refusal without reloading', async () => {
  reset()
  const sent = fakeClient(() => ({ ok: false, message: 'role research already exists in this project' }))

  const answer = await useSwarmStore.getState().copyRole('research')

  assert.equal(answer.ok, false)
  assert.equal(answer.message, 'role research already exists in this project')
  assert.equal(sent.length, 1, 'a refused copy does not reload')
})

test('load and reload surface manager warnings', async () => {
  reset()
  const sent = fakeClient((request) =>
    request.op === 'reload'
      ? {
          type: 'result',
          id: '11',
          ok: true,
          roles: [],
          notes: [],
          warnings: ['role research: bad manifest'],
        }
      : {
          type: 'result',
          id: '10',
          ok: true,
          roles: [],
          agents: [],
          tasks: [],
          messages: [],
          warnings: ['role research: bad manifest'],
        },
  )

  await useSwarmStore.getState().load(null)
  assert.deepEqual(useSwarmStore.getState().warnings, ['role research: bad manifest'])

  await useSwarmStore.getState().reload()
  assert.deepEqual(useSwarmStore.getState().warnings, ['role research: bad manifest'])
  assert.deepEqual(sent.at(-1), { type: 'swarm', op: 'reload' })
})

test('only a swarm notice produces a user notification', () => {
  assert.equal(swarmNoticeText(noticeFrame('@research: @you done')), '@research: @you done')
  assert.equal(swarmNoticeText(messageFrame('m_1', '@you done')), null)
  assert.equal(swarmNoticeText(noticeFrame('')), null)
})

test('swarm_event frames fold into per-task run rows with a hard cap', async () => {
  useSwarmStore.setState({ runRows: {} })
  const toolStart = (n: number) => ({
    type: 'swarm_event',
    swarm: true,
    agent: 'research',
    task: 't_1',
    event: { type: 'tool_execution_start', tool_call_id: `c${n}`, tool_name: 'bash' },
  })
  for (let i = 0; i < 1505; i += 1) {
    useSwarmStore.getState().applyFrame(toolStart(i) as never)
  }
  const rows = useSwarmStore.getState().runRows['t_1']
  assert.equal(rows.length, 1500, 'run rows are capped')
  assert.equal((rows[rows.length - 1] as { call_id: string }).call_id, 'c1504', 'newest kept')

  useSwarmStore.getState().clearRun('t_1')
  assert.equal(useSwarmStore.getState().runRows['t_1'], undefined)
})

test('two tasks keep separate run rows', () => {
  useSwarmStore.setState({ runRows: {} })
  const frame = (task: string) => ({
    type: 'swarm_event', swarm: true, agent: 'a', task,
    event: { type: 'tool_execution_start', tool_call_id: 'c1', tool_name: 'bash' },
  })
  useSwarmStore.getState().applyFrame(frame('t_1') as never)
  useSwarmStore.getState().applyFrame(frame('t_2') as never)
  assert.equal(useSwarmStore.getState().runRows['t_1'].length, 1)
  assert.equal(useSwarmStore.getState().runRows['t_2'].length, 1)
})

test('a terminal task frame releases its live rows even with no panel open', () => {
  useSwarmStore.setState({ runRows: {} })
  useSwarmStore.getState().applyFrame({
    type: 'swarm_event', swarm: true, agent: 'research', task: 't_1',
    event: { type: 'tool_execution_start', tool_call_id: 'c1', tool_name: 'bash' },
  } as never)
  useSwarmStore.getState().applyFrame(taskFrame('t_1', 'running'))
  assert.equal(useSwarmStore.getState().runRows['t_1'].length, 1, 'live rows survive a running transition')
  useSwarmStore.getState().applyFrame(taskFrame('t_1', 'done'))
  assert.equal(useSwarmStore.getState().runRows['t_1'], undefined, 'settling releases the rows')
})

test('selectTask toggles the selection', () => {
  reset()
  useSwarmStore.getState().selectTask('t_1')
  assert.equal(useSwarmStore.getState().selectedTask, 't_1')
  useSwarmStore.getState().selectTask('t_1')
  assert.equal(useSwarmStore.getState().selectedTask, null)
  useSwarmStore.getState().selectTask('t_1')
  useSwarmStore.getState().selectTask('t_2')
  assert.equal(useSwarmStore.getState().selectedTask, 't_2')
})

test('load records the requesting project and sends it on the wire', async () => {
  reset()
  const sent = fakeClient(() => listAnswer())
  await useSwarmStore.getState().load('/work/a')
  assert.equal(useSwarmStore.getState().loadedProject, '/work/a')
  assert.deepEqual(sent[0], { type: 'swarm', op: 'list', project: '/work/a' })
  await useSwarmStore.getState().load(null)
  assert.equal(useSwarmStore.getState().loadedProject, null)
  assert.deepEqual(sent[1], { type: 'swarm', op: 'list' })
})

test('a stale load answer is dropped', async () => {
  reset()
  let resolveA!: (value: unknown) => void
  fakeClient((request) =>
    request.project === '/a'
      ? new Promise((resolve) => { resolveA = resolve })
      : listAnswer('b'),
  )
  const a = useSwarmStore.getState().load('/a')
  const b = useSwarmStore.getState().load('/b')
  await b
  resolveA(listAnswer('a'))
  await a
  const state = useSwarmStore.getState()
  assert.equal(state.loadedProject, '/b')
  assert.equal(state.messages[0].id, 'm_b')
})

test('load keeps an open task selection (save refresh)', async () => {
  reset()
  fakeClient(() => listAnswer())
  useSwarmStore.getState().selectTask('t_1')
  await useSwarmStore.getState().load('/work/a')
  assert.equal(useSwarmStore.getState().selectedTask, 't_1')
})

test('clearRoomUi drops selection and live rows but keeps room data', async () => {
  reset()
  fakeClient(() => listAnswer())
  await useSwarmStore.getState().load('/work/a')
  useSwarmStore.getState().selectTask('t_1')
  useSwarmStore.getState().applyFrame({
    type: 'swarm_event', swarm: true, agent: 'research', task: 't_1',
    event: { type: 'tool_execution_start', tool_call_id: 'c1', tool_name: 'bash' },
  } as never)
  useSwarmStore.getState().clearRoomUi()
  const state = useSwarmStore.getState()
  assert.equal(state.selectedTask, null)
  assert.deepEqual(state.runRows, {})
  assert.equal(state.loaded, true)
  assert.equal(state.messages.length, 1)
})

test('swarmLoadPlan: boot loads, same project stays, different switches', () => {
  assert.equal(swarmLoadPlan(false, null, null), 'load')
  assert.equal(swarmLoadPlan(false, null, '/a'), 'load')
  assert.equal(swarmLoadPlan(true, '/a', '/a'), 'none')
  assert.equal(swarmLoadPlan(true, null, '/a'), 'switch')
  assert.equal(swarmLoadPlan(true, '/a', '/b'), 'switch')
})

test('swarmActiveCount only counts live tasks once loaded', () => {
  const tasks = [taskFrame('t_1', 'running'), taskFrame('t_2', 'queued'), taskFrame('t_3', 'done')]
  assert.equal(swarmActiveCount(false, tasks), 0)
  assert.equal(swarmActiveCount(true, tasks), 2)
  assert.equal(swarmActiveCount(true, []), 0)
})
