import { Component, type ErrorInfo, type ReactNode } from 'react'
import { reportClientError } from '../lib/errorReporting'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
}

/**
 * Catches render-time errors that `window.onerror` cannot see (React
 * unmounts the failing subtree before that global handler would fire),
 * reports them the same way as `installGlobalErrorReporting` does, and
 * shows a minimal fallback instead of a blank page.
 *
 * Deliberately a class component: this is the only way to implement
 * `getDerivedStateFromError` / `componentDidCatch` in React today.
 */
class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    reportClientError({
      message: error.message,
      stack: error.stack ?? info.componentStack ?? undefined,
      source: 'react.ErrorBoundary',
    })
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div role="alert" className="error-boundary-fallback">
          <p>予期しないエラーが発生しました。ページを再読み込みしてください。</p>
        </div>
      )
    }

    return this.props.children
  }
}

export default ErrorBoundary
