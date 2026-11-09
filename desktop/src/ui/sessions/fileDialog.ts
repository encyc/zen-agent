/** The desktop's one Tauri dialog-plugin entry.
 *
 * Every picker builds its own `DialogOptions` (one .jsonl session, a batch of
 * images, a project directory) but they all cross the same bridge command
 * here, so a plugin rename or an ACL change is a one-line edit. */

import { invoke } from '@tauri-apps/api/core'

export const DIALOG_OPEN = 'plugin:dialog|open'

export interface DialogOptions {
  multiple?: boolean
  directory?: boolean
  title?: string
  filters?: { name: string; extensions: string[] }[]
}

/** The plugin answers a string, a list, or null when the person cancelled. */
export type DialogSelection = string | string[] | null

/** One options object in, the plugin's answer out. */
export type DialogRunner = (options: DialogOptions) => Promise<DialogSelection>

type DialogInvoke = (command: string, args: { options: DialogOptions }) => Promise<DialogSelection>

const runInvoke: DialogInvoke = (command, args) => invoke<DialogSelection>(command, args)

export function openDialog(
  options: DialogOptions,
  run: DialogInvoke = runInvoke,
): Promise<DialogSelection> {
  return run(DIALOG_OPEN, { options })
}

export const SESSION_FILE_OPTIONS: DialogOptions = {
  multiple: false,
  directory: false,
  title: 'ZenAgent',
  filters: [{ name: 'Sessions', extensions: ['jsonl'] }],
}

export const PROJECT_FOLDER_OPTIONS: DialogOptions = {
  directory: true,
  multiple: false,
  title: 'ZenAgent',
}

export async function pickSessionFileToOpen(
  run: DialogRunner = openDialog,
): Promise<string | null> {
  try {
    const selection = await run(SESSION_FILE_OPTIONS)
    return typeof selection === 'string' ? selection : null
  } catch {
    return null
  }
}

export async function pickProjectFolder(run: DialogRunner = openDialog): Promise<string | null> {
  try {
    const selection = await run(PROJECT_FOLDER_OPTIONS)
    return typeof selection === 'string' ? selection : null
  } catch {
    return null
  }
}
