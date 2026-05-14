import { useState } from 'react'
import { useSynapseStore } from '@/stores/synapseStore'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import type { AskResponse } from '@/lib/types'

interface GlobalAskBoxProps {
  projectId: string
}

/**
 * Korpus-weite Frage an das validierte Synapsen-Wissen — eine
 * "globale Suche": die Antwort wird über die Synapsen synthetisiert,
 * mit Verweis auf die genutzten Wissens-Knoten.
 */
export function GlobalAskBox({ projectId }: GlobalAskBoxProps) {
  const ask = useSynapseStore((s) => s.ask)

  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<AskResponse | null>(null)

  const handleAsk = async () => {
    if (!question.trim() || loading) return
    setLoading(true)
    setResult(null)
    try {
      const res = await ask(projectId, question.trim())
      setResult(res)
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Cmd/Ctrl+Enter submits — Enter alone keeps newlines for longer questions.
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      void handleAsk()
    }
  }

  return (
    <div className="mb-4 rounded-md border border-border bg-muted/30 p-3">
      <div className="flex items-start gap-2">
        <Textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Frage ans Projektwissen stellen… (z.B. „Was sind die zentralen Risiken?“)"
          rows={2}
          className="flex-1 resize-none text-sm"
        />
        <Button
          size="sm"
          onClick={handleAsk}
          disabled={loading || !question.trim()}
        >
          {loading ? 'Denkt…' : 'Fragen'}
        </Button>
      </div>

      {result && (
        <div className="mt-3 rounded border border-border bg-card p-3 text-sm">
          <p className="whitespace-pre-wrap">{result.answer}</p>
          {result.sources.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1 border-t border-border pt-2">
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                Quellen:
              </span>
              {result.sources.map((src) => (
                <Badge
                  key={src.synapse_id}
                  variant="secondary"
                  className="text-[10px]"
                  title={`Konfidenz ${Math.round(src.confidence * 100)}%`}
                >
                  {src.title}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
