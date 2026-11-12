import { test } from 'node:test'
import assert from 'node:assert/strict'
import { surfaceRunError } from '../src/state/runError.ts'
import { useNoticeStore } from '../src/state/stores.ts'

test('a run failure surfaces once while the status poll repeats', () => {
  useNoticeStore.setState({ notice: null })
  surfaceRunError(undefined)

  surfaceRunError('Cannot reach the model provider ｜ ConnectError: ConnectError(EndOfStream())')
  const first = useNoticeStore.getState().notice
  assert.equal(first?.kind, 'error')
  assert.match(first?.text ?? '', /ConnectError/)

  // The backend keeps last_error until the next run; the 2s poll must not
  // re-raise the banner the user dismissed.
  useNoticeStore.setState({ notice: null })
  surfaceRunError('Cannot reach the model provider ｜ ConnectError: ConnectError(EndOfStream())')
  assert.equal(useNoticeStore.getState().notice, null)

  // The next run clears last_error, then fails again: the banner returns.
  surfaceRunError(undefined)
  surfaceRunError('Cannot reach the model provider ｜ ConnectError: ConnectError(EndOfStream())')
  assert.ok(useNoticeStore.getState().notice)

  // ...and a later clean reading takes the stale banner down.
  surfaceRunError(undefined)
  assert.equal(useNoticeStore.getState().notice, null)
})

test('a blank reading re-arms the same failure for the next run', () => {
  useNoticeStore.setState({ notice: null })
  surfaceRunError(undefined)
  surfaceRunError('the run failed ｜ ConnectError: ')
  assert.ok(useNoticeStore.getState().notice)

  useNoticeStore.setState({ notice: null })
  surfaceRunError(undefined)
  surfaceRunError('the run failed ｜ ConnectError: ')
  assert.equal(useNoticeStore.getState().notice?.text, 'the run failed ｜ ConnectError:')
})
