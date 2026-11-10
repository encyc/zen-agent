/** Wire calls for the Plugins panel. Each resolves with the raw result or
 * error frame; the panel localizes failures, matching McpSettings. */

import type { PluginsResult } from '../rpc/types'
import { useAppStore } from './appStore'

function send(payload: Record<string, unknown>): Promise<PluginsResult> {
  return useAppStore.getState().client().send<PluginsResult>(payload)
}

export function listPlugins(): Promise<PluginsResult> {
  return send({ type: 'plugins', op: 'list' })
}

export function installPlugin(source: { id?: string; path?: string }): Promise<PluginsResult> {
  return send({ type: 'plugins', op: 'install', ...source })
}

export function uninstallPlugin(id: string): Promise<PluginsResult> {
  return send({ type: 'plugins', op: 'uninstall', plugin: id })
}

export function setPluginEnabled(id: string, enabled: boolean): Promise<PluginsResult> {
  return send({ type: 'plugins', op: enabled ? 'enable' : 'disable', plugin: id })
}

export function configurePlugin(
  id: string,
  values: Record<string, string>,
): Promise<PluginsResult> {
  return send({ type: 'plugins', op: 'configure', plugin: id, values })
}
