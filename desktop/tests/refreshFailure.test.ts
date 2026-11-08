import test from 'node:test'
import assert from 'node:assert/strict'
import { reportRefreshFailure } from '../src/state/refresh.ts'
import { useNoticeStore, useNotificationStore, useSettingsStore } from '../src/state/stores.ts'

test('a failed background refresh surfaces a locale-keyed notice', () => {
  useNoticeStore.setState({ notice: null })
  useNotificationStore.setState({ items: [] })
  useSettingsStore.setState({ locale: 'en' })

  reportRefreshFailure('sessions', new Error('wire died'))

  const notice = useNoticeStore.getState().notice
  assert.ok(notice, 'no notice shown')
  assert.equal(notice.kind, 'error')
  assert.equal(notice.text, 'sessions refresh failed: wire died')
  // and it lands in the notification log, not just the one-shot banner
  assert.ok(useNotificationStore.getState().items.length > 0)
})

test('the notice follows the configured locale', () => {
  useNoticeStore.setState({ notice: null })
  useNotificationStore.setState({ items: [] })
  useSettingsStore.setState({ locale: 'zh' })

  reportRefreshFailure('models', new Error('x'))

  const notice = useNoticeStore.getState().notice
  assert.ok(notice)
  assert.equal(notice.text, 'models 刷新失败：x')
})
