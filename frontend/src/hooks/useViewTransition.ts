import { useCallback } from 'react'
import { flushSync } from 'react-dom'
import { useNavigate, type NavigateOptions } from 'react-router-dom'

type StartViewTransition = (cb: () => void) => { finished: Promise<void> } | void

function supportsViewTransitions(): boolean {
  return typeof document !== 'undefined' && typeof (document as Document & { startViewTransition?: StartViewTransition }).startViewTransition === 'function'
}

export function useViewTransitionNavigate() {
  const navigate = useNavigate()

  return useCallback(
    (to: string, options?: NavigateOptions) => {
      const doc = document as Document & { startViewTransition?: StartViewTransition }
      if (supportsViewTransitions() && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        doc.startViewTransition!(() => {
          flushSync(() => navigate(to, options))
        })
      } else {
        navigate(to, options)
      }
    },
    [navigate],
  )
}

export function useStartViewTransition() {
  return useCallback((update: () => void) => {
    const doc = document as Document & { startViewTransition?: StartViewTransition }
    if (supportsViewTransitions() && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      doc.startViewTransition!(() => {
        flushSync(update)
      })
    } else {
      update()
    }
  }, [])
}
