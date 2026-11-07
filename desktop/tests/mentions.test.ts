import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  detectMention,
  formatMentionInsert,
  recognizedTokens,
} from '../src/state/mentions.ts'

test('a bare @ at the start opens file mode', () => {
  assert.deepEqual(detectMention('@', 1), {
    query: '',
    tokenStart: 0,
    tokenEnd: 1,
    quoted: false,
  })
})

test('@ opens after a space and keeps the typed query', () => {
  assert.deepEqual(detectMention('see @src/m', 10), {
    query: 'src/m',
    tokenStart: 4,
    tokenEnd: 10,
    quoted: false,
  })
})

test('an @ inside a word is mail, not a mention', () => {
  assert.equal(detectMention('mail me@host', 12), null)
  assert.equal(detectMention('foo@bar', 7), null)
})

test('the menu closes once the @ token is followed by whitespace', () => {
  assert.equal(detectMention('@src/main.ts ', 13), null)
  assert.equal(detectMention('@src/main.ts now', 16), null)
})

test('a quoted mention spans spaces until the cursor', () => {
  assert.deepEqual(detectMention('@"my file', 9), {
    query: 'my file',
    tokenStart: 0,
    tokenEnd: 9,
    quoted: true,
  })
})

test('a closed quoted mention no longer triggers', () => {
  assert.equal(detectMention('@"done" ', 8), null)
})

test('a quote delimiter before the @ does not block the mention', () => {
  assert.deepEqual(detectMention("a='@src", 7), {
    query: 'src',
    tokenStart: 3,
    tokenEnd: 7,
    quoted: false,
  })
})

test('file inserts end with a space, directories continue the path', () => {
  assert.equal(formatMentionInsert('src/main.ts', 'file'), '@src/main.ts ')
  assert.equal(formatMentionInsert('src/nested', 'dir'), '@src/nested/')
})

test('paths with spaces switch to the quoted form', () => {
  assert.equal(formatMentionInsert('docs/my file.md', 'file'), '@"docs/my file.md" ')
  assert.equal(formatMentionInsert('docs/my notes', 'dir'), '@"docs/my notes/')
})

const commands = [{ name: 'compact', description: '' }, { name: 'model', description: '' }]
const skills = [
  { name: 'review', description: '', enabled: true },
  { name: 'tidy', description: '', enabled: false },
]
const entries = [
  { path: 'src', kind: 'dir' as const },
  { path: 'src/nested', kind: 'dir' as const },
  { path: 'src/main.ts', kind: 'file' as const },
  { path: 'docs/guide.md', kind: 'file' as const },
  { path: 'docs/my notes.md', kind: 'file' as const },
]

test('a known command is boxed, an unknown one is not', () => {
  assert.deepEqual(recognizedTokens('/compact now', commands, skills, entries), [
    { start: 0, end: 8, kind: 'command' },
  ])
  assert.deepEqual(recognizedTokens('/com', commands, skills, entries), [])
})

test('an enabled skill is boxed under its full token', () => {
  assert.deepEqual(recognizedTokens('/skill:review src', commands, skills, entries), [
    { start: 0, end: 13, kind: 'skill' },
  ])
  assert.deepEqual(recognizedTokens('/skill:tidy', commands, skills, entries), [])
})

test('an indexed @path is boxed as its file or dir kind', () => {
  assert.deepEqual(recognizedTokens('see @src/main.ts now', commands, skills, entries), [
    { start: 4, end: 16, kind: 'file' },
  ])
  assert.deepEqual(recognizedTokens('@src/', commands, skills, entries), [
    { start: 0, end: 5, kind: 'dir' },
  ])
  assert.deepEqual(recognizedTokens('@src', commands, skills, entries), [
    { start: 0, end: 4, kind: 'dir' },
  ])
})

test('an unknown or partial @path stays plain text', () => {
  assert.deepEqual(recognizedTokens('@src/mai', commands, skills, entries), [])
  assert.deepEqual(recognizedTokens('a@src/main.ts', commands, skills, entries), [])
  assert.deepEqual(recognizedTokens('@docs/missing.md', commands, skills, entries), [])
})

test('a closed quoted path keeps its quotes inside the box', () => {
  assert.deepEqual(
    recognizedTokens('open @"docs/my notes.md" now', commands, skills, entries),
    [{ start: 5, end: 24, kind: 'file' }],
  )
})

test('every recognized token on a line is boxed in document order', () => {
  assert.deepEqual(
    recognizedTokens('/compact @src/main.ts and /skill:review', commands, skills, entries),
    [
      { start: 0, end: 8, kind: 'command' },
      { start: 9, end: 21, kind: 'file' },
      { start: 26, end: 39, kind: 'skill' },
    ],
  )
})
