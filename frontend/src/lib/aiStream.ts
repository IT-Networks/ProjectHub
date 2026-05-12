export type AiStreamEvent =
  | { type: 'token'; token: string }
  | { type: 'done' }
  | { type: 'error'; error: string }

export interface AiStreamRequest {
  mode: 'continue' | 'summarize' | 'improve' | 'shorten' | 'expand' | 'fix_grammar' | 'custom'
  text: string
  prompt?: string
  context?: string
}

export async function streamAiGenerate(
  body: AiStreamRequest,
  onEvent: (evt: AiStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/ai/generate/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok || !res.body) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(detail || 'Stream konnte nicht geöffnet werden')
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = 'message'

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let newlineIdx: number
    while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newlineIdx).replace(/\r$/, '')
      buffer = buffer.slice(newlineIdx + 1)

      if (line === '') {
        currentEvent = 'message'
        continue
      }
      if (line.startsWith(':')) continue
      if (line.startsWith('event:')) {
        currentEvent = line.slice(6).trim()
        continue
      }
      if (line.startsWith('data:')) {
        const data = line.slice(5).trim()
        if (!data) continue
        try {
          const parsed = JSON.parse(data)
          if (currentEvent === 'token' && typeof parsed.token === 'string') {
            onEvent({ type: 'token', token: parsed.token })
          } else if (currentEvent === 'done') {
            onEvent({ type: 'done' })
          } else if (currentEvent === 'error') {
            onEvent({ type: 'error', error: parsed.error ?? 'Unbekannter Fehler' })
          }
        } catch {
          // Non-JSON data line, ignore
        }
      }
    }
  }
}
