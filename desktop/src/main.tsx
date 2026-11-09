import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles/tokens.css'
import './styles/themes.css'
import './styles/base.css'
import { App } from './App'
import { ErrorBoundary } from './ErrorBoundary'

const container = document.getElementById('root')
if (container === null) throw new Error('missing #root')

// Uncaught async errors surface in the window title too (blank-window triage).
window.addEventListener('error', (event) => {
  document.title = `ERR ${event.message}`.slice(0, 200)
})
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason as { message?: string } | null
  document.title = `ERR ${reason?.message ?? String(event.reason)}`.slice(0, 200)
})

// A plain browser (dev server without the Tauri shell) has no bridge, so the
// scripted one loads first and the real shell boots against it. `DEV` folds to
// false in a production build, letting the bundler drop this whole branch and
// the dev chunk from the Tauri bundle.
async function bootstrap(root: HTMLElement): Promise<void> {
  if (import.meta.env.DEV && !window.__TAURI_INTERNALS__) {
    const { installBridge, initialState } = await import('./dev/bridge')
    installBridge(initialState())
  }
  createRoot(root).render(
    <React.StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </React.StrictMode>,
  )
}

void bootstrap(container)
