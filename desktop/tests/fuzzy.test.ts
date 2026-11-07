import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  compareMatches,
  fuzzyMatchCommand,
  fuzzyMatchPath,
  selectBestMatches,
} from '../src/state/fuzzy.ts'

test('an empty file query matches everything with no ranges', () => {
  assert.deepEqual(fuzzyMatchPath('', 'src/main.ts'), { score: 0, ranges: [] })
  assert.deepEqual(fuzzyMatchPath('', 'src', 'dir'), { score: 10, ranges: [] })
})

test('an exact file name scores highest and highlights the name', () => {
  const match = fuzzyMatchPath('main.ts', 'src/main.ts')
  assert.equal(match?.score, 100)
  assert.deepEqual(match?.ranges, [[4, 11]])
})

test('a file name prefix outranks a file name substring', () => {
  assert.equal(fuzzyMatchPath('mai', 'src/main.ts')?.score, 80)
  assert.deepEqual(fuzzyMatchPath('mai', 'src/main.ts')?.ranges, [[4, 7]])
  assert.equal(fuzzyMatchPath('ain', 'src/main.ts')?.score, 50)
  assert.deepEqual(fuzzyMatchPath('ain', 'src/main.ts')?.ranges, [[5, 8]])
})

test('a directory bonus raises every directory match by ten', () => {
  assert.equal(fuzzyMatchPath('src', 'src', 'dir')?.score, 100 + 10)
  assert.equal(fuzzyMatchPath('rc', 'src', 'dir')?.score, 50 + 10)
  assert.equal(fuzzyMatchPath('main.ts', 'src/main.ts', 'dir')?.score, 110)
})

test('a slash query only searches the full path', () => {
  const match = fuzzyMatchPath('src/main', 'src/main.ts')
  assert.equal(match?.score, 30)
  assert.deepEqual(match?.ranges, [[0, 8]])
})

test('a subsequence keeps forgiving matches at the bottom', () => {
  const match = fuzzyMatchPath('smt', 'src/main.ts')
  assert.equal(match?.score, 10)
  assert.deepEqual(match?.ranges, [
    [0, 1],
    [4, 5],
    [9, 10],
  ])
})

test('a path that contains nothing yields no match', () => {
  assert.equal(fuzzyMatchPath('zzz', 'src/main.ts'), null)
  assert.equal(fuzzyMatchPath('src/zzz', 'src/main.ts'), null)
})

test('command matching prefers prefixes over substrings', () => {
  assert.equal(fuzzyMatchCommand('co', 'compact')?.score, 100)
  assert.deepEqual(fuzzyMatchCommand('co', 'compact')?.ranges, [[0, 2]])
  assert.equal(fuzzyMatchCommand('pa', 'compact')?.score, 50)
  assert.deepEqual(fuzzyMatchCommand('pa', 'compact')?.ranges, [[3, 5]])
  assert.equal(fuzzyMatchCommand('cct', 'compact')?.score, 10)
  assert.equal(fuzzyMatchCommand('zzz', 'compact'), null)
  assert.deepEqual(fuzzyMatchCommand('', 'compact'), { score: 0, ranges: [] })
})

test('matches order by score, then shorter text, then lexically', () => {
  const rows = [
    { score: 50, text: 'b.ts' },
    { score: 80, text: 'a.ts' },
    { score: 80, text: 'c.ts' },
    { score: 80, text: 'aa.ts' },
  ]
  assert.deepEqual(
    rows.sort(compareMatches).map((row) => row.text),
    ['a.ts', 'c.ts', 'aa.ts', 'b.ts'],
  )
})

test('only the bounded best slice is returned in sorted order', () => {
  const candidates = Array.from({ length: 100 }, (_, index) => ({
    score: index,
    text: `file-${index}.ts`,
  }))
  const best = selectBestMatches(candidates, 3, (row) => row)
  assert.deepEqual(
    best.map((row) => row.score),
    [99, 98, 97],
  )
  assert.deepEqual(selectBestMatches(candidates, 0, (row) => row), [])
})
