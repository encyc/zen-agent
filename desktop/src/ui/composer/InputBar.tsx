/** The coding/zen composer: attachments rail, the shared plate, control row
 * (attach · mode | context · model+thinking · send/stop), slash-command menu,
 * plus the pending lanes and todo docks stacked above the plate.
 * Enter submits; while running it steers; Esc interrupts. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import clsx from 'clsx'
import { useAppStore } from '../../state/appStore'
import { useApprovalStore, useAskStore, useComposerStore, useSettingsStore, useStatusStore, useCatalogStore, useSkillsStore } from '../../state/stores'
import { slashEntries } from '../../state/slashMenu'
import { detectMention, formatMentionInsert, recognizedTokens } from '../../state/mentions'
import type { IndexedPath } from '../../state/mentions'
import { fuzzyMatchPath, selectBestMatches } from '../../state/fuzzy'
import type { FuzzyMatch } from '../../state/fuzzy'
import type { FsIndexResult } from '../../rpc/types'
import { useT } from '../../i18n/locale'
import { ApprovalPanel } from '../approval/ApprovalPanel'
import { AskCard } from '../ask/AskCard'
import { pickImagePaths } from './pickImages'
import { attachDragDrop } from './dragDrop'
import { TodoDock, PendingDock, ContextMeter } from './Docks'
import { ModelMenu, ModeMenu } from './Menus'
import {
  ComposerAttachments,
  ComposerMenu,
  ComposerPlate,
  ComposerRoot,
  isSubmitKey,
  type ComposerPrimary,
} from './ComposerPlate'
import { ChevronDownIcon, PlusIcon } from '../icons'
import { Tooltip } from '../primitives'
import css from './InputBar.module.css'

const FS_INDEX_TIMEOUT_MS = 8000
const MAX_FILE_ITEMS = 50

interface MenuState {
  kind: 'model' | 'mode' | null
}

export function InputBar() {
  const t = useT()
  const busy = useAppStore((state) => state.busy)
  const submit = useAppStore((state) => state.submit)
  const cancel = useAppStore((state) => state.cancel)
  const steer = useAppStore((state) => state.steer)
  const setMode = useAppStore((state) => state.setMode)
  const status = useStatusStore((state) => state.status)
  const mode = status?.mode ?? 'zen'
  const model = status?.model ?? ''
  const thinking = status?.thinking ?? null

  const draft = useComposerStore((state) => state.draft)
  const setDraft = useComposerStore((state) => state.setDraft)
  const images = useComposerStore((state) => state.images)
  const addImages = useComposerStore((state) => state.addImages)
  const removeImage = useComposerStore((state) => state.removeImage)
  const commands = useCatalogStore((state) => state.commands)
  const skills = useSkillsStore((state) => state.skills)
  const refreshSkills = useAppStore((state) => state.refreshSkills)
  const enterToSend = useSettingsStore((state) => state.enterToSend)
  const approvalCount = useApprovalStore((state) => state.queue.length)
  const askCount = useAskStore((state) => state.queue.length)

  const [menu, setMenu] = useState<MenuState>({ kind: null })
  const [slashIndex, setSlashIndex] = useState(0)
  const [cursor, setCursor] = useState(0)
  const [files, setFiles] = useState<IndexedPath[] | null>(null)
  const [mentionDismissed, setMentionDismissed] = useState(false)
  const filesRequested = useRef(false)
  const textRef = useRef<HTMLTextAreaElement | null>(null)

  const empty = draft.trim() === '' && images.length === 0
  const entries = useMemo(
    () => slashEntries(commands, skills, draft),
    [commands, skills, draft],
  )
  const mention = useMemo(() => detectMention(draft, cursor), [draft, cursor])
  const mentionActive = mention !== null

  // The workspace index is fetched the first time `@` opens, then kept for
  // this composer's lifetime; the backend refreshes its own snapshot.
  const requestFiles = useCallback(() => {
    if (filesRequested.current) return
    filesRequested.current = true
    void useAppStore
      .getState()
      .client()
      .send<FsIndexResult>({ type: 'fs_index' }, FS_INDEX_TIMEOUT_MS)
      .then((result) => {
        if (result.ok !== true) {
          filesRequested.current = false
          return
        }
        setFiles(result.entries ?? [])
      })
      .catch(() => {
        filesRequested.current = false
      })
  }, [])

  useEffect(() => {
    if (mentionActive) requestFiles()
  }, [mentionActive, requestFiles])

  const mentionRows = useMemo(() => {
    if (mention === null || files === null) return []
    const matched: Array<{ entry: IndexedPath; match: FuzzyMatch }> = []
    for (const entry of files) {
      const match = fuzzyMatchPath(mention.query, entry.path, entry.kind)
      if (match !== null) matched.push({ entry, match })
    }
    return selectBestMatches(matched, MAX_FILE_ITEMS, ({ entry, match }) => ({
      score: match.score,
      text: entry.path,
    }))
  }, [files, mention])

  const boxes = useMemo(
    () => recognizedTokens(draft, commands, skills, files ?? []),
    [draft, commands, skills, files],
  )

  const mentionOpen =
    mention !== null &&
    !mentionDismissed &&
    menu.kind === null &&
    mentionRows.length > 0
  const slashOpen = entries.length > 0 && menu.kind === null && mention === null
  const menuOpen = mentionOpen || slashOpen
  const rowCount = mentionOpen ? mentionRows.length : slashOpen ? entries.length : 0
  const activeIndex = rowCount === 0 ? -1 : Math.min(slashIndex, rowCount - 1)

  // Newly enabled skills (settings toggle, external edit) appear on next open.
  useEffect(() => {
    void refreshSkills()
  }, [refreshSkills])

  // Tauri drag-drop: file drop events carry absolute paths. The helper's
  // cleanup unlistens even when StrictMode tears this effect down before the
  // async registration settles.
  useEffect(
    () =>
      attachDragDrop((paths) => {
        const imagePaths = paths.filter((path) => /\.(png|jpe?g|gif|webp|bmp)$/i.test(path))
        if (imagePaths.length > 0) addImages(imagePaths)
      }),
    [addImages],
  )

  const doSubmit = () => {
    setMenu({ kind: null })
    if (busy) {
      const text = draft.trim()
      if (text === '') return
      void steer(text)
      return
    }
    void submit()
    requestAnimationFrame(() => textRef.current?.focus())
  }

  const acceptMention = (index: number) => {
    const row = mentionRows[index]
    if (row === undefined || mention === null) return
    const insert = formatMentionInsert(row.entry.path, row.entry.kind)
    const caret = mention.tokenStart + insert.length
    setDraft(
      draft.slice(0, mention.tokenStart) + insert + draft.slice(mention.tokenEnd),
    )
    setSlashIndex(0)
    requestAnimationFrame(() => {
      const area = textRef.current
      if (area === null) return
      area.focus()
      area.setSelectionRange(caret, caret)
      setCursor(caret)
    })
  }

  const pickCommand = (index: number) => {
    const picked = entries[index]
    if (picked === undefined) return
    setDraft(`/${picked.name} `)
    setSlashIndex(0)
    textRef.current?.focus()
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // The composer menu (slash commands or @ files) owns these keys first.
    if (menuOpen) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setSlashIndex((index) => (index + 1) % rowCount)
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setSlashIndex((index) => (index - 1 + rowCount) % rowCount)
        return
      }
      if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        event.preventDefault()
        if (mentionOpen) acceptMention(activeIndex)
        else pickCommand(activeIndex)
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        if (mentionOpen) setMentionDismissed(true)
        else setDraft('')
        return
      }
    }
    if (isSubmitKey(event, enterToSend)) {
      event.preventDefault()
      doSubmit()
      return
    }
    if (event.key === 'Escape' && busy) {
      event.preventDefault()
      void cancel()
    }
  }

  const placeholder = busy
    ? t('composer.placeholder.busy')
    : mode === 'plan'
      ? t('composer.placeholder.plan')
      : t('composer.placeholder')

  const primary: ComposerPrimary = busy
    ? empty
      ? { kind: 'stop', disabled: false, onPress: () => void cancel() }
      : { kind: 'steer', disabled: false, onPress: doSubmit }
    : { kind: 'send', disabled: empty, onPress: doSubmit }

  const options = mentionOpen
    ? mentionRows.map(({ entry, match }) => ({
        key: entry.path,
        kind: entry.kind,
        name: markedPath(entry, match.ranges),
        desc: t(entry.kind === 'dir' ? 'composer.dirTag' : 'composer.fileTag'),
      }))
    : entries.map((entry) => ({
        key: entry.name,
        kind: entry.kind,
        name: `/${entry.name}`,
        desc:
          entry.kind === 'skill'
            ? `${t('composer.skillTag')} · ${entry.description}`
            : entry.description,
      }))

  return (
    <ComposerRoot>
      {approvalCount > 0 && <ApprovalPanel />}
      {askCount > 0 && <AskCard />}
      <PendingDock />
      <TodoDock />
      <ComposerPlate
        value={draft}
        boxes={boxes}
        placeholder={placeholder}
        inputRef={textRef}
        onChange={(value, caret) => {
          setDraft(value)
          setCursor(caret)
          setSlashIndex(0)
          setMentionDismissed(false)
        }}
        onCaret={setCursor}
        onKeyDown={onKeyDown}
        primary={primary}
        plateData={{ 'data-composer-plate': '' }}
        menu={
          menuOpen ? (
            <ComposerMenu
              options={options}
              activeIndex={activeIndex}
              onHover={setSlashIndex}
              onPick={(index) => {
                if (mentionOpen) acceptMention(index)
                else pickCommand(index)
              }}
            />
          ) : undefined
        }
        attachments={
          images.length > 0 ? (
            <ComposerAttachments items={images} onRemove={removeImage} />
          ) : undefined
        }
        leading={
          <>
            <Tooltip label={t('composer.attach')}>
              <button
                type="button"
                className={css.add}
                aria-label={t('composer.attach')}
                onClick={() => {
                  void pickImagePaths()
                    .then((paths) => {
                      if (paths.length > 0) addImages(paths)
                    })
                    .catch(() => {})
                }}
              >
                <PlusIcon size={14} />
              </button>
            </Tooltip>
            <div className={css.modes}>
              <span className={css.anchor}>
                <ModeChip mode={mode} onOpen={() => setMenu({ kind: 'mode' })} />
                {menu.kind === 'mode' && (
                  <div className={css.menuLayer}>
                    <ModeMenu
                      mode={mode}
                      onPick={(picked) => {
                        void setMode(picked)
                        setMenu({ kind: null })
                      }}
                      onClose={() => setMenu({ kind: null })}
                    />
                  </div>
                )}
              </span>
            </div>
          </>
        }
        trailing={
          <>
            <ContextMeter />
            <span className={css.anchor} data-align="right">
              <ModelChip model={model} thinking={thinking} onOpen={() => setMenu({ kind: 'model' })} />
              {menu.kind === 'model' && (
                <div className={css.menuLayer}>
                  <ModelMenu
                    thinking={thinking}
                    onThinkingPick={(level) => {
                      void useAppStore.getState().setThinking(level)
                    }}
                    onClose={() => setMenu({ kind: null })}
                  />
                </div>
              )}
            </span>
          </>
        }
      />
    </ComposerRoot>
  )
}

function markedPath(entry: IndexedPath, ranges: Array<[number, number]>): React.ReactNode {
  const text = entry.kind === 'dir' ? `${entry.path}/` : entry.path
  const parts: React.ReactNode[] = []
  let at = 0
  ranges.forEach(([start, end], index) => {
    const from = Math.max(at, Math.min(start, text.length))
    const to = Math.max(from, Math.min(end, text.length))
    if (from > at) parts.push(text.slice(at, from))
    if (to > from) {
      parts.push(
        <mark key={index} className={css.matchHit}>
          {text.slice(from, to)}
        </mark>,
      )
    }
    at = to
  })
  if (at < text.length) parts.push(text.slice(at))
  return parts
}

function ModeChip({ mode, onOpen }: { mode: string; onOpen(): void }) {
  const t = useT()
  return (
    <button
      type="button"
      className={clsx(css.chip, css.modeChip)}
      data-menu-trigger="mode"
      onClick={onOpen}
    >
      <span className={css.chipLabel} data-mode={mode}>{t(`status.mode.${mode}`)}</span>
      <ChevronDownIcon size={11} />
    </button>
  )
}

function ModelChip({
  model,
  thinking,
  onOpen,
}: {
  model: string
  thinking: string | null
  onOpen(): void
}) {
  const t = useT()
  const level = thinking !== null && thinking !== 'off' ? thinking : null
  return (
    <button
      type="button"
      className={css.chip}
      data-menu-trigger="model"
      aria-label={
        level === null
          ? `${t('menu.model')}: ${model}`
          : `${t('menu.model')}: ${model}. ${t('menu.thinking')}: ${level}`
      }
      onClick={onOpen}
    >
      <span className={css.chipLabel}>{model}</span>
      {level !== null ? <span className={css.thinkingBadge}>{level}</span> : null}
      <ChevronDownIcon size={11} />
    </button>
  )
}

export { clsx }
