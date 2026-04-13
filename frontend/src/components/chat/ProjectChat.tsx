import { useState, useRef, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { useIsOffline } from '@/hooks/useOffline'
import { cn } from '@/lib/utils'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

interface Props {
  projectId: string
  projectName: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ProjectChat({ projectId, projectName, open, onOpenChange }: Props) {
  const isOffline = useIsOffline()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  // Load history on open
  useEffect(() => {
    if (!open) return
    fetch(`/api/chat/history/projecthub-${projectId}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.history) {
          setMessages(data.history.filter((m: ChatMessage) => m.role === 'user' || m.role === 'assistant'))
        }
      })
      .catch(() => {})
  }, [open, projectId])

  // Auto-scroll
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, streamingText])

  const handleSend = async () => {
    if (!input.trim() || streaming) return

    const userMsg = input.trim()
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: userMsg }])
    setStreaming(true)
    setStreamingText('')

    try {
      const response = await fetch(`/api/chat/project/${projectId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg, include_sources: true }),
      })

      const reader = response.body?.getReader()
      const decoder = new TextDecoder()
      let fullText = ''

      if (reader) {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          const chunk = decoder.decode(value, { stream: true })
          const lines = chunk.split('\n')

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6))
                if (data.token) {
                  fullText += data.token
                  setStreamingText(fullText)
                }
                if (data.done && data.full_response) {
                  fullText = data.full_response
                }
                if (data.error) {
                  fullText = `Fehler: ${data.error}`
                }
              } catch { /* skip non-JSON lines */ }
            }
          }
        }
      }

      setMessages((prev) => [...prev, { role: 'assistant', content: fullText }])
    } catch (e) {
      setMessages((prev) => [...prev, { role: 'assistant', content: `Fehler: ${(e as Error).message}` }])
    }

    setStreaming(false)
    setStreamingText('')
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-[440px] flex-col p-0 sm:max-w-[440px]">
        <SheetHeader className="border-b border-border px-4 py-3">
          <SheetTitle className="text-sm">Chat — {projectName}</SheetTitle>
        </SheetHeader>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={cn(
                'rounded-lg px-3 py-2 text-sm',
                msg.role === 'user'
                  ? 'ml-8 bg-primary text-primary-foreground'
                  : 'mr-8 bg-muted'
              )}
            >
              <div className="prose prose-sm prose-invert max-w-none whitespace-pre-wrap">
                {msg.content}
              </div>
            </div>
          ))}
          {streaming && streamingText && (
            <div className="mr-8 rounded-lg bg-muted px-3 py-2 text-sm">
              <div className="prose prose-sm prose-invert max-w-none whitespace-pre-wrap">
                {streamingText}
                <span className="animate-pulse">|</span>
              </div>
            </div>
          )}
          {messages.length === 0 && !streaming && (
            <p className="py-8 text-center text-xs text-muted-foreground">
              Stelle eine Frage zum Projekt. Der Kontext (Todos, Notizen, Builds) wird automatisch mitgesendet.
            </p>
          )}
        </div>

        {/* Input */}
        <div className="border-t border-border p-3">
          {isOffline && (
            <p className="mb-2 text-xs text-yellow-400">Chat benötigt AI-Assist Verbindung</p>
          )}
          <div className="flex gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault()
                  handleSend()
                }
              }}
              placeholder="Frage zum Projekt... (Ctrl+Enter)"
              rows={2}
              className="flex-1 resize-none"
              disabled={isOffline || streaming}
            />
            <Button onClick={handleSend} disabled={isOffline || streaming || !input.trim()} className="self-end">
              Senden
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}
