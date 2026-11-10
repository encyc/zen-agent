import test from 'node:test'
import assert from 'node:assert/strict'
import { newlyInstalledId } from '../src/state/pluginInstall.ts'
import type { PluginRow, PluginsResult } from '../src/rpc/types.ts'

function row(
  id: string,
  status: PluginRow['status'] = 'ready',
): PluginRow {
  return {
    id,
    name: id,
    version: '0.1.0',
    description: '',
    enabled: true,
    status,
    credentials: [],
    servers: [],
  }
}

function frame(plugins: PluginRow[]): PluginsResult {
  return {
    type: 'result',
    id: 'p1',
    ok: true,
    plugins,
    catalog: [],
    problem: null,
    apply_pending: false,
  }
}

test('newlyInstalledId prefers the requested id', () => {
  const result = frame([row('zen.dingtalk', 'needs_credentials'), row('zen.feishu', 'ready')])
  assert.equal(newlyInstalledId(result, ['zen.dingtalk'], 'zen.feishu'), 'zen.feishu')
})

test('newlyInstalledId ignores older unconfigured plugins', () => {
  const result = frame([
    row('zen.dingtalk', 'needs_credentials'),
    row('zen.feishu', 'needs_credentials'),
  ])
  assert.equal(newlyInstalledId(result, ['zen.dingtalk'], undefined), 'zen.feishu')
})

test('newlyInstalledId is null when the frame adds nothing', () => {
  const result = frame([row('zen.dingtalk', 'ready')])
  assert.equal(newlyInstalledId(result, ['zen.dingtalk'], undefined), null)
})
