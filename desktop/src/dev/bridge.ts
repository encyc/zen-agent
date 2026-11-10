/** Scripted Tauri bridge for the e2e harness and the dev-browser shim.
 * Installs `window.__TAURI_INTERNALS__` (the surface `invoke`/`listen` read),
 * so the real React shell boots with scripted RPC answers and no `zen` binary
 * or Rust side. Kept free of Node/Playwright imports: `main.tsx` loads it
 * dynamically under `import.meta.env.DEV`; Playwright loads it via harness. */

import { handlePlugins, type PluginMockState } from './pluginsMock'
type Lane = 'queue' | 'steer'

interface PendingItemMock {
  id: number
  lane: Lane
  text: string
  images: number
  created_at: number
}

export interface MemoryEntryMock {
  id: string
  title: string
  body: string
  created: string
  updated: string
  source: string
}

export interface BridgeState extends PluginMockState {
  running: boolean
  pending: PendingItemMock[]
  calls: Record<string, unknown>[]
  starts: { cwd?: string | null; pid?: number }[]
  dialogSelection: string | string[] | null
  title: string
  failExport?: boolean
  noCurrent?: boolean
  projectSessions?: number
  transcript?: Record<string, unknown>[]
  subagents?: Record<string, unknown>[]
  subagentTranscripts?: Record<string, unknown[]>
  todos?: Record<string, unknown>[]
  tools?: { name: string; description: string; approval: string }[]
  thinking?: string | null
  models?: Record<string, unknown>[]
  secretsStored?: string[]
  secretsModel?: Record<string, unknown>[]
  secretsSearch?: Record<string, unknown>[]
  secretTests?: Record<string, { connected: boolean; message: string }>
  skills?: Record<string, unknown>[]
  skillsDisabled?: string[]
  /** Knob values served for `limits get`, keyed by `table.name`; absent → default. */
  limitValues?: Record<string, number | null>
  /** Keys the fake considers written to the file (drives the "explicit" chip). */
  limitExplicit?: string[]
  /** How many times the shell asked the (faked) window plugin to show it. */
  showCalls?: number
  /** When set, `limits set` answers this error (hand-mangled file, bad value). */
  limitSetError?: string
  skillBodies?: Record<string, string>
  mcpServers?: Record<string, unknown>[]
  mcpProblem?: string
  mcpTests?: Record<string, { connected: boolean; tools?: string[]; message?: string }>
  sessionProjects?: string[]
  relocations?: { from: string; to: string }[]
  relocateError?: string
  fsEntries?: Record<string, Record<string, unknown>[]>
  fsIndex?: { path: string; kind: 'dir' | 'file' }[]
  fsFiles?: Record<string, Record<string, unknown>>
  memory?: MemoryEntryMock[]
  worktrees?: Record<string, unknown>
  /** The dispatch board payload served for `taiji board`. */
  taiji?: Record<string, unknown>
  /** When set, `taiji merge` answers this error message. */
  taijiMergeError?: string
  /** The transcript tail served for `taiji peek`. */
  taijiPeek?: { role: string; text: string }[]
  /** Action ops (message/merge/kill/peek) the panel sent, most recent last. */
  taijiRequests?: { op: string; name?: string; text?: string }[]
  /** When set, the session list carries the project's taiji singleton. */
  taijiSession?: boolean
  statusSession?: string
  /** The runtime mode the fake status answers with (default zen). */
  statusMode?: string
  /** Milliseconds `rpc_start` stays in flight, so a spec can watch a project
   * switch happen (the shell must never fall back to the boot screen). */
  switchDelayMs?: number
  /** When set, `rpc_start` never resolves: the shell stays before the ready
   * tree, whose boot fail-safe must still reveal the hidden window. */
  startHangs?: boolean
  /** `get_initial_state` flags; absent → updates enabled and self-updatable. */
  updatesDisabled?: boolean
  selfUpdatable?: boolean
}

declare global {
  interface Window {
    /** What `isTauri()` reads; the boot reveal is Tauri-only. */
    isTauri?: boolean
    /** The IPC surface `invoke`/`listen` call; the real shell or this mock. */
    __TAURI_INTERNALS__?: unknown
    __ZEN_E2E__: BridgeState
    __ZEN_E2E_EMIT__(event: string, payload: unknown): void
  }
}

