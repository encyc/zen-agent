/** Scripted `plugins` frames for the dev/e2e bridge: a mutable in-memory
 * registry that mirrors the host's wire contract (install, configure,
 * enable/disable, uninstall) without a real zen process. */

export interface PluginMockState {
  /** Installed plugin rows served for `plugins list`; the fake mutates them. */
  plugins?: Record<string, unknown>[]
  /** Overrides the built-in three-connector catalog. */
  pluginCatalog?: Record<string, unknown>[]
  /** Drives the "takes effect after this turn" banner. */
  pluginApplyPending?: boolean
}

const CATALOG: Record<string, unknown>[] = [
  {
    id: 'zen.dingtalk',
    name: '钉钉',
    description: '钉钉官方 MCP 连接器：消息与日程',
    requires_runtime: null,
  },
  {
    id: 'zen.feishu',
    name: '飞书',
    description: '飞书官方 MCP 连接器：IM、文档与日历',
    requires_runtime: 'Node.js 18+（npx）',
  },
  {
    id: 'zen.wecom',
    name: '企业微信',
    description: '企业微信官方 MCP 连接器：群机器人',
    requires_runtime: null,
  },
]

function credentialsFor(id: string): Record<string, unknown>[] {
  if (id === 'zen.feishu') {
    return [
      {
        key: 'APP_ID',
        label: 'App ID',
        secret: false,
        help: '开放平台应用的 App ID',
        url: 'https://open.feishu.cn',
        set: false,
      },
      { key: 'APP_SECRET', label: 'App Secret', secret: true, help: null, url: null, set: false },
    ]
  }
  if (id === 'demo.local') {
    return [{ key: 'DEMO_TEXT', label: 'Demo text', secret: true, help: null, url: null, set: false }]
  }
  return [
    { key: 'MCP_URL', label: 'MCP 服务 URL', secret: true, help: null, url: null, set: false },
  ]
}

function refresh(row: Record<string, unknown>) {
  const ready = (row.credentials as Record<string, unknown>[]).every((spec) => spec.set === true)
  if (row.enabled === false) {
    row.status = 'disabled'
    row.servers = [{ name: `${row.id}.main`, state: 'off', tools: [], error: null }]
    return
  }
  row.status = ready ? 'connected' : 'needs_credentials'
  row.servers = [
    {
      name: `${row.id}.main`,
      state: ready ? 'connected' : 'off',
      tools: ready ? ['echo'] : [],
      error: null,
    },
  ]
}

export function handlePlugins(
  state: PluginMockState,
  payload: Record<string, unknown>,
): Record<string, unknown> {
  const rows = () => state.plugins ?? []
  const installed = () => new Set(rows().map((row) => String(row.id)))
  const result = () => ({
    type: 'result',
    id: payload.id,
    ok: true,
    plugins: rows(),
    catalog: (state.pluginCatalog ?? CATALOG).map((entry) => ({
      ...entry,
      installed: installed().has(String(entry.id)),
    })),
    problem: null,
    apply_pending: state.pluginApplyPending ?? false,
  })

  if (payload.op === 'list') return result()

  if (payload.op === 'install') {
    const requested = typeof payload.plugin === 'string' ? payload.plugin : ''
    const path = typeof payload.path === 'string' ? payload.path : ''
    if (requested === '' && path === '') {
      return { type: 'error', id: payload.id, message: 'plugins install needs an id or path' }
    }
    const id = requested || 'demo.local'
    if (rows().some((row) => row.id === id)) return result()
    const entry = (state.pluginCatalog ?? CATALOG).find((item) => item.id === id)
    const row = {
      id,
      name: entry ? String(entry.name) : 'Demo plugin',
      version: '0.1.0',
      description: entry ? String(entry.description) : `从 ${path} 安装的本地插件`,
      enabled: true,
      status: 'needs_credentials',
      credentials: credentialsFor(id),
      servers: [],
    }
    state.plugins = [...rows(), row]
    refresh(row)
    return result()
  }

  const id = String(payload.plugin ?? '')
  const row = rows().find((item) => item.id === id)
  if (!row) return { type: 'error', id: payload.id, message: 'unknown plugin' }

  if (payload.op === 'configure') {
    const values = (payload.values ?? {}) as Record<string, string>
    row.credentials = (row.credentials as Record<string, unknown>[]).map((spec) => {
      const key = String(spec.key)
      if (!(key in values)) return spec
      return { ...spec, set: values[key] !== '' }
    })
    refresh(row)
    return result()
  }
  if (payload.op === 'enable' || payload.op === 'disable') {
    row.enabled = payload.op === 'enable'
    refresh(row)
    return result()
  }
  if (payload.op === 'uninstall') {
    state.plugins = rows().filter((item) => item.id !== id)
    return result()
  }
  return { type: 'error', id: payload.id, message: `unknown plugins op: ${String(payload.op)}` }
}
