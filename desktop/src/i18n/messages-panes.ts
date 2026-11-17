/** The multi-session panes' copy, split out of messages.ts to keep the core
 * dictionary under the architecture line budget. Keys are prefixed `panes.`;
 * the two locales mirror each other exactly. */

export const panesZh: Record<string, string> = {
  'panes.openInSplit': '在旁边打开',
  'panes.capReached': '最多同时打开 3 个会话面板。',
  'panes.close': '关闭面板',
  'panes.popOut': '拖出为独立窗口',
  'panes.stop': '停止运行',
  'panes.load': '载入会话',
  'panes.notResident': '会话未在后台运行，点击载入以恢复。',
  'panes.staticHint': '静态快照（后台无此会话）',
  'panes.empty': '这个会话还没有内容。',
  'panes.composerPlaceholder': '发消息到这个会话…',
  'panes.detachFailed': '独立窗口只能在桌面应用中打开。',
  'panes.split': '分屏',
}

export const panesEn: Record<string, string> = {
  'panes.openInSplit': 'Open in split',
  'panes.capReached': 'At most 3 session panes can be open at once.',
  'panes.close': 'Close pane',
  'panes.popOut': 'Pop out into its own window',
  'panes.stop': 'Stop the run',
  'panes.load': 'Load session',
  'panes.notResident': 'This session is not running in the background; click load to resume it.',
  'panes.staticHint': 'Static snapshot (not resident in the background)',
  'panes.empty': 'This session has no content yet.',
  'panes.composerPlaceholder': 'Message this session…',
  'panes.detachFailed': 'Detached windows only work in the desktop app.',
  'panes.split': 'Split',
}
