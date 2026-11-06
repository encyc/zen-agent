import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  clampFontScale,
  useNoticeStore,
  useNotificationStore,
  useSettingsStore,
} from '../src/state/stores.ts'

test('settings clamp the font scale', () => {
  assert.equal(clampFontScale(0.5), 0.9)
  assert.equal(clampFontScale(1.05), 1.05)
  assert.equal(clampFontScale(2), 1.2)
})

test('settings carry pi defaults and the shell view', () => {
  const settings = useSettingsStore.getState()
  assert.equal(settings.fontScale, 1)
  assert.equal(settings.sessionTime, true)
  assert.equal(settings.view, 'chat')
  settings.setView('tools')
  assert.equal(useSettingsStore.getState().view, 'tools')
  useSettingsStore.getState().setView('chat')
})

test('showing a notice records a notification', () => {
  useNotificationStore.setState({ items: [], unread: 0 })
  useNoticeStore.setState({ notice: null })
  useNoticeStore.getState().show({ kind: 'error', text: 'boom' })
  assert.equal(useNotificationStore.getState().items[0].text, 'boom')
  assert.equal(useNotificationStore.getState().unread, 1)
  useNotificationStore.getState().markRead()
  assert.equal(useNotificationStore.getState().unread, 0)
  useNotificationStore.getState().clear()
  assert.equal(useNotificationStore.getState().items.length, 0)
})