export function installBridge(state: BridgeState) {
  const callbacks = new Map<number, (payload: unknown) => void>()
  const listeners = new Map<string, ((payload: unknown) => void)[]>()
  let nextCallback = 1
  let nextEvent = 1
  let nextPid = 4242

  const relocatedPath = (from: string) => {
    const moves = (state.relocations ?? []).filter((move) => move.from === from)
    return moves.length > 0 ? moves[moves.length - 1].to : from
  }

  /** The fake tracks the current session so a resume flips the list. */
  let currentMode = state.statusMode ?? 'zen'
  let currentSessionPath = state.noCurrent === true ? '' : '/sessions/a.jsonl'

  const sessions = (id: unknown, scope: unknown) => ({
    type: 'result',
    id,
    ok: true,
    scope: scope === 'all' ? 'all' : 'project',
    sessions: [
      {
        path: '/sessions/a.jsonl',
        name: 'a',
        title: state.title,
        project: 'p',
        project_path: relocatedPath('/work/p'),
        messages: 4,
        current: currentSessionPath === '/sessions/a.jsonl',
        pinned: false,
        created_at: 1,
        updated_at: 2,
        model: 'deepseek-flash',
        derived_from: '',
      },
      {
        path: '/sessions/b.jsonl',
        name: 'b',
        title: '另一个会话',
        project: 'p',
        project_path: relocatedPath('/work/p'),
        messages: 2,
        current: currentSessionPath === '/sessions/b.jsonl',
        pinned: false,
        created_at: 1,
        updated_at: 1,
        model: 'deepseek-flash',
        derived_from: '',
      },
      ...(state.taijiSession === true
        ? [
            {
              path: '/sessions/taiji.jsonl',
              name: 'taiji',
              title: '太极',
              project: 'p',
              project_path: relocatedPath('/work/p'),
              messages: 0,
              current: currentSessionPath === '/sessions/taiji.jsonl',
              pinned: false,
              created_at: 1,
              updated_at: 1,
              model: 'deepseek-flash',
              derived_from: '',
            },
          ]
        : []),
      ...Array.from({ length: Math.max(0, (state.projectSessions ?? 2) - 2) }, (_, index) => {
        const ordinal = index + 3
        return {
          path: `/sessions/p${ordinal}.jsonl`,
          name: `p${ordinal}`,
          title: `额外会话 ${ordinal}`,
          project: 'p',
          messages: 1,
          current: false,
          pinned: false,
          created_at: 1,
          updated_at: 1,
          model: 'deepseek-flash',
          derived_from: '',
        }
      }),
      ...(scope === 'all'
        ? [
            {
              path: '/sessions/c.jsonl',
              name: 'c',
              title: '另一个项目',
              project: 'q',
              project_path: '/work/q',
              messages: 1,
              current: false,
              pinned: false,
              created_at: 1,
              updated_at: 1,
              model: 'deepseek-flash',
              derived_from: '',
            },
            ...(state.sessionProjects ?? []).map((root, index) => ({
              path: `/sessions/extra${index}.jsonl`,
              name: `extra${index}`,
              title: `会话项目 ${root}`,
              project: root.split('/').filter(Boolean).at(-1) ?? 'extra',
              project_path: root,
              messages: 1,
              current: false,
              pinned: false,
              created_at: 1,
              updated_at: 3,
              model: 'deepseek-flash',
              derived_from: '',
            })),
          ]
        : []),
    ],
  })

  const respond = (payload: Record<string, unknown>) => {
    switch (payload.type) {
      case 'hello':
        return { type: 'result', id: payload.id, ok: true, protocol_version: 2 }
      case 'status':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          running: state.running,
          messages: 2,
          model: 'deepseek-flash',
          usage: { input: 0, output: 0, cache_read: 0, cache_write: 0, cost: 0 },
          termination: null,
          mode: currentMode,
          session: state.statusSession,
          thinking: state.thinking ?? 'high',
          pending: state.pending,
          subagents: state.subagents ?? [],
          todos: state.todos ?? [],
          context_used: 32000,
          context_window: 128000,
          context: {
            slices: [
              { id: 'system', label: 'system prompt', chars: 12000, tokens: 3200, pct: 2.5 },
              { id: 'tools', label: 'tool schemas', chars: 0, tokens: 4800, pct: 3.8 },
              { id: 'session', label: 'session', chars: 0, tokens: 24000, pct: 18.8 },
            ],
            total_tokens: 32000,
            window: 128000,
            measured: true,
            prompt_tokens: 32000,
            free_tokens: 96000,
          },
        }
      case 'transcript':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          transcript: state.transcript ?? [],
          notices: [],
          compactions: [],
        }
      case 'subagent_transcript': {
        const transcript = state.subagentTranscripts?.[String(payload.call_id ?? '')]
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          available: transcript !== undefined,
          transcript: transcript ?? [],
        }
      }
      case 'models':
        return { type: 'result', id: payload.id, ok: true, models: state.models ?? [] }
      case 'secrets': {
        const stored = () => state.secretsStored ?? []
        if (payload.op === 'list') {
          const modelProviders = state.secretsModel ?? [
            {
              id: 'deepseek',
              name: 'DeepSeek',
              env: 'DEEPSEEK_API_KEY',
              models: 2,
              configured: false,
              source: null,
              current: true,
            },
            {
              id: 'openai',
              name: 'OpenAI',
              env: 'OPENAI_API_KEY',
              models: 3,
              configured: false,
              source: null,
              current: false,
            },
          ]
          const searchProviders = state.secretsSearch ?? [
            {
              id: 'tavily',
              name: 'Tavily',
              env: 'TAVILY_API_KEY',
              configured: false,
              source: null,
            },
          ]
          const decorate = (row: Record<string, unknown>) => {
            const env = String(row.env)
            const isStored = stored().includes(env)
            return {
              ...row,
              configured: row.source === 'env' || isStored,
              source: row.source === 'env' ? 'env' : isStored ? 'stored' : null,
            }
          }
          return {
            type: 'result',
            id: payload.id,
            ok: true,
            model_providers: modelProviders.map(decorate),
            search_providers: searchProviders.map(decorate),
          }
        }
        if (payload.op === 'set') {
          const name = String(payload.name)
          state.secretsStored = [...new Set([...stored(), name])]
          return { type: 'result', id: payload.id, ok: true }
        }
        if (payload.op === 'clear') {
          state.secretsStored = stored().filter((name) => name !== String(payload.name))
          return { type: 'result', id: payload.id, ok: true }
        }
        if (payload.op === 'test') {
          return {
            type: 'result',
            id: payload.id,
            ok: true,
            ...(state.secretTests?.[String(payload.target)] ?? {
              connected: true,
              message: '',
            }),
          }
        }
        return { type: 'error', id: payload.id, message: 'unknown secrets op' }
      }
      case 'skills': {
        const rows =
          state.skills ?? [
            {
              name: 'review',
              description: '按 checklist 审查改动',
              path: '/work/p/.zenagent/skills/review/SKILL.md',
              source: 'project',
            },
            {
              name: 'tidy',
              description: '整理命名与文件结构',
              path: '/Users/e2e/.zenagent/skills/tidy/SKILL.md',
              source: 'global',
            },
          ]
        const disabled = () => state.skillsDisabled ?? []
        const decorate = () =>
          rows.map((row) => ({
            ...row,
            enabled: !disabled().includes(String(row.name)),
          }))
        if (payload.op === 'list' || payload.op === 'rescan') {
          return { type: 'result', id: payload.id, ok: true, skills: decorate(), warnings: [], notes: [] }
        }
        const name = String(payload.name ?? '')
        if (!rows.some((row) => row.name === name)) {
          return { type: 'error', id: payload.id, message: `unknown skill: ${name}` }
        }
        if (payload.op === 'read') {
          return {
            type: 'result',
            id: payload.id,
            ok: true,
            name,
            path: rows.find((row) => row.name === name)?.path,
            body: state.skillBodies?.[name] ?? `# ${name}\n\nbody of ${name}`,
            truncated: false,
          }
        }
        if (payload.op === 'toggle') {
          const enabled = payload.enabled === true
          state.skillsDisabled = enabled
            ? disabled().filter((entry) => entry !== name)
            : [...new Set([...disabled(), name])]
          return { type: 'result', id: payload.id, ok: true, name, enabled, notes: [] }
        }
        return { type: 'error', id: payload.id, message: 'unknown skills op' }
      }
      case 'limits': {
        // Ranges mirror the backend registry (zenagent/limits.py LIMITS/TASK_KNOBS)
        // so specs can rely on client-side range rejection.
        const knobs = [
          ['limits.skills', 'max_index_bytes', 12288, 'int', 'immediate', 1024, 262144],
          ['limits.skills', 'max_body_bytes', 32768, 'int', 'immediate', 1024, 1048576],
          ['limits.context', 'max_context_bytes', 32768, 'int', 'session', 0, 1048576],
          ['limits.context', 'compact_threshold', 0.85, 'float', 'session', 0.3, 0.99],
          ['limits.agent', 'max_turns', 100, 'int', 'session', 1, 10000],
          ['limits.agent', 'loop_guard_threshold', 5, 'int', 'immediate', 2, 100],
          ['limits.agent', 'max_result_bytes', 40000, 'int', 'session', 4096, 1048576],
          ['limits.tools', 'shell_max_output_bytes', 30000, 'int', 'immediate', 1024, 1048576],
          ['task', 'max_turns', 300, 'int', 'session', 1, 100000],
          ['task', 'timeout_s', 900, 'int', 'session', 0, 86400],
          ['task', 'max_cost', null, 'float', 'session', 0.000001, 1000000],
          ['task', 'keep_transcripts', 64, 'int', 'session', 1, 10000],
          ['task', 'max_transcript_bytes', 33554432, 'int', 'session', 1048576, 2147483648],
          ['task', 'max_transcript_file_bytes', 8388608, 'int', 'session', 1048576, 2147483648],
        ] as const
        const sections: Record<string, string> = {
          'limits.skills': 'skills',
          'limits.context': 'context',
          'limits.agent': 'agent',
          'limits.tools': 'tools',
          task: 'subagents',
        }
        const state2 = state
        const valueOf = (table: string, name: string, fallback: number | null) => {
          const values = state2.limitValues ?? {}
          const key = `${table}.${name}`
          return key in values ? (values[key] as number | null) : fallback
        }
        const explicitOf = (table: string, name: string) =>
          (state2.limitExplicit ?? []).includes(`${table}.${name}`)
        const overrides: Record<string, string> = { 'limits.agent.max_turns': '--max-turns 20' }
        const rows = () =>
          knobs.map(([table, name, fallback, kind, apply, min, max]) => ({
            key: `${table}.${name}`,
            section: sections[table] ?? table,
            table,
            name,
            kind,
            nullable: name === 'max_cost',
            min,
            max,
            default: fallback,
            unit: 'bytes',
            apply,
            value: valueOf(table, name, fallback),
            explicit: explicitOf(table, name),
            override: overrides[`${table}.${name}`] ?? null,
          }))
        const result = (ok = true, error?: string) => ({
          type: 'result',
          id: payload.id,
          ok,
          path: '/Users/e2e/.zenagent/config.toml',
          project_path: '/work/p/.zenagent/config.toml',
          groups: ['skills', 'context', 'agent', 'subagents', 'tools'],
          knobs: rows(),
          ...(error ? { error } : {}),
        })
        if (payload.op === 'get') return result()
        const key = String(payload.key ?? '')
        if (state.limitSetError) return result(false, state.limitSetError)
        const values = { ...(state.limitValues ?? {}) }
        const explicit = new Set(state.limitExplicit ?? [])
        if (payload.op === 'set') {
          values[key] = payload.value as number | null
          explicit.add(key)  // written to the file = explicit, whatever the value
        } else {
          delete values[key]
          explicit.delete(key)
        }
        state.limitValues = values
        state.limitExplicit = [...explicit]
        return result()
      }
      case 'plugins': return handlePlugins(state, payload)
      case 'mcp': {
        const rows = () =>
          state.mcpServers ?? [
            {
              name: 'filesystem',
              transport: 'stdio',
              command: 'npx -y @modelcontextprotocol/server-filesystem',
              url: null,
              env_keys: ['HOME'],
              header_keys: [],
              source: 'global',
              overrides_global: false,
              state: 'off',
              tools: [],
              error: null,
            },
            {
              name: 'docs',
              transport: 'http',
              url: 'https://example.com/mcp',
              command: null,
              env_keys: [],
              header_keys: ['Authorization'],
              source: 'project',
              overrides_global: true,
              state: 'off',
              tools: [],
              error: null,
            },
          ]
        const list = () => ({
          type: 'result',
          id: payload.id,
          ok: true,
          servers: rows(),
          problem: state.mcpProblem ?? null,
        })
        if (payload.op === 'list') return list()
        const name = String(payload.name ?? '')
        if (payload.op === 'add') {
          const transport = payload.transport === 'http' ? 'http' : 'stdio'
          const keys = (value: unknown) =>
            value !== null && typeof value === 'object' ? Object.keys(value) : []
          state.mcpServers = [
            ...rows(),
            {
              name,
              transport,
              command: typeof payload.command === 'string' ? payload.command : null,
              url: typeof payload.url === 'string' ? payload.url : null,
              env_keys: keys(payload.env),
              header_keys: keys(payload.headers),
              source: payload.layer === 'project' ? 'project' : 'global',
              overrides_global: false,
              state: 'off',
              tools: [],
              error: null,
            },
          ]
          return list()
        }
        if (payload.op === 'remove') {
          state.mcpServers = rows().filter(
            (row) => !(row.name === name && row.source === payload.layer),
          )
          return list()
        }
        if (payload.op === 'test') {
          const outcome = state.mcpTests?.[name] ?? { connected: true, tools: ['read', 'write'] }
          return {
            type: 'result',
            id: payload.id,
            ok: true,
            connected: outcome.connected,
            tools: outcome.tools ?? [],
            message: outcome.message ?? '',
          }
        }
        return { type: 'error', id: payload.id, message: 'unknown mcp op' }
      }
      case 'tools':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          tools: state.tools ?? [
            { name: 'read_file', description: 'Read a file', approval: 'read' },
          ],
        }
      case 'ask':
        return { type: 'result', id: payload.id, ok: true }
      case 'commands':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          commands: [
            { name: 'compact', description: 'Compact context' },
            { name: 'rename', description: 'Rename session' },
            { name: 'quit', description: 'Quit' },
          ],
        }
      case 'command':
        if (payload.name === 'rename') state.title = String(payload.argument ?? '')
        if (payload.name === 'thinking') state.thinking = String(payload.argument ?? 'off')
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          ...(payload.name === 'rename' ? { message: '已重命名' } : {}),
          ...(payload.name === 'memory' ? { screen: 'memory-manager' } : {}),
        }
      case 'session':
        switch (payload.op) {
          case 'list':
            return sessions(payload.id, payload.scope)
          case 'resume':
            currentSessionPath = String(payload.path)
            return { type: 'result', id: payload.id, ok: true }
          case 'export': {
            if (state.failExport) return { type: 'error', message: '导出失败' }
            const suffix = payload.format === 'html' ? '.html' : '.md'
            return {
              type: 'result',
              id: payload.id,
              ok: true,
              path: String(payload.path).replace(/\.jsonl$/, suffix),
            }
          }
          case 'promote': {
            const target = state.pending.find((item) => item.id === payload.pending_id)
            if (target === undefined) {
              return { type: 'result', id: payload.id, ok: true, outcome: 'missing' }
            }
            const nextId = Math.max(0, ...state.pending.map((item) => item.id)) + 1
            state.pending = state.pending.map((item) =>
              item.id === payload.pending_id
                ? { ...item, id: nextId, lane: 'steer' as Lane }
                : item,
            )
            return { type: 'result', id: payload.id, ok: true, outcome: 'ok' }
          }
          case 'dequeue': {
            let index = -1
            for (let position = state.pending.length - 1; position >= 0; position -= 1) {
              if (state.pending[position].lane === 'queue') {
                index = position
                break
              }
            }
            const taken = index >= 0 ? state.pending.splice(index, 1)[0] : undefined
            return {
              type: 'result',
              id: payload.id,
              ok: true,
              dequeued: taken !== undefined,
              text: taken?.text ?? null,
            }
          }
          case 'clear_pending': {
            const cleared = state.pending.length
            state.pending = []
            return { type: 'result', id: payload.id, ok: true, cleared }
          }
          default:
            return { type: 'result', id: payload.id, ok: true }
        }
      case 'fs_list': {
        const path = String(payload.path ?? '')
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          path,
          entries: state.fsEntries?.[path] ?? [],
          truncated: false,
        }
      }
      case 'fs_index':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          entries: state.fsIndex ?? [],
          truncated: false,
        }
      case 'fs_read': {
        const file = state.fsFiles?.[String(payload.path ?? '')]
        if (file === undefined) return { type: 'error', message: 'not found' }
        return { type: 'result', id: payload.id, ok: true, file }
      }
      case 'memory': {
        const entries = state.memory ?? []
        if (payload.op === 'list') {
          return { type: 'result', id: payload.id, ok: true, entries }
        }
        if (payload.op === 'create') {
          const entry: MemoryEntryMock = {
            id: `c${String(entries.length + 1).padStart(3, '0')}`,
            title: String(payload.title ?? ''),
            body: String(payload.body ?? ''),
            created: '2026-11-07T10:00:00',
            updated: '2026-11-07T10:00:00',
            source: 'ui',
          }
          state.memory = [...entries, entry]
          return { type: 'result', id: payload.id, ok: true, entry }
        }
        if (payload.op === 'update') {
          const id = String(payload.entry_id ?? '')
          const current = entries.find((entry) => entry.id === id)
          if (current === undefined) {
            return { type: 'error', id: payload.id, message: `memory entry not found: ${id}` }
          }
          const entry: MemoryEntryMock = {
            ...current,
            title: String(payload.title ?? ''),
            body: String(payload.body ?? ''),
            updated: '2026-11-07T11:00:00',
          }
          state.memory = entries.map((item) => (item.id === id ? entry : item))
          return { type: 'result', id: payload.id, ok: true, entry }
        }
        if (payload.op === 'delete') {
          const id = String(payload.entry_id ?? '')
          const deleted = entries.some((entry) => entry.id === id)
          state.memory = entries.filter((entry) => entry.id !== id)
          return { type: 'result', id: payload.id, ok: true, deleted }
        }
        return {
          type: 'error',
          id: payload.id,
          message: `unknown memory op: ${String(payload.op)}`,
        }
      }
      case 'git_worktrees':
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          worktree: state.worktrees ?? {
            root: '/work/p',
            is_repo: true,
            is_worktree: false,
            main: '/work/p',
            branch: 'main',
            worktrees: [
              {
                path: '/work/p',
                head: 'abc12345',
                branch: 'main',
                bare: false,
                detached: false,
                current: true,
              },
            ],
            status: { branch: 'main', staged: 0, dirty: 0, untracked: 0, clean: true },
          },
        }
      case 'taiji': {
        const op = String(payload.op ?? '')
        if (op === 'board') {
          return {
            type: 'result',
            id: payload.id,
            ok: true,
            board: state.taiji ?? {
              active: false,
              router_busy: false,
              slots: null,
              workers: [],
            },
          }
        }
        state.taijiRequests = [
          ...(state.taijiRequests ?? []),
          {
            op,
            name: payload.name as string | undefined,
            text: payload.text as string | undefined,
          },
        ]
        if (op === 'message') {
          const board = state.taiji as
            | { workers?: { name: string; status: string }[] }
            | undefined
          const worker = (board?.workers ?? []).find((w) => w.name === payload.name)
          if (worker === undefined) {
            return { type: 'error', id: payload.id, message: `message failed: worker 不存在: ${payload.name}` }
          }
          const queued = worker.status === 'running'
          return {
            type: 'result',
            id: payload.id,
            ok: true,
            queued,
            status: queued ? 'running' : 'ok',
          }
        }
        if (op === 'merge') {
          if (state.taijiMergeError !== undefined) {
            return { type: 'error', id: payload.id, message: state.taijiMergeError }
          }
          return { type: 'result', id: payload.id, ok: true }
        }
        if (op === 'kill') {
          return { type: 'result', id: payload.id, ok: true }
        }
        if (op === 'peek') {
          return { type: 'result', id: payload.id, ok: true, items: state.taijiPeek ?? [] }
        }
        return { type: 'error', id: payload.id, message: `unknown taiji op: ${op}` }
      }
      case 'project': {
        if (payload.op !== 'relocate') {
          return { type: 'error', id: payload.id, message: 'unknown project op' }
        }
        if (state.relocateError !== undefined) {
          return { type: 'error', id: payload.id, message: state.relocateError }
        }
        const from = String(payload.from ?? '')
        const to = String(payload.to ?? '')
        state.relocations = [...(state.relocations ?? []), { from, to }]
        return {
          type: 'result',
          id: payload.id,
          ok: true,
          old_root: from,
          new_root: to,
          old_key: 'old-key',
          new_key: 'new-key',
          sessions: 2,
          moved: true,
        }
      }
      default:
        return { type: 'result', id: payload.id, ok: true }
    }
  }

  const internals = {
    // The api package reads these for getCurrentWindow/getCurrentWebviewWindow;
    // boot calls the latter to show the window after the first paint.
    metadata: {
      currentWindow: { label: 'main' },
      currentWebview: { label: 'main' },
    },
    transformCallback(callback: (payload: unknown) => void) {
      const id = nextCallback
      nextCallback += 1
      callbacks.set(id, callback)
      return id
    },
    unregisterCallback(id: number) {
      callbacks.delete(id)
    },
    async invoke(
      cmd: string,
      args: Record<string, unknown> & { payload?: Record<string, unknown> },
    ) {
      if (cmd === 'plugin:event|listen') {
        const handler = callbacks.get(args.handler as number)
        if (handler !== undefined) {
          const event = String(args.event)
          const list = listeners.get(event) ?? []
          list.push(handler)
          listeners.set(event, list)
        }
        return nextEvent++
      }
      if (cmd === 'plugin:event|unlisten') return null
      if (cmd === 'rpc_start') {
        const options = (args.options ?? {}) as { cwd?: string | null }
        const pid = nextPid
        nextPid += 1
        state.starts.push({ cwd: options.cwd ?? null, pid })
        if (state.startHangs === true) {
          await new Promise<never>(() => {})
        }
        // A real child takes a few hundred ms to replace; the delay makes that
        // window observable instead of a race the spec would usually lose.
        if (state.switchDelayMs !== undefined) {
          await new Promise((resolve) => setTimeout(resolve, state.switchDelayMs))
        }
        return { pid }
      }
      if (cmd === 'rpc_stop') return null
      if (cmd === 'get_initial_state') {
        return {
          updatesDisabled: state.updatesDisabled ?? false,
          selfUpdatable: state.selfUpdatable ?? true,
        }
      }
      // `runtime_probe` is a `Result<RuntimeProbe, String>`: the Ok branch.
      if (cmd === 'runtime_probe') {
        return { resolved: '/app/zen', exists: true, version: '0.0.0-e2e' }
      }
      // The shell shows its window after the ready frame paints.
      if (cmd === 'plugin:window|show') {
        state.showCalls = (state.showCalls ?? 0) + 1
        return null
      }
      if (cmd === 'plugin:dialog|open') return state.dialogSelection
      if (cmd === 'plugin:path|resolve_directory') return '/Users/e2e'
      if (cmd === 'rpc_send') {
        const payload = args.payload ?? {}
        state.calls.push(payload)
        // The fake's status answers track the frames the client sent, so a
        // mode switch reads back on the next status poll.
        if (payload.type === 'mode' && typeof payload.mode === 'string') {
          currentMode = payload.mode
        }
        return respond(payload)
      }
      throw new Error(`unexpected invoke: ${cmd}`)
    },
  }

  window.__TAURI_INTERNALS__ = internals
  // The scripted page is a webview as far as the shell is concerned, so the
  // boot reveal (`isTauri()` → `plugin:window|show`) actually runs.
  window.isTauri = true
  ;(
    window as unknown as { __TAURI_EVENT_PLUGIN_INTERNALS__: unknown }
  ).__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} }
  ;(window as unknown as { __ZEN_E2E__: BridgeState }).__ZEN_E2E__ = state
  ;(window as unknown as { __ZEN_E2E_EMIT__: (event: string, payload: unknown) => void }).__ZEN_E2E_EMIT__ = (
    event,
    payload,
  ) => {
    for (const handler of listeners.get(event) ?? []) handler(payload)
  }
}

export function initialState(overrides: Partial<BridgeState> = {}): BridgeState {
  return {
    running: true,
    pending: [],
    calls: [],
    starts: [],
    dialogSelection: null,
    title: '修复登录超时',
    ...overrides,
  }
}

