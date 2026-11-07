/** Project registry: the folders the rail remembers, their session counts and
 * last activity. Opening a row restarts the runtime at that cwd; removing a
 * row only forgets the registration — files are never touched. Changing a
 * directory moves the project's state to the renamed folder's key. */

import { useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'
import { lastProjectCwd, useAppStore } from '../../state/appStore'
import { useProjectStore, useSessionStore, useSettingsStore } from '../../state/stores'
import { useT } from '../../i18n/locale'
import { formatAge } from '../format'
import { pickProjectFolder } from '../HomeHero'
import { ChatIcon, FolderIcon, PlusIcon, XIcon } from '../icons'
import css from './ProjectSettings.module.css'

interface ProjectStat {
  sessions: number
  last: number
}

const EMPTY_STAT: ProjectStat = { sessions: 0, last: 0 }

/** The recorded project root for a registry entry: the containing session root
 * when the registered folder was a subdirectory of the repository. */
function rootFor(path: string, stats: Map<string, ProjectStat>): string {
  if (stats.has(path)) return path
  for (const root of stats.keys()) {
    if (path.startsWith(`${root}/`)) return root
  }
  return path
}

interface ProjectRowProps {
  path: string
  root: string
  name: string
  stat: ProjectStat
  active: boolean
}

function ProjectRow({ path, root, name, stat, active }: ProjectRowProps) {
  const t = useT()
  const locale = useSettingsStore((state) => state.locale)
  const removeProject = useProjectStore((state) => state.removeProject)
  const updateProject = useProjectStore((state) => state.updateProject)
  const openProject = useAppStore((state) => state.openProject)
  const relocateProject = useAppStore((state) => state.relocateProject)
  const newSession = useAppStore((state) => state.newSession)
  const setView = useSettingsStore((state) => state.setView)
  const [armed, setArmed] = useState(false)
  const [target, setTarget] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function open() {
    if (busy) return
    setBusy(true)
    try {
      await openProject(path)
      setView('chat')
    } finally {
      setBusy(false)
    }
  }

  async function startSession() {
    if (busy) return
    setBusy(true)
    try {
      await openProject(path)
      await newSession()
      setView('chat')
    } finally {
      setBusy(false)
    }
  }

  async function chooseTarget() {
    if (busy || active) return
    const picked = await pickProjectFolder()
    if (picked === null || picked === path) return
    setError(null)
    setTarget(picked)
  }

  async function relocate() {
    if (busy || target === null) return
    setBusy(true)
    setError(null)
    try {
      const frame = await relocateProject(root, target)
      if (frame.type === 'error') {
        setError(
          t('settings.projectRelocateFailed', {
            message: String(frame.message ?? ''),
          }),
        )
        return
      }
      updateProject(path, target)
      setTarget(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={css.project} data-project={path} data-current={active || undefined}>
      <span className={css.icon} aria-hidden>
        <FolderIcon size={14} />
      </span>
      <div className={css.copy}>
        <div className={css.name}>
          <span>{name}</span>
          {active ? <span className={css.current}>{t('settings.projectCurrent')}</span> : null}
        </div>
        <div className={css.path}>{path}</div>
        <div className={css.meta}>
          <span>{t('settings.projectSessions', { count: stat.sessions })}</span>
          {stat.last > 0 ? (
            <span>
              {t('settings.projectLastUsed', { when: formatAge(stat.last, locale) })}
            </span>
          ) : null}
        </div>
      </div>
      <div className={css.actions}>
        <button type="button" className={css.action} disabled={busy} onClick={() => void open()}>
          {active ? t('settings.projectOpenCurrent') : t('settings.projectOpen')}
        </button>
        <button
          type="button"
          className={css.action}
          disabled={busy}
          onClick={() => void startSession()}
        >
          <ChatIcon size={11} />
          <span>{t('settings.projectNewSession')}</span>
        </button>
        <button
          type="button"
          className={css.action}
          disabled={busy || active}
          title={active ? t('settings.projectRelocateActive') : undefined}
          onClick={() => void chooseTarget()}
        >
          <FolderIcon size={11} />
          <span>{t('settings.projectRelocate')}</span>
        </button>
        <button
          type="button"
          className={clsx(css.action, armed && css.actionDanger)}
          disabled={busy}
          onClick={() => (armed ? removeProject(path) : setArmed(true))}
        >
          <XIcon size={11} />
          <span>{armed ? t('settings.projectRemoveArmed') : t('settings.remove')}</span>
        </button>
      </div>
      {active ? <div className={css.hint}>{t('settings.projectRelocateActive')}</div> : null}
      {target !== null ? (
        <div className={css.confirm} data-project-confirm>
          <span className={css.confirmText}>
            {t('settings.projectRelocateConfirm', { count: stat.sessions, path: target })}
          </span>
          <button
            type="button"
            className={css.action}
            disabled={busy}
            onClick={() => setTarget(null)}
          >
            {t('settings.projectRelocateCancel')}
          </button>
          <button
            type="button"
            className={clsx(css.action, css.confirmAction)}
            disabled={busy}
            onClick={() => void relocate()}
          >
            {t('settings.projectRelocateAction')}
          </button>
        </div>
      ) : null}
      {error !== null ? <div className={css.error}>{error}</div> : null}
    </div>
  )
}

export function ProjectSettings() {
  const t = useT()
  const projects = useProjectStore((state) => state.projects)
  const addProject = useProjectStore((state) => state.addProject)
  const sessions = useSessionStore((state) => state.sessions)
  const refreshSessions = useAppStore((state) => state.refreshSessions)
  const current = useAppStore(() => lastProjectCwd())

  useEffect(() => {
    void refreshSessions()
  }, [refreshSessions])

  const stats = useMemo(() => {
    const map = new Map<string, ProjectStat>()
    for (const session of sessions) {
      const root = session.project_path ?? ''
      if (root === '') continue
      const previous = map.get(root) ?? { sessions: 0, last: 0 }
      map.set(root, {
        sessions: previous.sessions + 1,
        last: Math.max(previous.last, session.updated_at ?? 0),
      })
    }
    return map
  }, [sessions])

  const add = async () => {
    const path = await pickProjectFolder()
    if (path === null) return
    addProject(path)
  }

  return (
    <div className={css.stack}>
      <section className={css.cardBlock}>
        <div className={css.cardHeadline}>
          <div className={css.headCopy}>
            <h2 className={css.cardHeading}>{t('settings.projectsList')}</h2>
            <p className={css.cardCaption}>{t('settings.projectsListCaption')}</p>
          </div>
          <button type="button" className={css.addButton} onClick={() => void add()}>
            <PlusIcon size={12} />
            <span>{t('settings.projectAdd')}</span>
          </button>
        </div>
        <div className={css.panel}>
          {projects.length === 0 ? (
            <div className={css.empty}>{t('settings.projectsEmpty')}</div>
          ) : (
            projects.map((project) => {
              const root = rootFor(project.path, stats)
              return (
                <ProjectRow
                  key={project.path}
                  path={project.path}
                  root={root}
                  name={project.name}
                  stat={stats.get(root) ?? EMPTY_STAT}
                  active={current === project.path || current === root}
                />
              )
            })
          )}
        </div>
      </section>
    </div>
  )
}
