import { invoke } from '@tauri-apps/api/core'

export const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif', 'webp']

const IMAGE_RE = /\.(png|jpe?g|gif|webp)$/i

export function imagePathsFromSelection(selection: string | string[] | null): string[] {
  if (selection === null) return []
  const paths = Array.isArray(selection) ? selection : [selection]
  return paths.filter((path) => IMAGE_RE.test(path))
}

export async function pickImagePaths(): Promise<string[]> {
  const selection = await invoke<string | string[] | null>('plugin:dialog|open', {
    options: {
      multiple: true,
      directory: false,
      title: 'ZenAgent',
      filters: [{ name: 'Images', extensions: IMAGE_EXTENSIONS }],
    },
  })
  return imagePathsFromSelection(selection)
}
