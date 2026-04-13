import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ConfirmDialog } from './ConfirmDialog'
import type { LinkedMessage } from '@/lib/types'

interface Props {
  targetType: 'project' | 'todo' | 'note'
  targetId: string
}

export function LinkedMessageList({ targetType, targetId }: Props) {
  const [messages, setMessages] = useState<LinkedMessage[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [unlinkingId, setUnlinkingId] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.get<LinkedMessage[]>(`/inbox/links?link_target=${targetType}&target_id=${targetId}`)
      setMessages(data)
    } catch (e) {
      setMessages([])
      setError((e as Error).message || 'Fehler beim Laden')
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [targetType, targetId])

  const handleUnlink = async (linkId: string) => {
    await api.del(`/inbox/link/${linkId}`)
    await load()
  }

  if (loading) return <p className="text-sm text-muted-foreground">Laden...</p>

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <span className="text-sm text-muted-foreground">{messages.length} verlinkte Nachrichten</span>
      </div>

      {error && (
        <div className="mb-3 rounded-md border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-400" role="alert">
          {error}
        </div>
      )}

      <div className="space-y-2">
        {messages.map((msg) => (
          <Card key={msg.id} className="group flex items-center justify-between p-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-xs">
                  {msg.source === 'email' ? 'Email' : 'Webex'}
                </Badge>
                <span className="truncate text-sm font-medium">{msg.subject || '(Kein Betreff)'}</span>
              </div>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {msg.sender} — {msg.date ? new Date(msg.date).toLocaleDateString('de-DE') : ''}
              </p>
              {msg.snippet && (
                <p className="mt-1 text-xs text-muted-foreground line-clamp-1">{msg.snippet}</p>
              )}
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="opacity-0 group-hover:opacity-100"
              onClick={() => setUnlinkingId(msg.id)}
            >
              Entfernen
            </Button>
          </Card>
        ))}

        {messages.length === 0 && (
          <p className="py-8 text-center text-sm text-muted-foreground">
            Keine Nachrichten verlinkt. Verknüpfe Emails oder Webex-Nachrichten über die Inbox.
          </p>
        )}
      </div>

      <ConfirmDialog
        open={!!unlinkingId}
        onOpenChange={() => setUnlinkingId(null)}
        title="Verlinkung entfernen"
        description="Die Verknüpfung zwischen Nachricht und diesem Element wird entfernt."
        confirmLabel="Entfernen"
        onConfirm={() => {
          if (unlinkingId) handleUnlink(unlinkingId)
          setUnlinkingId(null)
        }}
      />
    </div>
  )
}
