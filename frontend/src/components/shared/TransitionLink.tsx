import { forwardRef, type MouseEvent, type AnchorHTMLAttributes, type ReactNode } from 'react'
import { useViewTransitionNavigate } from '@/hooks/useViewTransition'

interface Props extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  to: string
  replace?: boolean
  children: ReactNode
}

export const TransitionLink = forwardRef<HTMLAnchorElement, Props>(function TransitionLink(
  { to, replace, onClick, children, ...rest },
  ref,
) {
  const navigate = useViewTransitionNavigate()

  const handleClick = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e)
    if (e.defaultPrevented) return
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    navigate(to, { replace })
  }

  return (
    <a ref={ref} href={to} onClick={handleClick} {...rest}>
      {children}
    </a>
  )
})
