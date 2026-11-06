/** Todo dock contract: phases flatten in order and the collapsed summary
 * agrees with the list about what counts as done. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { flattenTodos, todoProgress } from '../src/ui/composer/todos.ts'

const t = (key: string, params?: Record<string, string | number>) =>
  `${key}:${params?.completed ?? ''}/${params?.total ?? ''}`

test('tasks flatten in phase order and keep their phase name', () => {
  const tasks = flattenTodos([
    {
      name: '调研',
      tasks: [
        { content: '读代码', status: 'completed' },
        { content: '写方案', status: 'in_progress' },
      ],
    },
    {
      name: '实现',
      tasks: [{ content: '改 Dock', status: 'pending' }],
    },
  ])
  assert.deepEqual(tasks, [
    { content: '读代码', status: 'completed', blocker: '', phase: '调研' },
    { content: '写方案', status: 'in_progress', blocker: '', phase: '调研' },
    { content: '改 Dock', status: 'pending', blocker: '', phase: '实现' },
  ])
})

test('malformed phases and tasks are dropped, unknown statuses fall back', () => {
  const tasks = flattenTodos([
    { name: '  ', tasks: [{ content: '无主任务', status: 'pending' }] },
    { name: 'Tasks', tasks: [{ content: '', status: 'pending' }, null, 'x'] },
    {
      name: ' Tasks ',
      tasks: [{ content: '保留', status: 'mystery' }],
    },
    { name: 'No tasks' },
  ])
  assert.deepEqual(
    tasks.map((task) => [task.content, task.status, task.phase]),
    [
      ['保留', 'pending', 'Tasks'],
    ],
  )
})

test('progress counts completed tasks and keeps abandoned out of the total', () => {
  const tasks = flattenTodos([
    {
      name: 'Tasks',
      tasks: [
        { content: 'a', status: 'completed' },
        { content: 'b', status: 'in_progress' },
        { content: 'c', status: 'pending' },
        { content: 'd', status: 'abandoned' },
      ],
    },
  ])
  const progress = todoProgress(tasks, t)
  assert.equal(progress.label, 'chat.todo.progress:1/3')
  assert.equal(progress.symbol, '\u25d0')
  assert.equal(progress.current?.content, 'b')
})

test('the blocked state stays open and carries its reason', () => {
  const tasks = flattenTodos([
    {
      name: 'Tasks',
      tasks: [{ content: '等 CI', status: 'blocked', blocker: '  等待审批  ' }],
    },
  ])
  const progress = todoProgress(tasks, t)
  assert.equal(tasks[0]?.blocker, '等待审批')
  assert.equal(tasks[0]?.status, 'blocked')
  assert.equal(progress.label, 'chat.todo.progress:0/1')
  assert.equal(progress.symbol, '\u25d0')
  assert.equal(progress.current, undefined)
})

test('a finished list switches to the completed symbol', () => {
  const tasks = flattenTodos([
    {
      name: 'Tasks',
      tasks: [
        { content: 'a', status: 'completed' },
        { content: 'b', status: 'completed' },
      ],
    },
  ])
  const progress = todoProgress(tasks, t)
  assert.equal(progress.label, 'chat.todo.completed:2/2')
  assert.equal(progress.symbol, '\u2713')
})

test('an entirely abandoned list reports the cancelled state', () => {
  const tasks = flattenTodos([
    {
      name: 'Tasks',
      tasks: [
        { content: 'a', status: 'abandoned' },
        { content: 'b', status: 'abandoned' },
      ],
    },
  ])
  const progress = todoProgress(tasks, t)
  assert.equal(progress.label, 'chat.todo.status.abandoned:/')
  assert.equal(progress.symbol, '\u2298')
})

test('no tasks means no dock', () => {
  assert.deepEqual(flattenTodos(undefined), [])
  assert.deepEqual(flattenTodos([]), [])
})
