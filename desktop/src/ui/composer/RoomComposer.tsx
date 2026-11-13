/** The swarm room composer: the same plate as chat, bound to the room instead
 * of a coding session. `@` lists roles and workspace files, Enter follows the
 * same setting, and submitting dispatches through the supervisor — there is no
 * running turn to steer, so an empty plate turns into a stop control while
 * tasks are live. A quote supplies context for the next post; attachments fan
 * out to every role that post delegates to. */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useT } from '../../i18n/locale'
import {
  detectMention,
  formatMentionInsert,
  recognizedTokens,
} from '../../state/mentions'
import type { IndexedPath } from '../../state/mentions'
import { fuzzyMatchPath, selectBestMatches } from '../../state/fuzzy'
import type { FuzzyMatch } from '../../state/fuzzy'
import type { AttachmentImage } from '../../state/stores'
import { useAppStore } from '../../state/appStore'
import { useComposerStore, useSettingsStore } from '../../state/stores'
import { imagesNeedMention, roleMentions, roomSendPreview } from '../../state/roomPreview'
import {
  parseRoomCommand,
  roomCommandMenu,
  ROOM_COMMANDS,
} from '../../state/roomCommands'
import type { RoomCommand, RoomCommandEntry } from '../../state/roomCommands'
import type { FsIndexResult, SwarmRole } from '../../rpc/types'
import { useSwarmStore } from '../../state/swarmStore'
import { ApprovalPanel } from '../approval/ApprovalPanel'
import { AskCard } from '../ask/AskCard'
import { ChevronDownIcon, PlusIcon } from '../icons'
import { Tooltip } from '../primitives'
import { pickImagePaths } from './pickImages'
import {
  ComposerAttachments,
  ComposerMenu,
  ComposerPlate,
  ComposerRoot,
  isSubmitKey,
  type ComposerOption,
  type ComposerPrimary,
} from './ComposerPlate'
import { RoleModelMenu } from './Menus'
import { useImageDrop } from './useImageDrop'
import css from './RoomComposer.module.css'

const NO_IMAGES: AttachmentImage[] = []
const FS_INDEX_TIMEOUT_MS = 8000
const MAX_FILE_MATCHES = 24

type MentionChoice =
  | { kind: 'role'; role: SwarmRole }
  | { kind: 'file'; entry: IndexedPath }

function roleMeta(role: SwarmRole): string {
  return [role.model ?? '—', role.mode, role.thinking ?? '']
    .filter((part) => part !== '')
    .join(' · ')
}

/** A file mention is always quoted: the room's `@role` parser only sees bare
 * tokens, so `@"src/room.ts"` cannot be mistaken for a role named `src`. */
function quotedFile(path: string, kind: 'dir' | 'file'): string {
  const target = kind === 'dir' && !path.endsWith('/') ? `${path}/` : path
  return `@"${target}" `
}

