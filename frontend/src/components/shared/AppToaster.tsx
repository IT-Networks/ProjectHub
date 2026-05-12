import { Toast } from './Toast'
import { useToastStore } from '@/stores/toastStore'

export function AppToaster() {
  const toasts = useToastStore((s) => s.toasts)
  const dismiss = useToastStore((s) => s.dismiss)

  return (
    <div className="fixed inset-0 pointer-events-none z-50">
      <div className="fixed top-6 right-6 flex flex-col gap-3 max-w-md">
        {toasts.map((t) => (
          <Toast
            key={t.id}
            type={t.type}
            message={t.message}
            description={t.description}
            action={t.action}
            duration={t.duration}
            onDismiss={() => dismiss(t.id)}
            position="top-right"
          />
        ))}
      </div>
    </div>
  )
}
