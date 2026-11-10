/** The Settings rail: one destination per panel, grouped for the searchable
 * left column. Extracted from SettingsPage to keep that page's line budget. */

import type { ReactNode } from 'react'
import {
  ActivityIcon,
  BotIcon,
  ChatIcon,
  FileIcon,
  FolderIcon,
  InfoIcon,
  ListTodoIcon,
  PaletteIcon,
  SparkIcon,
  TerminalIcon,
} from '../icons'

export type Tab =
  | 'appearance'
  | 'models'
  | 'conversation'
  | 'skills'
  | 'limits'
  | 'mcp'
  | 'plugins'
  | 'agents'
  | 'projects'
  | 'about'
export type NavGroup = 'preferences' | 'resources' | 'system'

export interface NavItem {
  id: Tab
  labelKey: string
  hintKey: string
  icon: ReactNode
  group: NavGroup
  keywords: string[]
}

export const NAV_ITEMS: NavItem[] = [
  {
    id: 'appearance',
    labelKey: 'settings.appearance',
    hintKey: 'settings.appearanceHint',
    icon: <PaletteIcon size={14} />,
    group: 'preferences',
    keywords: ['settings.theme', 'settings.themePalette', 'settings.language', 'settings.fontScale'],
  },
  {
    id: 'models',
    labelKey: 'settings.models',
    hintKey: 'settings.modelsHint',
    icon: <SparkIcon size={14} />,
    group: 'preferences',
    keywords: [
      'settings.modelProviders',
      'settings.searchProviders',
      'settings.configured',
      'settings.testConnection',
    ],
  },
  {
    id: 'conversation',
    labelKey: 'settings.conversation',
    hintKey: 'settings.conversationHint',
    icon: <ChatIcon size={14} />,
    group: 'preferences',
    keywords: [
      'settings.sessionTime',
      'settings.chatWidth',
      'settings.showThinking',
      'settings.autoCollapseThinking',
      'settings.contextUsageDisplay',
      'settings.enterToSend',
    ],
  },
  {
    id: 'skills',
    labelKey: 'settings.skills',
    hintKey: 'settings.skillsHint',
    icon: <FileIcon size={14} />,
    group: 'resources',
    keywords: ['settings.skillsList', 'settings.skillProject', 'settings.skillGlobal'],
  },
  {
    id: 'limits',
    labelKey: 'settings.limits',
    hintKey: 'settings.limitsHint',
    icon: <ActivityIcon size={14} />,
    group: 'system',
    keywords: [
      'settings.limitFile',
      'settings.limit.skills.max_index_bytes',
      'settings.limit.task.max_turns',
      'settings.limitGroup.subagents',
      'settings.limitGroup.tools',
    ],
  },
  {
    id: 'mcp',
    labelKey: 'settings.mcp',
    hintKey: 'settings.mcpHint',
    icon: <TerminalIcon size={14} />,
    group: 'resources',
    keywords: ['settings.mcpServers', 'settings.mcpAdd', 'settings.mcpLayer'],
  },
  {
    id: 'plugins',
    labelKey: 'settings.plugins',
    hintKey: 'settings.pluginsHint',
    icon: <BotIcon size={14} />,
    group: 'resources',
    keywords: [
      'settings.pluginsCatalog',
      'settings.pluginsInstalledSection',
      'settings.pluginsApplyPending',
    ],
  },
  {
    id: 'agents',
    labelKey: 'settings.agents',
    hintKey: 'settings.agentsHint',
    icon: <ListTodoIcon size={14} />,
    group: 'resources',
    keywords: ['swarm.rolesList', 'swarm.roleNew', 'swarm.rolePrompt', 'swarm.roleRoot'],
  },
  {
    id: 'projects',
    labelKey: 'settings.projects',
    hintKey: 'settings.projectsHint',
    icon: <FolderIcon size={14} />,
    group: 'resources',
    keywords: ['settings.projectsList', 'settings.projectAdd', 'settings.projectNewSession'],
  },
  {
    id: 'about',
    labelKey: 'settings.about',
    hintKey: 'settings.aboutHint',
    icon: <InfoIcon size={14} />,
    group: 'system',
    keywords: ['settings.aboutVersion'],
  },
]

export const NAV_GROUPS: { id: NavGroup; labelKey: string }[] = [
  { id: 'preferences', labelKey: 'settings.groupPreferences' },
  { id: 'resources', labelKey: 'settings.groupResources' },
  { id: 'system', labelKey: 'settings.groupSystem' },
]
