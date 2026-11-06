import React from 'react'
import { createRoot } from 'react-dom/client'
import './styles/tokens.css'
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

createRoot(container).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)
