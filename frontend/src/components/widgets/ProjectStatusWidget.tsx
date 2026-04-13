import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { STATUS_LABELS } from '@/lib/types'
import type { Project } from '@/lib/types'

interface Props {
  config: Record<string, unknown>
}

export function ProjectStatusWidget({ config }: Props) {
  const [project, setProject] = useState<Project | null>(null)

  useEffect(() => {
    if (!config.project_id) return
    api.get<Project>(`/projects/${config.project_id}`).then(setProject).catch(() => {})
  }, [config.project_id])

  if (!project) return <p className="text-sm text-muted-foreground">Projekt wählen...</p>

  const total = project.counts.todos_open + project.counts.todos_done
  const progress = total > 0 ? Math.round((project.counts.todos_done / total) * 100) : 0

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span className="h-3 w-3 rounded-full" style={{ backgroundColor: project.color }} />
        <span className="text-sm font-medium">{project.name}</span>
        <Badge variant="secondary" className="text-xs">{STATUS_LABELS[project.status]}</Badge>
      </div>
      <div className="mb-1 h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progress}%` }} />
      </div>
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{project.counts.todos_open} offen</span>
        <span>{project.counts.todos_done} erledigt</span>
        <span>{project.counts.notes} Notizen</span>
        <span>{project.sources.length} Quellen</span>
      </div>
    </div>
  )
}
