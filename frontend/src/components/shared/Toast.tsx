import { useEffect, useState } from 'react'
import { Check, AlertCircle, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

export type ToastType = 'success' | 'error' | 'info' | 'warning'

interface ToastProps {
  /**
   * Type of toast message
   */
  type?: ToastType

  /**
   * Main message text
   */
  message: string

  /**
   * Optional description or detailed message
   */
  description?: string

  /**
   * Action button label and callback
   */
  action?: {
    label: string
    onClick: () => void
  }

  /**
   * Auto-dismiss after milliseconds (0 = no auto-dismiss)
   */
  duration?: number

  /**
   * Callback when toast is dismissed
   */
  onDismiss?: () => void

  /**
   * Position on screen
   */
  position?: 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left' | 'top-center'

  /**
   * Additional CSS classes
   */
  className?: string
}

/**
 * Toast Component — Transient notification/feedback
 *
 * Shows temporary messages for success, errors, info, warnings.
 * Auto-dismisses after duration, or can be manually closed.
 *
 * @example
 * <Toast
 *   type="success"
 *   message="Project created successfully"
 *   duration={3000}
 * />
 *
 * <Toast
 *   type="error"
 *   message="Failed to delete"
 *   description="Unable to remove project"
 *   action={{ label: 'Retry', onClick: () => {} }}
 * />
 */
export function Toast({
  type = 'info',
  message,
  description,
  action,
  duration = 5000,
  onDismiss,
  position = 'bottom-right',
  className,
}: ToastProps) {
  const [isVisible, setIsVisible] = useState(true)

  useEffect(() => {
    if (duration === 0) return

    const timer = setTimeout(() => {
      setIsVisible(false)
      onDismiss?.()
    }, duration)

    return () => clearTimeout(timer)
  }, [duration, onDismiss])

  const handleDismiss = () => {
    setIsVisible(false)
    onDismiss?.()
  }

  if (!isVisible) return null

  const iconMap = {
    success: <Check className="w-5 h-5" />,
    error: <AlertCircle className="w-5 h-5" />,
    info: <Info className="w-5 h-5" />,
    warning: <AlertCircle className="w-5 h-5" />,
  }

  const colorMap = {
    success: 'bg-green-500/10 border-green-500/30 text-green-700 dark:text-green-400',
    error: 'bg-red-500/10 border-red-500/30 text-red-700 dark:text-red-400',
    info: 'bg-blue-500/10 border-blue-500/30 text-blue-700 dark:text-blue-400',
    warning: 'bg-yellow-500/10 border-yellow-500/30 text-yellow-700 dark:text-yellow-400',
  }

  const iconColorMap = {
    success: 'text-green-600 dark:text-green-400',
    error: 'text-red-600 dark:text-red-400',
    info: 'text-blue-600 dark:text-blue-400',
    warning: 'text-yellow-600 dark:text-yellow-400',
  }

  const positionMap = {
    'top-right': 'top-6 right-6',
    'top-left': 'top-6 left-6',
    'bottom-right': 'bottom-6 right-6',
    'bottom-left': 'bottom-6 left-6',
    'top-center': 'top-6 left-1/2 -translate-x-1/2',
  }

  return (
    <div
      className={cn(
        'fixed z-50 max-w-md animate-in fade-in slide-in-from-right',
        positionMap[position],
        className,
      )}
      role="alert"
      aria-live="polite"
    >
      <div
        className={cn(
          'rounded-lg border backdrop-blur-sm flex items-start gap-3 p-4 shadow-lg transition-all',
          colorMap[type],
        )}
      >
        {/* Icon */}
        <div className={cn('flex-shrink-0 mt-0.5', iconColorMap[type])}>
          {iconMap[type]}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <p className="font-medium text-sm">{message}</p>
          {description && (
            <p className="text-sm opacity-90 mt-1">{description}</p>
          )}
        </div>

        {/* Action */}
        {action && (
          <button
            onClick={action.onClick}
            className="flex-shrink-0 font-medium text-sm hover:underline"
          >
            {action.label}
          </button>
        )}

        {/* Dismiss Button */}
        <button
          onClick={handleDismiss}
          className="flex-shrink-0 opacity-70 hover:opacity-100 transition-opacity"
          aria-label="Close"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

/**
 * Toast Container — For managing multiple toasts
 */
interface ToastContainerProps {
  toasts: (ToastProps & { id: string })[]
  onDismiss: (id: string) => void
}

export function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  return (
    <div className="fixed inset-0 pointer-events-none z-50">
      <div className="fixed top-6 right-6 flex flex-col gap-3 max-w-md">
        {toasts.map((toast) => (
          <Toast
            key={toast.id}
            {...toast}
            onDismiss={() => onDismiss(toast.id)}
            position="top-right"
          />
        ))}
      </div>
    </div>
  )
}

/**
 * Hook for managing toast notifications
 */
export function useToast() {
  const [toasts, setToasts] = useState<(ToastProps & { id: string })[]>([])

  const addToast = (toast: ToastProps) => {
    const id = Math.random().toString(36).substr(2, 9)
    setToasts((prev) => [...prev, { ...toast, id }])
    return id
  }

  const removeToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }

  const success = (message: string, options?: Omit<ToastProps, 'type' | 'message'>) => {
    return addToast({ type: 'success', message, ...options })
  }

  const error = (message: string, options?: Omit<ToastProps, 'type' | 'message'>) => {
    return addToast({ type: 'error', message, ...options })
  }

  const info = (message: string, options?: Omit<ToastProps, 'type' | 'message'>) => {
    return addToast({ type: 'info', message, ...options })
  }

  const warning = (message: string, options?: Omit<ToastProps, 'type' | 'message'>) => {
    return addToast({ type: 'warning', message, ...options })
  }

  return {
    toasts,
    addToast,
    removeToast,
    success,
    error,
    info,
    warning,
  }
}

/**
 * Standalone success toast
 */
export function SuccessToast(props: Omit<ToastProps, 'type'>) {
  return <Toast type="success" {...props} />
}

/**
 * Standalone error toast
 */
export function ErrorToast(props: Omit<ToastProps, 'type'>) {
  return <Toast type="error" {...props} />
}

/**
 * Standalone info toast
 */
export function InfoToast(props: Omit<ToastProps, 'type'>) {
  return <Toast type="info" {...props} />
}

/**
 * Standalone warning toast
 */
export function WarningToast(props: Omit<ToastProps, 'type'>) {
  return <Toast type="warning" {...props} />
}
