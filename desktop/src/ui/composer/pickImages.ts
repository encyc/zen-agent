/** Image picking for the composer: the picker options and the extension
 * filter. The bridge call itself lives in sessions/fileDialog. */

import { openDialog, type DialogOptions, type DialogRunner } from '../sessions/fileDialog.ts'

export const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif', 'webp']

const IMAGE_RE = /\.(png|jpe?g|gif|webp)$/i

export const IMAGE_FILE_OPTIONS: DialogOptions = {
  multiple: true,
  directory: false,
  title: 'ZenAgent',
  filters: [{ name: 'Images', extensions: IMAGE_EXTENSIONS }],
}

export function imagePathsFromSelection(selection: string | string[] | null): string[] {
  if (selection === null) return []
  const paths = Array.isArray(selection) ? selection : [selection]
  return paths.filter((path) => IMAGE_RE.test(path))
}

export async function pickImagePaths(run: DialogRunner = openDialog): Promise<string[]> {
  const selection = await run(IMAGE_FILE_OPTIONS)
  return imagePathsFromSelection(selection)
}
