/** Directory trees over a changed-file list (VSCode's tree view).
 *
 * Paths are git-relative POSIX strings. A folder row owns its children and a
 * recursive file count; `flattenTree` yields the visible pre-order rows for a
 * set of collapsed folder paths, which is all the renderer iterates. */

export interface TreeFile<T> {
  kind: 'file'
  name: string
  path: string
  file: T
}

export interface TreeDir<T> {
  kind: 'dir'
  name: string
  path: string
  children: TreeRow<T>[]
  /** Descendant file count, shown on the folder row. */
  files: number
}

export type TreeRow<T> = TreeDir<T> | TreeFile<T>

export interface VisibleTreeRow<T> {
  node: TreeRow<T>
  depth: number
}

function compareRows<T>(a: TreeRow<T>, b: TreeRow<T>): number {
  if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1
  return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
}

function countFiles<T>(dir: TreeDir<T>): number {
  let total = 0
  for (const child of dir.children) {
    total += child.kind === 'file' ? 1 : countFiles(child)
  }
  dir.files = total
  return total
}

/** The root folder is unnamed/empty; a single-file input still yields a tree
 * so the caller renders one row at depth 0. */
export function buildFileTree<T extends { path: string }>(files: T[]): TreeDir<T> {
  const root: TreeDir<T> = { kind: 'dir', name: '', path: '', children: [], files: 0 }
  const dirs = new Map<string, TreeDir<T>>([['', root]])

  for (const file of files) {
    const parts = file.path.split('/').filter((part) => part !== '')
    if (parts.length === 0) continue
    let parent = root
    for (let index = 0; index < parts.length - 1; index += 1) {
      const path = parts.slice(0, index + 1).join('/')
      let dir = dirs.get(path)
      if (dir === undefined) {
        dir = { kind: 'dir', name: parts[index], path, children: [], files: 0 }
        dirs.set(path, dir)
        parent.children.push(dir)
      }
      parent = dir
    }
    parent.children.push({
      kind: 'file',
      name: parts[parts.length - 1],
      path: file.path,
      file,
    })
  }

  const sort = (dir: TreeDir<T>) => {
    dir.children.sort(compareRows)
    for (const child of dir.children) if (child.kind === 'dir') sort(child)
    countFiles(dir)
  }
  sort(root)
  return root
}

export function flattenTree<T>(
  root: TreeDir<T>,
  collapsed: ReadonlySet<string>,
): VisibleTreeRow<T>[] {
  const rows: VisibleTreeRow<T>[] = []
  const walk = (dir: TreeDir<T>, depth: number) => {
    for (const child of dir.children) {
      rows.push({ node: child, depth })
      if (child.kind === 'dir' && !collapsed.has(child.path)) walk(child, depth + 1)
    }
  }
  walk(root, 0)
  return rows
}

/** Every folder path in the tree, for a collapse-all action. */
export function dirPaths<T>(root: TreeDir<T>): string[] {
  const paths: string[] = []
  const walk = (dir: TreeDir<T>) => {
    for (const child of dir.children) {
      if (child.kind !== 'dir') continue
      paths.push(child.path)
      walk(child)
    }
  }
  walk(root)
  return paths
}
