import { useCallback } from 'react'
import { Check } from 'lucide-react'
import { cn } from '@/lib/utils'

interface CheckboxProps {
  checked: boolean
  indeterminate?: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  className?: string
  ariaLabel?: string
}

export function Checkbox({
  checked,
  indeterminate = false,
  onChange,
  disabled = false,
  className,
  ariaLabel,
}: CheckboxProps) {
  const handleClick = useCallback(() => {
    if (!disabled) {
      onChange(!checked)
    }
  }, [checked, disabled, onChange])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if ((e.key === ' ' || e.key === 'Enter') && !disabled) {
        e.preventDefault()
        onChange(!checked)
      }
    },
    [checked, disabled, onChange]
  )

  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={indeterminate ? 'mixed' : checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      className={cn(
        'inline-flex h-4 w-4 items-center justify-center rounded border transition-colors',
        checked || indeterminate
          ? 'border-primary bg-primary text-primary-foreground'
          : 'border-input bg-background hover:border-primary/50',
        disabled && 'opacity-50 cursor-not-allowed',
        className
      )}
    >
      {(checked || indeterminate) && (
        <Check className="h-3 w-3 stroke-2" />
      )}
      {indeterminate && !checked && (
        <div className="h-0.5 w-2 bg-current" />
      )}
    </button>
  )
}
