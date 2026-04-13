import { useState } from 'react'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useIsOffline } from '@/hooks/useOffline'
import { cn } from '@/lib/utils'

interface PRReviewResult {
  prNumber: number
  verdict: string
  summary: string
  bySeverity: Record<string, number>
  findings: { severity: string; title: string; file: string; line?: number; description: string }[]
  canApprove: boolean
}

interface Props {
  projectId: string
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red-600 text-white',
  high: 'bg-red-500/20 text-red-400',
  medium: 'bg-yellow-500/20 text-yellow-400',
  low: 'bg-blue-500/20 text-blue-400',
  info: 'bg-muted text-muted-foreground',
}

export function PRReviewPanel({ projectId }: Props) {
  const isOffline = useIsOffline()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [owner, setOwner] = useState('')
  const [repo, setRepo] = useState('')
  const [prNumber, setPrNumber] = useState('')
  const [reviewing, setReviewing] = useState(false)
  const [reviewResult, setReviewResult] = useState<PRReviewResult | null>(null)
  const [diff, setDiff] = useState<string | null>(null)
  const [loadingDiff, setLoadingDiff] = useState(false)
  const [error, setError] = useState('')

  const handleLoadDiff = async () => {
    if (!owner || !repo || !prNumber) return
    setLoadingDiff(true)
    try {
      const data = await api.get<{ diff: string }>(`/pulls/diff/${owner}/${repo}/${prNumber}`)
      setDiff(data.diff || 'Kein Diff verfügbar')
    } catch {
      setDiff('Fehler beim Laden des Diffs')
    }
    setLoadingDiff(false)
  }

  const handleReview = async () => {
    if (!owner || !repo || !prNumber) return
    setReviewing(true)
    setError('')
    try {
      const data = await api.post<PRReviewResult>(`/pulls/review/${owner}/${repo}/${prNumber}`)
      setReviewResult(data)
    } catch (e) {
      setReviewResult(null)
      setError((e as Error).message || 'Review fehlgeschlagen — ist AI-Assist erreichbar?')
    }
    setReviewing(false)
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <span className="text-sm text-muted-foreground">Pull Request Review</span>
        <Button size="sm" onClick={() => setDialogOpen(true)} disabled={isOffline}>
          PR reviewen
        </Button>
      </div>

      <p className="text-sm text-muted-foreground">
        Wähle einen PR aus einem verknüpften GitHub-Repo zum automatischen LLM-Review.
      </p>

      {/* Review Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>PR Review</DialogTitle>
          </DialogHeader>

          <div className="space-y-4 py-2">
            {/* PR Identifier */}
            <div className="flex gap-2">
              <Input placeholder="Owner" value={owner} onChange={(e) => setOwner(e.target.value)} className="w-32" />
              <Input placeholder="Repo" value={repo} onChange={(e) => setRepo(e.target.value)} className="w-40" />
              <Input placeholder="PR #" value={prNumber} onChange={(e) => setPrNumber(e.target.value)} className="w-24" type="number" />
              <Button variant="outline" onClick={handleLoadDiff} disabled={loadingDiff || !owner || !repo || !prNumber}>
                {loadingDiff ? 'Laden...' : 'Diff laden'}
              </Button>
              <Button onClick={handleReview} disabled={reviewing || !owner || !repo || !prNumber}>
                {reviewing ? 'Analysiert...' : 'LLM Review'}
              </Button>
            </div>

            {error && (
              <div className="rounded-md border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-400" role="alert">
                {error}
              </div>
            )}

            {/* Diff viewer */}
            {diff && (
              <div className="max-h-[300px] overflow-auto rounded bg-muted/50 p-3">
                <pre className="text-xs leading-relaxed">
                  {diff.split('\n').map((line, i) => (
                    <div
                      key={i}
                      className={cn(
                        line.startsWith('+') && !line.startsWith('+++') ? 'text-green-400 bg-green-500/10' :
                        line.startsWith('-') && !line.startsWith('---') ? 'text-red-400 bg-red-500/10' :
                        line.startsWith('@@') ? 'text-blue-400' : ''
                      )}
                    >
                      {line}
                    </div>
                  ))}
                </pre>
              </div>
            )}

            {/* Review Result */}
            {reviewResult && (
              <div className="space-y-3">
                <div className="flex items-center gap-3">
                  <Badge className={cn('text-sm', {
                    'bg-green-600': reviewResult.verdict === 'approve',
                    'bg-red-600': reviewResult.verdict === 'request_changes',
                    'bg-yellow-600': reviewResult.verdict === 'comment',
                  })}>
                    {reviewResult.verdict === 'approve' ? 'Genehmigt' :
                     reviewResult.verdict === 'request_changes' ? 'Änderungen nötig' : 'Kommentar'}
                  </Badge>
                  {Object.entries(reviewResult.bySeverity || {}).filter(([,v]) => v > 0).map(([sev, count]) => (
                    <Badge key={sev} variant="outline" className={cn('text-xs', SEVERITY_COLORS[sev])}>
                      {sev}: {count}
                    </Badge>
                  ))}
                </div>

                <p className="text-sm">{reviewResult.summary}</p>

                <div className="space-y-2">
                  {(reviewResult.findings || []).map((f, i) => (
                    <Card key={i} className="p-3">
                      <div className="flex items-center gap-2 mb-1">
                        <Badge className={cn('text-xs', SEVERITY_COLORS[f.severity])}>{f.severity}</Badge>
                        <span className="text-sm font-medium">{f.title}</span>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {f.file}{f.line ? `:${f.line}` : ''}
                      </p>
                      <p className="mt-1 text-sm">{f.description}</p>
                    </Card>
                  ))}
                </div>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
