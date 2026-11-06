/** Idle host: before the first connection the home hero asks for a project.
 * The zen rpc subprocess runs with `--cwd` scoped to the picked folder. */

import { useState } from 'react'
import { HomeHero, pickProjectFolder } from './HomeHero'
import { loadRecentProjects } from '../state/appStore'
import css from './WelcomeScreen.module.css'

export function WelcomeScreen({ onStart }: { onStart(cwd: string | undefined): void }) {
  const [recents] = useState(loadRecentProjects)

  const onPick = async () => {
    const path = await pickProjectFolder()
    if (path !== null) onStart(path)
  }

  return (
    <div className={css.root}>
      <HomeHero
        connected={false}
        projectName={null}
        recents={recents}
        onPickFolder={() => void onPick()}
        onUseCurrent={() => onStart(undefined)}
        onOpenRecent={(path) => onStart(path)}
      />
    </div>
  )
}