export function RoomComposer({ roles }: { roles: SwarmRole[] }) {
  const t = useT()
  const post = useSwarmStore((state) => state.post)
  const reload = useSwarmStore((state) => state.reload)
  const cancel = useSwarmStore((state) => state.cancel)
  const resetAgent = useSwarmStore((state) => state.resetAgent)
  const saveRole = useSwarmStore((state) => state.saveRole)
  const tasks = useSwarmStore((state) => state.tasks)
  const project = useSwarmStore((state) => state.loadedProject) ?? ''
  const agents = useSwarmStore((state) => state.agents)
  const draft = useComposerStore((state) => state.room[project]?.draft ?? '')
  const quote = useComposerStore((state) => state.room[project]?.quote ?? null)
  const images = useComposerStore((state) => state.room[project]?.images ?? NO_IMAGES)
  const setDraft = useComposerStore((state) => state.setRoomDraft)
  const setQuote = useComposerStore((state) => state.setRoomQuote)
  const addImages = useComposerStore((state) => state.addRoomImages)
  const removeImage = useComposerStore((state) => state.removeRoomImage)
  const clearImages = useComposerStore((state) => state.clearRoomImages)
  const enterToSend = useSettingsStore((state) => state.enterToSend)
  const [caret, setCaret] = useState(0)
  const [active, setActive] = useState(0)
  const [dismissed, setDismissed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [commandError, setCommandError] = useState<string | null>(null)
  const [helpOpen, setHelpOpen] = useState(false)
  const [resetOpen, setResetOpen] = useState(false)
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const [modelBusy, setModelBusy] = useState(false)
  const [files, setFiles] = useState<IndexedPath[] | null>(null)
  const filesRequested = useRef(false)
  const area = useRef<HTMLTextAreaElement>(null)

  const trigger = detectMention(draft, caret)
  const mentionQuery = trigger?.query ?? null

  // Files are fetched the first time `@` opens; roles stay usable while the
  // index is in flight (or when the backend has no slot to answer for).
  const requestFiles = useCallback(() => {
    if (filesRequested.current) return
    filesRequested.current = true
    void useAppStore
      .getState()
      .client()
      .send<FsIndexResult>({ type: 'fs_index' }, FS_INDEX_TIMEOUT_MS)
      .then((result: FsIndexResult) => {
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
    if (mentionQuery !== null) requestFiles()
  }, [mentionQuery, requestFiles])

  const roleChoices: MentionChoice[] =
    trigger === null
      ? []
      : roles
          .filter((role) =>
            `${role.id} ${role.name}`.toLowerCase().includes(trigger.query.toLowerCase()),
          )
          .map((role) => ({ kind: 'role', role }))

  // An empty `@` stays a role picker; files join once the query has a shape.
  const fileMatches: Array<{ entry: IndexedPath; match: FuzzyMatch }> = []
  if (
    trigger !== null &&
    files !== null &&
    trigger.query.trim() !== '' &&
    trigger.query.length > 0
  ) {
    for (const entry of files) {
      const match = fuzzyMatchPath(trigger.query, entry.path, entry.kind)
      if (match !== null) fileMatches.push({ entry, match })
    }
  }
  const fileChoices: MentionChoice[] = selectBestMatches(
    fileMatches,
    MAX_FILE_MATCHES,
    ({ entry, match }) => ({ score: match.score, text: entry.path }),
  ).map(({ entry }) => ({ kind: 'file', entry }))

  const choices = [...roleChoices, ...fileChoices]
  const open = !dismissed && trigger !== null && choices.length > 0
  const commandEntries =
    !open && !resetOpen && /^\/\S*$/.test(draft) ? roomCommandMenu(draft) : []
  const commandOpen = !dismissed && commandEntries.length > 0
  const activeIndex = choices.length === 0 ? 0 : Math.min(active, choices.length - 1)
  const commandIndex =
    commandEntries.length === 0 ? 0 : Math.min(active, commandEntries.length - 1)
  const resetIndex = roles.length === 0 ? 0 : Math.min(active, roles.length - 1)
  const boxes = useMemo(
    () => recognizedTokens(draft, [], [], files ?? [], roles),
    [draft, files, roles],
  )
  const roleIds = roles.map((role) => role.id)

  const mentions = roleMentions(draft, roles.map((role) => role.id))
  const preview = roomSendPreview(mentions, roles, agents, images.length)
  const targets = mentions
    .map((id) => {
      const role = roles.find((entry) => entry.id === id)
      return role === undefined ? `@${id}` : `@${id} · ${roleMeta(role)}`
    })
    .join('  ')
  // The model chip is room-wide, not per mention: a chain delegation from one
  // role to another must use the same provider/model the person selected, so
  // every role manifest is updated together and no @ is needed to arm it.
  const modelValues = roles.map((role) => role.model ?? null)
  const modelChoices = new Set(modelValues)
  const modelValue = modelChoices.size === 1 ? (modelValues[0] ?? null) : null
  const modelMixed = modelChoices.size > 1
  const modelLabel =
    roles.length === 0
      ? t('swarm.modelEmpty')
      : modelMixed
        ? t('swarm.modelMixed')
        : (modelValue ?? t('swarm.modelDefault'))
  const liveTasks = tasks.filter(
    (task) => task.state === 'running' || task.state === 'queued',
  )

  useImageDrop((paths) => addImages(project, paths))

  const update = (value: string, position: number) => {
    setDraft(project, value)
    setCaret(position)
    setActive(0)
    setDismissed(false)
    setCommandError(null)
    setHelpOpen(false)
    setResetOpen(false)
    setModelMenuOpen(false)
  }

  const focusCaret = (position: number) => {
    requestAnimationFrame(() => {
      const node = area.current
      if (node === null) return
      node.focus()
      node.setSelectionRange(position, position)
    })
  }

  const pick = (choice: MentionChoice) => {
    if (trigger === null) return
    const insert =
      choice.kind === 'role'
        ? formatMentionInsert(choice.role.id, 'file')
        : quotedFile(choice.entry.path, choice.entry.kind)
    const next = `${draft.slice(0, trigger.tokenStart)}${insert}${draft.slice(trigger.tokenEnd)}`
    const position = trigger.tokenStart + insert.length
    update(next, position)
    focusCaret(position)
  }

  const pickCommand = (entry: RoomCommandEntry) => {
    if (entry.text === '/reset') {
      openReset()
      return
    }
    update(entry.insert, entry.insert.length)
    focusCaret(entry.insert.length)
  }

  const openHelp = () => {
    update('', 0)
    setHelpOpen(true)
    requestAnimationFrame(() => area.current?.focus())
  }

  const openReset = () => {
    if (roles.length === 0) {
      setCommandError(t('swarm.noAgents'))
      return
    }
    update('', 0)
    setResetOpen(true)
    setActive(0)
    requestAnimationFrame(() => area.current?.focus())
  }

  const stopAll = async () => {
    if (busy || liveTasks.length === 0) return
    setBusy(true)
    try {
      await Promise.allSettled(liveTasks.map((task) => cancel(task.id)))
    } finally {
      setBusy(false)
    }
  }

  const resetRole = async (roleId: string) => {
    setResetOpen(false)
    setBusy(true)
    try {
      await resetAgent(roleId)
    } finally {
      setBusy(false)
    }
  }

  const changeModel = async (modelId: string | null) => {
    setModelMenuOpen(false)
    if (modelBusy || roles.length === 0) return
    setModelBusy(true)
    setCommandError(null)
    try {
      for (const role of roles) {
        const answer = await saveRole('update', role.id, { model: modelId })
        if (answer.ok !== true) {
          const message =
            typeof answer.message === 'string' && answer.message !== ''
              ? answer.message
              : t('swarm.modelFailed')
          setCommandError(message)
          return
        }
      }
    } catch (cause) {
      setCommandError(cause instanceof Error ? cause.message : t('swarm.modelFailed'))
    } finally {
      setModelBusy(false)
    }
  }

  const runRoomCommand = async (command: RoomCommand) => {
    if (command.kind === 'reload') return reload()
    if (command.kind === 'stop') return stopAll()
    if (command.kind === 'help') return openHelp()
    if (command.kind === 'reset') return resetRole(command.roleId)
    setCommandError(t('swarm.commandUnknownReset', { role: command.roleId }))
  }

  const submit = async () => {
    const text = draft.trim()
    if (text === '') return
    if (text === '/reset') {
      openReset()
      return
    }
    const command = parseRoomCommand(text, roleIds)
    if (command !== null) {
      update('', 0)
      await runRoomCommand(command)
      return
    }
    if (busy || imagesNeedMention(images.length, mentions)) return
    setBusy(true)
    try {
      const sent = await post(text, {
        ...(quote !== null ? { ref: quote.id } : {}),
        ...(images.length > 0 ? { images: images.map((image) => image.path) } : {}),
      })
      // A refusal keeps everything typed: the host's reason sits under the
      // plate until the next keystroke.
      if (!sent.ok) {
        setCommandError(sent.message ?? t('swarm.postFailed'))
        return
      }
      setDraft(project, '')
      setQuote(project, null)
      clearImages(project)
      update('', 0)
    } finally {
      setBusy(false)
    }
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (helpOpen && event.key === 'Escape') {
      event.preventDefault()
      setHelpOpen(false)
      return
    }
    if (resetOpen) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActive((index) => (index + 1) % roles.length)
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActive((index) => (index - 1 + roles.length) % roles.length)
        return
      }
      if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        event.preventDefault()
        const role = roles[resetIndex]
        if (role !== undefined) void resetRole(role.id)
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setResetOpen(false)
        return
      }
    }
    if (open) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActive((index) => (index + 1) % choices.length)
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActive((index) => (index - 1 + choices.length) % choices.length)
        return
      }
      if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        event.preventDefault()
        pick(choices[activeIndex])
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setDismissed(true)
        return
      }
    }
    if (commandOpen) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActive((index) => (index + 1) % commandEntries.length)
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActive((index) => (index - 1 + commandEntries.length) % commandEntries.length)
        return
      }
      if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        event.preventDefault()
        // An exact command runs straight away; a prefix completes first.
        if (event.key === 'Enter' && parseRoomCommand(draft, roleIds) !== null) {
          void submit()
          return
        }
        pickCommand(commandEntries[commandIndex])
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        update('', 0)
        return
      }
    }
    if (event.key === 'Escape' && draft === '' && quote !== null) {
      event.preventDefault()
      setQuote(project, null)
      return
    }
    if (event.key === 'Escape' && draft.trim() === '' && liveTasks.length > 0) {
      event.preventDefault()
      void stopAll()
      return
    }
    if (isSubmitKey(event, enterToSend)) {
      event.preventDefault()
      void submit()
    }
  }

  const stopReady = draft.trim() === '' && liveTasks.length > 0 && !busy
  const primary: ComposerPrimary = stopReady
    ? { kind: 'stop', disabled: false, onPress: () => void stopAll() }
    : {
        kind: 'send',
        disabled: draft.trim() === '' || busy || imagesNeedMention(images.length, mentions),
        onPress: () => void submit(),
      }

  const menuOptions: ComposerOption[] = choices.map((choice): ComposerOption =>
    choice.kind === 'role'
      ? {
          key: `role:${choice.role.id}`,
          name: `@${choice.role.id}`,
          desc: `${choice.role.name} · ${roleMeta(choice.role)}`,
          kind: 'role',
          data: { 'data-swarm-mention': choice.role.id },
        }
      : {
          key: `file:${choice.entry.path}`,
          name: choice.entry.path,
          desc: t(choice.entry.kind === 'dir' ? 'composer.dirTag' : 'composer.fileTag'),
          kind: choice.entry.kind,
          data: { 'data-swarm-file-mention': choice.entry.path },
        },
  )
  const resetOptions: ComposerOption[] = roles.map((role) => ({
    key: `reset:${role.id}`,
    name: role.name,
    desc: `@${role.id} · ${roleMeta(role)}`,
    kind: 'role',
    data: { 'data-swarm-reset-role': role.id },
  }))

  return (
    <ComposerRoot>
      <ApprovalPanel />
      <AskCard />
      {helpOpen && (
        <section className={css.help} data-swarm-help aria-label={t('swarm.helpTitle')}>
          <div className={css.helpHead}>
            <span>{t('swarm.helpTitle')}</span>
            <button
              type="button"
              className={css.helpClose}
              aria-label={t('swarm.helpClose')}
              onClick={() => setHelpOpen(false)}
            >
              ×
            </button>
          </div>
          <ul className={css.helpList}>
            {ROOM_COMMANDS.map((entry) => (
              <li key={entry.text}>
                <code className={css.helpCode}>{entry.text}</code>
                <span>{t(entry.labelKey)}</span>
              </li>
            ))}
          </ul>
          <p className={css.helpHint}>{t('swarm.helpHint')}</p>
        </section>
      )}
      {resetOpen && (
        <div className={css.resetHint} data-swarm-reset-hint>
          <span className={css.resetTitle}>{t('swarm.resetTitle')}</span>
          <span>{t('swarm.resetHint')}</span>
        </div>
      )}
      {quote !== null && (
        <div className={css.quote} data-swarm-quote-strip aria-label={t('swarm.quoteStrip')}>
          <span className={css.quoteAuthor}>{quote.author.id}</span>
          <span className={css.quoteText}>{quote.text}</span>
          <button
            type="button"
            className={css.quoteClear}
            aria-label={t('swarm.quoteClear')}
            onClick={() => setQuote(project, null)}
          >
            ×
          </button>
        </div>
      )}
      <ComposerPlate
        value={draft}
        boxes={boxes}
        placeholder={t('swarm.placeholder')}
        inputRef={area}
        onChange={update}
        onCaret={setCaret}
        onKeyDown={onKeyDown}
        primary={primary}
        plateData={{ 'data-swarm-composer': '', 'data-composer-plate': '' }}
        inputData={{ 'data-swarm-input': '' }}
        attachments={
          images.length > 0 ? (
            <ComposerAttachments items={images} onRemove={(id) => removeImage(project, id)} />
          ) : undefined
        }
        menu={
          open ? (
            <ComposerMenu
              options={menuOptions}
              activeIndex={activeIndex}
              onHover={setActive}
              onPick={(index) => {
                const choice = choices[index]
                if (choice !== undefined) pick(choice)
              }}
            />
          ) : resetOpen ? (
            <ComposerMenu
              options={resetOptions}
              activeIndex={resetIndex}
              onHover={setActive}
              onPick={(index) => {
                const role = roles[index]
                if (role !== undefined) void resetRole(role.id)
              }}
            />
          ) : commandOpen ? (
            <ComposerMenu
              options={commandEntries.map((entry) => ({
                key: entry.text,
                name: entry.text,
                desc: t(entry.labelKey),
                data: { 'data-swarm-command': entry.text },
              }))}
              activeIndex={commandIndex}
              onHover={setActive}
              onPick={(index) => pickCommand(commandEntries[index])}
            />
          ) : undefined
        }
        leading={
          <Tooltip label={t('composer.attach')}>
            <button
              type="button"
              className={css.add}
              aria-label={t('composer.attach')}
              onClick={() => {
                void pickImagePaths()
                  .then((paths) => {
                    if (paths.length > 0) addImages(project, paths)
                  })
                  .catch(() => {})
              }}
            >
              <PlusIcon size={14} />
            </button>
          </Tooltip>
        }
        trailing={
          <>
            <span className={css.anchor}>
              <button
                type="button"
                className={css.modelChip}
                data-swarm-model
                data-swarm-model-value={modelValue ?? ''}
                aria-label={`${t('swarm.model')}: ${modelLabel}`}
                aria-expanded={modelMenuOpen}
                disabled={roles.length === 0 || modelBusy}
                title={roles.length === 0 ? t('swarm.modelEmpty') : t('swarm.modelHint')}
                onClick={() => setModelMenuOpen((open) => !open)}
              >
                <span className={css.modelName}>{modelLabel}</span>
                <ChevronDownIcon size={10} />
              </button>
              {modelMenuOpen && (
                <div className={css.menuLayer}>
                  <RoleModelMenu
                    current={modelValue}
                    mixed={modelMixed}
                    onPick={(modelId) => void changeModel(modelId)}
                    onClose={() => setModelMenuOpen(false)}
                  />
                </div>
              )}
            </span>
            <span
              className={css.preview}
              data-swarm-preview
              data-swarm-targets={targets || undefined}
              title={targets || undefined}
            >
              {targets !== '' && <span className={css.targets}>{targets}</span>}
              <span>
                {preview.tasks > 0
                  ? t('swarm.previewTasks', { n: preview.tasks })
                  : t('swarm.previewNoTasks')}
              </span>
              {preview.images > 0 && <span>{t('swarm.previewImages', { n: preview.images })}</span>}
              {preview.queued.map((role) => (
                <span key={`q-${role}`} className={css.warn}>
                  {t('swarm.previewQueued', { role })}
                </span>
              ))}
              {preview.noVision.map((role) => (
                <span key={`v-${role}`} className={css.warn}>
                  {t('swarm.previewNoVision', { role })}
                </span>
              ))}
              {images.length > 0 && preview.tasks === 0 && (
                <span className={css.warn}>{t('swarm.imagesNeedMention')}</span>
              )}
            </span>
          </>
        }
      />
      {commandError !== null && (
        <div className={css.commandError} data-swarm-command-error role="alert">
          {commandError}
        </div>
      )}
    </ComposerRoot>
  )
}
