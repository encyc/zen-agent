/** The swarm room composer: the same plate as chat, bound to the room instead
 * of a coding session. `@` lists roles (not files), Enter follows the same
 * setting, and submitting broadcasts with `swarm post` — there is no running
 * turn to steer, so the primary button is send only. A quote supplies context
 * for the next post; attachments fan out to every role that post delegates to. */

import { useMemo, useRef, useState } from 'react'
import { useT } from '../../i18n/locale'
import { detectMention, formatMentionInsert, recognizedTokens } from '../../state/mentions'
import type { AttachmentImage } from '../../state/stores'
import { useComposerStore, useSettingsStore } from '../../state/stores'
import { roleMentions, roomSendPreview } from '../../state/roomPreview'
import type { SwarmRole } from '../../rpc/types'
import { useSwarmStore } from '../../state/swarmStore'
import { ApprovalPanel } from '../approval/ApprovalPanel'
import { AskCard } from '../ask/AskCard'
import { PlusIcon } from '../icons'
import { Tooltip } from '../primitives'
import { pickImagePaths } from './pickImages'
import {
  ComposerAttachments,
  ComposerMenu,
  ComposerPlate,
  ComposerRoot,
  isSubmitKey,
  type ComposerPrimary,
} from './ComposerPlate'
import { useImageDrop } from './useImageDrop'
import css from './RoomComposer.module.css'

const NO_IMAGES: AttachmentImage[] = []

export function RoomComposer({ roles }: { roles: SwarmRole[] }) {
  const t = useT()
  const post = useSwarmStore((state) => state.post)
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
  const area = useRef<HTMLTextAreaElement>(null)

  const trigger = detectMention(draft, caret)
  const options =
    trigger === null
      ? []
      : roles.filter((role) =>
          `${role.id} ${role.name}`.toLowerCase().includes(trigger.query.toLowerCase()),
        )
  const open = !dismissed && trigger !== null && options.length > 0
  const boxes = useMemo(
    () => recognizedTokens(draft, [], [], [], roles),
    [draft, roles],
  )

  const mentions = roleMentions(draft, roles.map((role) => role.id))
  const preview = roomSendPreview(mentions, roles, agents, images.length)
  const targets = mentions
    .map((id) => {
      const role = roles.find((entry) => entry.id === id)
      return role === undefined ? `@${id}` : `@${id} · ${role.model ?? '—'}`
    })
    .join('  ')

  useImageDrop((paths) => addImages(project, paths))

  const update = (value: string, position: number) => {
    setDraft(project, value)
    setCaret(position)
    setActive(0)
    setDismissed(false)
  }

  const pick = (role: SwarmRole) => {
    if (trigger === null) return
    const insert = formatMentionInsert(role.id, 'file')
    const next = `${draft.slice(0, trigger.tokenStart)}${insert}${draft.slice(trigger.tokenEnd)}`
    const position = trigger.tokenStart + insert.length
    update(next, position)
    requestAnimationFrame(() => {
      const node = area.current
      if (node === null) return
      node.focus()
      node.setSelectionRange(position, position)
    })
  }

  const submit = async () => {
    const text = draft.trim()
    if (text === '' || busy) return
    setBusy(true)
    try {
      await post(text, {
        ...(quote !== null ? { ref: quote.id } : {}),
        ...(images.length > 0 ? { images: images.map((image) => image.path) } : {}),
      })
      setDraft(project, '')
      setQuote(project, null)
      clearImages(project)
      update('', 0)
    } finally {
      setBusy(false)
    }
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (open) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setActive((index) => (index + 1) % options.length)
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setActive((index) => (index - 1 + options.length) % options.length)
        return
      }
      if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        event.preventDefault()
        pick(options[Math.min(active, options.length - 1)])
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setDismissed(true)
        return
      }
    }
    if (event.key === 'Escape' && draft === '' && quote !== null) {
      event.preventDefault()
      setQuote(project, null)
      return
    }
    if (isSubmitKey(event, enterToSend)) {
      event.preventDefault()
      void submit()
    }
  }

  const primary: ComposerPrimary = {
    kind: 'send',
    disabled: draft.trim() === '' || busy || (images.length > 0 && preview.tasks === 0),
    onPress: () => void submit(),
  }

  return (
    <ComposerRoot>
      <ApprovalPanel />
      <AskCard />
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
              options={options.map((role) => ({
                key: role.id,
                name: `@${role.id}`,
                desc: role.name,
                data: { 'data-swarm-mention': role.id },
              }))}
              activeIndex={Math.min(active, options.length - 1)}
              onHover={setActive}
              onPick={(index) => pick(options[index])}
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
          <span className={css.preview} data-swarm-preview>
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
        }
      />
    </ComposerRoot>
  )
}
