/** The swarm room composer: the same plate as chat, bound to the room instead
 * of a coding session. `@` lists roles (not files), Enter follows the same
 * setting, and submitting broadcasts with `swarm post` — there is no running
 * turn to steer, so the primary button is send only. */

import { useMemo, useRef, useState } from 'react'
import { useT } from '../../i18n/locale'
import { detectMention, formatMentionInsert, recognizedTokens } from '../../state/mentions'
import { useComposerStore, useSettingsStore } from '../../state/stores'
import type { SwarmRole } from '../../rpc/types'
import { useSwarmStore } from '../../state/swarmStore'
import { ApprovalPanel } from '../approval/ApprovalPanel'
import { AskCard } from '../ask/AskCard'
import {
  ComposerMenu,
  ComposerPlate,
  ComposerRoot,
  isSubmitKey,
  type ComposerPrimary,
} from './ComposerPlate'

export function RoomComposer({ roles }: { roles: SwarmRole[] }) {
  const t = useT()
  const post = useSwarmStore((state) => state.post)
  const project = useSwarmStore((state) => state.loadedProject) ?? ''
  const draft = useComposerStore((state) => state.room[project]?.draft ?? '')
  const setDraft = useComposerStore((state) => state.setRoomDraft)
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
      await post(text)
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
    if (isSubmitKey(event, enterToSend)) {
      event.preventDefault()
      void submit()
    }
  }

  const primary: ComposerPrimary = {
    kind: 'send',
    disabled: draft.trim() === '' || busy,
    onPress: () => void submit(),
  }

  return (
    <ComposerRoot>
      <ApprovalPanel />
      <AskCard />
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
      />
    </ComposerRoot>
  )
}
