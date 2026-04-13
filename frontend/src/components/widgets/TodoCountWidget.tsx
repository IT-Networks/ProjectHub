import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { Todo } from '@/lib/types'

interface Props {
  config: Record<string, unknown>
}

export function TodoCountWidget({ config }: Props) {
  const [counts, setCounts] = useState({ backlog: 0, in_progress: 0, review: 0, done: 0 })

  useEffect(() => {
    const load = async () => {
      const params = config.project_id ? `?project_id=${config.project_id}` : ''
      const todos = await api.get<Todo[]>(`/todos${params}`)
      const c = { backlog: 0, in_progress: 0, review: 0, done: 0 }
      for (const t of todos) {
        if (t.status in c) c[t.status as keyof typeof c]++
      }
      setCounts(c)
    }
    load()
    const interval = setInterval(load, 30000)
    return () => clearInterval(interval)
  }, [config.project_id])

  const total = counts.backlog + counts.in_progress + counts.review + counts.done
  const donePercent = total > 0 ? Math.round((counts.done / total) * 100) : 0

  return (
    <div>
      <div className="mb-3 flex items-end gap-2">
        <span className="text-3xl font-bold">{total - counts.done}</span>
        <span className="pb-1 text-sm text-muted-foreground">offen</span>
      </div>
      {/* Progress bar */}
      <div className="mb-2 h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-green-500 transition-all" style={{ width: `${donePercent}%` }} />
      </div>
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>Backlog: {counts.backlog}</span>
        <span>In Arbeit: {counts.in_progress}</span>
        <span>Review: {counts.review}</span>
        <span>Erledigt: {counts.done}</span>
      </div>
    </div>
  )
}
