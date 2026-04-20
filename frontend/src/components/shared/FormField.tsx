import { ReactNode } from 'react'
import { AlertCircle, Check } from 'lucide-react'
import { cn } from '@/lib/utils'

interface FormFieldProps {
  label?: string
  error?: string
  success?: boolean
  loading?: boolean
  children: ReactNode
  className?: string
}

export function FormField({
  label,
  error,
  success,
  loading,
  children,
  className,
}: FormFieldProps) {
  return (
    <div className={cn('space-y-1', className)}>
      {label && (
        <label className="block text-sm font-medium">{label}</label>
      )}
      {children}
      {error && (
        <div className="flex items-center gap-1">
          <AlertCircle className="h-3 w-3 text-destructive" />
          <p className="text-xs text-destructive">{error}</p>
        </div>
      )}
      {!error && success && (
        <div className="flex items-center gap-1">
          <Check className="h-3 w-3 text-green-600 dark:text-green-500" />
          <p className="text-xs text-green-600 dark:text-green-500">Gültig</p>
        </div>
      )}
      {loading && !error && (
        <p className="text-xs text-muted-foreground">Prüfung läuft...</p>
      )}
    </div>
  )
}
