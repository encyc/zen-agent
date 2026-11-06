/** Top-level error boundary: a crashed render shows the error instead of a
 * blank window, and mirrors the message into document.title so the failure is
 * visible from outside (window lists, diagnostics). */

import { Component, type ErrorInfo, type ReactNode } from 'react'

interface BoundaryState {
  error: Error | null
  info: string
}

export class ErrorBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = { error: null, info: '' }

  static getDerivedStateFromError(error: Error): Partial<BoundaryState> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.setState({ info: info.componentStack ?? '' })
    document.title = `ERR ${error.name}: ${error.message}`.slice(0, 200)
  }

  render(): ReactNode {
    const { error, info } = this.state
    if (error !== null) {
      return (
        <div
          style={{
            height: '100%',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 12,
            padding: 24,
            fontFamily: 'var(--dsw-font-family, -apple-system, sans-serif)',
            background: 'var(--dsw-alias-bg-base, #fff)',
            color: 'var(--dsw-alias-label-primary, #111)',
          }}
        >
          <div style={{ fontSize: 15, fontWeight: 600 }}>
            界面遇到一个错误 / The UI hit an error
          </div>
          <pre
            style={{
              margin: 0,
              maxWidth: 640,
              maxHeight: 240,
              overflow: 'auto',
              padding: 12,
              borderRadius: 8,
              background: 'var(--dsw-alias-markdown-code-block, #f6f6f6)',
              fontSize: 12,
              lineHeight: '17px',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {`${error.name}: ${error.message}\n${info}`}
          </pre>
          <button
            type="button"
            onClick={() => {
              this.setState({ error: null, info: '' })
              document.title = 'ZenAgent'
            }}
            style={{
              height: 30,
              padding: '0 16px',
              border: 'none',
              borderRadius: 8,
              background: 'var(--dsw-alias-button-info-fill, #4176e6)',
              color: '#fff',
              fontSize: 13,
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            重试 / Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
