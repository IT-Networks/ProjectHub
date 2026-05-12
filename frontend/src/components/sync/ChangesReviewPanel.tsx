import { useEffect, useMemo } from 'react'
import { GitPullRequest, Hammer, GitCommit, GitMerge, FileCode, Ticket, MessageSquare, Sparkles, Check, X, RefreshCw, ExternalLink } from 'lucide-react'
import { useProjectSyncStore, type SourceChange } from '@/stores/projectSyncStore'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { EmptyState } from '@/components/shared/EmptyState'
import { cn } from '@/lib/utils'

interface Props {
  projectId: string
}

const ICONS: Record<SourceChange['source_type'], typeof GitPullRequest> = {
  pr: GitPullRequest,
  build: Hammer,
  commit: GitCommit,
  commit_batch: GitMerge,
  codebase_baseline: FileCode,
  jira: Ticket,
  jira_comment: MessageSquare,
  pr_comment: MessageSquare,
}

const TYPE_LABEL: Record<SourceChange['source_type'], string> = {
  pr: 'Pull Request',
  build: 'Build',
  commit: 'Commit',
  commit_batch: 'Commit-Batch',
  codebase_baseline: 'Codebase-Baseline',
  jira: 'Jira-Ticket',
  jira_comment: 'Jira-Kommentar',
  pr_comment: 'PR-Kommentar',
}

const RELEVANCE_COLOR: Record<string, string> = {
  core: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  related: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  irrelevant: 'bg-muted text-muted-foreground border-border',
}

export function ChangesReviewPanel({ projectId }: Props) {
  const changes = useProjectSyncStore((s) => s.changesByProject[projectId])
  const fetchChanges = useProjectSyncStore((s) => s.fetchChanges)
  const analyzePending = useProjectSyncStore((s) => s.analyzePending)
  const acceptChange = useProjectSyncStore((s) => s.acceptChange)
  const dismissChange = useProjectSyncStore((s) => s.dismissChange)
  const triggerSync = useProjectSyncStore((s) => s.triggerSync)
  const syncStatus = useProjectSyncStore((s) => s.statusByProject[projectId])

  useEffect(() => {
    fetchChanges(projectId)
  }, [projectId, fetchChanges])

  const grouped = useMemo(() => {
    const byType = new Map<SourceChange['source_type'], SourceChange[]>()
    for (const c of changes ?? []) {
      if (!byType.has(c.source_type)) byType.set(c.source_type, [])
      byType.get(c.source_type)!.push(c)
    }
    return Array.from(byType.entries()).sort((a, b) => a[0].localeCompare(b[0]))
  }, [changes])

  const pendingCount = (changes ?? []).filter((c) => c.analysis_status === 'pending').length
  const analyzedCount = (changes ?? []).filter((c) => c.analysis_status === 'analyzed').length

  if (!changes || changes.length === 0) {
    return (
      <EmptyState
        icon="🔄"
        title="Keine offenen Änderungen"
        description={
          syncStatus?.sources.length
            ? 'Beim nächsten Sync werden neue Änderungen hier auftauchen.'
            : 'Dieses Projekt hat noch keine verknüpften Datenquellen. Füge eine Quelle hinzu, damit Änderungen importiert werden können.'
        }
        action={
          <Button size="sm" onClick={() => triggerSync(projectId, 'manual', true)}>
            <RefreshCw className="h-3.5 w-3.5 mr-1.5" /> Jetzt prüfen
          </Button>
        }
      />
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 rounded-md border border-border bg-card px-3 py-2 text-xs">
        <div className="text-muted-foreground">
          {changes.length} Änderung(en) · <span className="text-brand">{pendingCount}</span> ausstehend · <span className="text-foreground">{analyzedCount}</span> analysiert
        </div>
        <div className="flex gap-2">
          {pendingCount > 0 && (
            <Button size="sm" variant="outline" onClick={() => analyzePending(projectId)} className="gap-1.5">
              <Sparkles className="h-3 w-3" />
              Alle analysieren
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => triggerSync(projectId, 'manual', true)} className="gap-1.5">
            <RefreshCw className="h-3 w-3" />
            Sync
          </Button>
        </div>
      </div>

      {grouped.map(([type, items]) => {
        const Icon = ICONS[type]
        return (
          <section key={type} className="space-y-1.5">
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
              <Icon className="h-3.5 w-3.5" />
              <span>{TYPE_LABEL[type]} ({items.length})</span>
            </div>
            <ul className="space-y-2" role="list">
              {items.map((c) => (
                <ChangeRow key={c.id} change={c}
                  onAccept={() => acceptChange(projectId, c.id)}
                  onDismiss={() => dismissChange(projectId, c.id)}
                />
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}


function ChangeRow({
  change,
  onAccept,
  onDismiss,
}: {
  change: SourceChange
  onAccept: () => void
  onDismiss: () => void
}) {
  const a = change.analysis
  const htmlUrl = (change as unknown as { payload?: { html_url?: string } }).payload?.html_url

  return (
    <li className="rounded-md border border-border bg-card p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{a?.title || change.title || change.external_ref}</span>
            {a && (
              <Badge variant="outline" className={cn('text-[10px]', RELEVANCE_COLOR[a.relevance])}>
                {a.relevance}
              </Badge>
            )}
            {change.analysis_status === 'accepted' && (
              <Badge variant="outline" className="text-[10px] text-emerald-400 border-emerald-500/30">✓ im Wissen</Badge>
            )}
            {change.analysis_status === 'pending' && (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">noch nicht analysiert</Badge>
            )}
            {change.analysis_status === 'error' && (
              <Badge variant="outline" className="text-[10px] text-red-400 border-red-500/30">Fehler</Badge>
            )}
            {change.auto_accepted && (
              <Badge variant="outline" className="text-[10px] text-brand">auto</Badge>
            )}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            <span className="tabular-nums">{change.external_ref}</span>
            {a && <span> · Konfidenz {Math.round(a.confidence * 100)}%</span>}
          </p>
          {a && (
            <>
              <p className="mt-2 text-sm">{a.summary}</p>
              <p className="mt-1 text-xs italic text-muted-foreground">{a.reason}</p>
              {a.tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {a.tags.map((t) => (
                    <Badge key={t} variant="secondary" className="text-[10px] px-1.5 py-0">{t}</Badge>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
        <div className="flex shrink-0 flex-col gap-1">
          {htmlUrl && (
            <a
              href={htmlUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex h-7 items-center gap-1 rounded border border-border px-2 text-[11px] text-muted-foreground hover:bg-accent"
            >
              <ExternalLink className="h-3 w-3" /> Öffnen
            </a>
          )}
          {change.analysis_status !== 'accepted' && change.analysis_status !== 'dismissed' && (
            <>
              <Button size="sm" variant="default" onClick={onAccept} className="h-7 gap-1 px-2 text-[11px]">
                <Check className="h-3 w-3" /> Übernehmen
              </Button>
              <Button size="sm" variant="ghost" onClick={onDismiss} className="h-7 gap-1 px-2 text-[11px] text-muted-foreground">
                <X className="h-3 w-3" /> Verwerfen
              </Button>
            </>
          )}
        </div>
      </div>
    </li>
  )
}
