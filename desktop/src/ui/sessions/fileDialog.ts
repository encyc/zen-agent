import { invoke } from '@tauri-apps/api/core'

export async function pickSessionFileToOpen(): Promise<string | null> {
  try {
    const selection = await invoke<string | string[] | null>('plugin:dialog|open', {
      options: {
        multiple: false,
        directory: false,
        title: 'ZenAgent',
        filters: [{ name: 'Sessions', extensions: ['jsonl'] }],
      },
    })
    return typeof selection === 'string' ? selection : null
  } catch {
    return null
  }
}
