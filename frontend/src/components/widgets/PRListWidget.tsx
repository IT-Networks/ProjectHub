import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { useSSEEvent } from '@/hooks/useSSE'
import { Badge } from '@/components/ui/badge'

interface RepoInfo {
  owner: string
  repo: string
  display_name: string
  open_issues_count: number
  updated_at: string
}

interface Props {
  config: Record<string, unknown>
}

export function PRListWidget({ config }: Props) {
  const [repos, setRepos] = useState<RepoInfo[]>([])
  const [connected, setConnected] = useState(true)

  const load = async () => {
    try {
      const url = config.project_id ? `/pulls/${config.project_id}` : '/pulls'
      const data = await api.get<{ repos: RepoInfo[]; connected: boolean }>(url)
      setRepos(data.repos)
      setConnected(data.connected)
    } catch { /* offline */ }
  }

  useEffect(() => { load() }, [config.project_id])

  useSSEEvent('pr_update', () => { load() })

  if (repos.length === 0) {
    return <p className="text-sm text-muted-foreground">Keine GitHub-Repos verknüpft</p>
  }

  return (
    <div className="space-y-2">
      {!connected && (
        <p className="text-xs text-yellow-400">(cached)</p>
      )}
      {repos.map((r) => (
        <div key={`${r.owner}/${r.repo}`} className="flex items-center justify-between rounded border border-border px-3 py-2">
          <span className="text-sm">{r.display_name}</span>
          <Badge variant={r.open_issues_count > 0 ? 'default' : 'secondary'} className="text-xs">
            {r.open_issues_count} offen
          </Badge>
        </div>
      ))}
    </div>
  )
}
