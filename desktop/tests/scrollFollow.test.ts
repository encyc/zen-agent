/** How the transcript decides to follow the tail: the mode, the jump pill and
 * the unseen count as a pure function of one scroll observation. */

import assert from 'node:assert/strict'
import { test } from 'node:test'
import { countUnseen, nextScrollSnapshot, type ScrollSnapshot } from '../src/state/scrollFollow.ts'

const following = (): ScrollSnapshot => ({ mode: 'following', showJump: false, atTop: false, unseen: 0 })
const input = (over: Partial<Parameters<typeof nextScrollSnapshot>[1]>) => ({
  scrollTop: 0,
  scrollHeight: 1000,
  clientHeight: 400,
  lastScrollTop: 0,
  programmatic: false,
  ...over,
})

test('at the bottom the mode is following and the jump pill is hidden', () => {
  const next = nextScrollSnapshot(following(), input({ scrollTop: 600, lastScrollTop: 600 }))
  assert.equal(next.mode, 'following')
  assert.equal(next.showJump, false)
  assert.equal(next.unseen, 0)
})

test('scrolling up past the edge releases follow and reveals the pill', () => {
  const released = nextScrollSnapshot(following(), input({ scrollTop: 300, lastScrollTop: 420 }))
  assert.equal(released.mode, 'free')
  assert.equal(released.showJump, true) // distance 300 > 160
})

test('a programmatic scroll never changes the mode', () => {
  // 跳到底部 / 点 minimap 时组件自己改 scrollTop，它触发的事件不得把 following 打成 free
  const next = nextScrollSnapshot(following(), input({ scrollTop: 100, lastScrollTop: 600, programmatic: true }))
  assert.equal(next.mode, 'following')
})

test('content growth alone does not release follow', () => {
  // scrollHeight 变大、scrollTop 未变：distance 变大了，但这不是用户往上滚
  const next = nextScrollSnapshot(following(), input({ scrollTop: 600, scrollHeight: 2000, lastScrollTop: 600 }))
  assert.equal(next.mode, 'following')
})

test('returning to the bottom clears the pill and the unseen count', () => {
  const free: ScrollSnapshot = { mode: 'free', showJump: true, atTop: false, unseen: 3 }
  const back = nextScrollSnapshot(free, input({ scrollTop: 600, scrollHeight: 1000, lastScrollTop: 590 }))
  assert.equal(back.mode, 'following')
  assert.equal(back.showJump, false)
  assert.equal(back.unseen, 0)
})

test('atTop is true only inside the edge band', () => {
  assert.equal(nextScrollSnapshot(following(), input({ scrollTop: 4 })).atTop, true)
  assert.equal(nextScrollSnapshot(following(), input({ scrollTop: 40, lastScrollTop: 40 })).atTop, false)
})

test('between the two thresholds the pill keeps its previous visibility', () => {
  const free: ScrollSnapshot = { mode: 'free', showJump: true, atTop: false, unseen: 1 }
  const next = nextScrollSnapshot(
    free,
    input({ scrollTop: 480, scrollHeight: 1000, clientHeight: 400, lastScrollTop: 500 }),
  )
  assert.equal(next.mode, 'free') // distance 120 < 160
  assert.equal(next.showJump, true) // 不改，避免 160 附近抖动
})

test('countUnseen counts user rows added after the last seen id', () => {
  const nodes = [
    { kind: 'user', id: 'u1' },
    { kind: 'assistant', id: 'a1' },
    { kind: 'user', id: 'u2' },
    { kind: 'assistant', id: 'a2' },
    { kind: 'user', id: 'u3' },
  ]
  assert.equal(countUnseen(nodes, new Set(['u1'])), 2)
  assert.equal(countUnseen(nodes, new Set(['u1', 'u2', 'u3'])), 0)
  // 窗口化裁掉旧行后仍按 id 计：u1 已不在数组里，u2/u3 就不是"新的"
  assert.equal(countUnseen(nodes.slice(2), new Set(['u1', 'u2', 'u3'])), 0)
})
