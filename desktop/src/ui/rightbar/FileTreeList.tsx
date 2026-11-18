/** Tree rendering for a changed-file list: folder rows (chevron + name +
 * recursive count) over indented file rows supplied by the caller. The caller
 * keeps ownership of how a file looks (changes row vs commit fold row). */

import { useMemo, type ReactNode } from 'react'
import { ChevronDownIcon, ChevronRightIcon, FolderIcon } from '../icons'
import { IndentGuides } from './IndentGuides.tsx'
import { buildFileTree, flattenTree } from './fileTree.ts'
import css from './FileTreeList.module.css'

export function FileTreeList<T extends { path: string }>({
  files,
  collapsed,
  onToggleDir,
  renderFile,
}: {
  files: T[]
  collapsed: ReadonlySet<string>
  onToggleDir(path: string): void
  renderFile(file: T, depth: number): ReactNode
}) {
  const tree = useMemo(() => buildFileTree(files), [files])
  const rows = useMemo(() => flattenTree(tree, collapsed), [tree, collapsed])

  return (
    <>
      {rows.map(({ node, depth }) =>
        node.kind === 'dir' ? (
          <button
            key={`dir:${node.path}`}
            type="button"
            className={css.dir}
            data-tree-dir={node.path}
            style={{ paddingLeft: 8 + depth * 12 }}
            aria-expanded={!collapsed.has(node.path)}
            title={node.path}
            onClick={() => onToggleDir(node.path)}
          >
            <IndentGuides depth={depth} />
            {collapsed.has(node.path) ? (
              <ChevronRightIcon size={12} />
            ) : (
              <ChevronDownIcon size={12} />
            )}
            <FolderIcon size={14} />
            <span className={css.name}>{node.name}</span>
            <span className={css.count}>{node.files}</span>
          </button>
        ) : (
          renderFile(node.file, depth)
        ),
      )}
    </>
  )
}
