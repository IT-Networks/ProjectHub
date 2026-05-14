import { useEffect, useMemo, useState } from 'react'
import { Brain, Link as LinkIcon, Paperclip, RefreshCw, Search } from 'lucide-react'
import {
  useInboxStore,
  type EmailResult,
} from '@/stores/inboxStore'
import { useProjectStore } from '@/stores/projectStore'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useIsOffline } from '@/hooks/useOffline'
import { api, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { EmptyState } from '@/components/shared/EmptyState'
import { ListSkeleton } from '@/components/shared/Skeleton'
import { useToast, ToastContainer } from '@/components/shared/Toast'

type KnowledgeDialogState = {
  source: 'email' | 'webex'
  externalId: string
  subject: string
  sender: string
  content: string
} | null

// ── Darstellungs-Helfer ───────────────────────────────────────────────────────

const AVATAR_COLORS = [
  'bg-sky-500/15 text-sky-400',
  'bg-violet-500/15 text-violet-400',
  'bg-emerald-500/15 text-emerald-400',
  'bg-amber-500/15 text-amber-400',
  'bg-rose-500/15 text-rose-400',
  'bg-cyan-500/15 text-cyan-400',
]

function initials(value: string): string {
  const cleaned = (value || '').trim()
  if (!cleaned) return '?'
  const parts = cleaned.split(/[\s@.]+/).filter(Boolean)
  if (parts.length === 0) return cleaned[0].toUpperCase()
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
}

function avatarColor(seed: string): string {
  let sum = 0
  for (let i = 0; i < seed.length; i++) sum += seed.charCodeAt(i)
  return AVATAR_COLORS[sum % AVATAR_COLORS.length]
}

function Avatar({ seed, label }: { seed: string; label: string }) {
  return (
    <div
      className={cn(
        'flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold',
        avatarColor(seed || label),
      )}
      aria-hidden="true"
    >
      {initials(label || seed)}
    </div>
  )
}

function fmtDate(value: string): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleDateString('de-DE', { day: '2-digit', month: 'short' })
}

function fmtDateTime(value: string): string {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString('de-DE', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// ── Komponente ────────────────────────────────────────────────────────────────

export function InboxPage() {
  const isOffline = useIsOffline()
  const activeTab = useInboxStore((s) => s.activeTab)
  const setTab = useInboxStore((s) => s.setTab)

  const emails = useInboxStore((s) => s.emails)
  const emailLoading = useInboxStore((s) => s.emailLoading)
  const emailLoaded = useInboxStore((s) => s.emailLoaded)
  const searchEmails = useInboxStore((s) => s.searchEmails)
  const emailDetail = useInboxStore((s) => s.emailDetail)
  const emailDetailLoading = useInboxStore((s) => s.emailDetailLoading)
  const openEmail = useInboxStore((s) => s.openEmail)
  const closeEmail = useInboxStore((s) => s.closeEmail)

  const webexRooms = useInboxStore((s) => s.webexRooms)
  const webexMessages = useInboxStore((s) => s.webexMessages)
  const webexMessagesLoading = useInboxStore((s) => s.webexMessagesLoading)
  const selectedRoom = useInboxStore((s) => s.selectedRoom)
  const webexLoading = useInboxStore((s) => s.webexLoading)
  const fetchWebexRooms = useInboxStore((s) => s.fetchWebexRooms)
  const fetchWebexMessages = useInboxStore((s) => s.fetchWebexMessages)

  const projects = useProjectStore((s) => s.projects)
  const { toasts, removeToast, success, error } = useToast()

  const [searchQuery, setSearchQuery] = useState('')
  const [roomQuery, setRoomQuery] = useState('')
  const [messageQuery, setMessageQuery] = useState('')
  const [linkDialog, setLinkDialog] = useState<{ source: 'email' | 'webex'; ref: string; subject: string; sender: string; date: string } | null>(null)
  const [openEmailSnapshot, setOpenEmailSnapshot] = useState<EmailResult | null>(null)
  const [linkTarget, setLinkTarget] = useState<'project' | 'todo'>('project')
  const [linkTargetId, setLinkTargetId] = useState('')
  const [knowledgeDialog, setKnowledgeDialog] = useState<KnowledgeDialogState>(null)
  const [knowledgeProjectId, setKnowledgeProjectId] = useState('')
  const [knowledgeSubmitting, setKnowledgeSubmitting] = useState(false)
  const [extractedKeys, setExtractedKeys] = useState<Set<string>>(new Set())

  // Initial-Load je Tab — Email nur wenn noch nie geladen, Webex-Rooms sind
  // im Store selbst gegen Doppel-Fetch geschützt.
  useEffect(() => {
    if (activeTab === 'email') {
      if (!emailLoaded) searchEmails()
    } else {
      fetchWebexRooms()
    }
  }, [activeTab, emailLoaded, searchEmails, fetchWebexRooms])

  // Alle bereits extrahierten Nachrichten-Keys vorab laden — EIN aggregierter
  // Request statt früher N (einer pro Projekt → O(Projekte) HTTP-Calls bei
  // jedem Inbox-Aufruf). Der Endpoint ist projekt-übergreifend, daher genügt
  // ein Mount-Fetch; inkrementelle Updates nach einer Extraktion übernehmen
  // handleExtractToKnowledge (optimistisch) + refreshExtractedForProject.
  useEffect(() => {
    let cancelled = false
    api
      .get<Array<{ source: string; external_id: string | null }>>('/knowledge/imports/messages')
      .then((entries) => {
        if (cancelled) return
        const next = new Set<string>()
        for (const entry of entries) {
          if (entry && entry.external_id) {
            next.add(`${entry.source}:${entry.external_id}`)
          }
        }
        setExtractedKeys(next)
      })
      .catch(() => {
        // Offline / Endpoint nicht erreichbar — Badges bleiben leer, kein Crash.
      })
    return () => {
      cancelled = true
    }
  }, [])

  const filteredRooms = useMemo(() => {
    const q = roomQuery.trim().toLowerCase()
    if (!q) return webexRooms
    return webexRooms.filter((r) => (r.title || r.id).toLowerCase().includes(q))
  }, [webexRooms, roomQuery])

  const filteredMessages = useMemo(() => {
    const q = messageQuery.trim().toLowerCase()
    if (!q) return webexMessages
    return webexMessages.filter(
      (m) =>
        (m.text || '').toLowerCase().includes(q) ||
        (m.person_display_name || m.person_email || '').toLowerCase().includes(q),
    )
  }, [webexMessages, messageQuery])

  const refreshExtractedForProject = async (projectId: string) => {
    if (!projectId) return
    try {
      const entries = await api.get<Array<{ source: string; external_id: string | null }>>(`/knowledge/${projectId}/imports/messages`)
      setExtractedKeys((prev) => {
        const next = new Set(prev)
        for (const entry of entries) {
          if (entry && entry.external_id) {
            next.add(`${entry.source}:${entry.external_id}`)
          }
        }
        return next
      })
    } catch {
      // ignore — keep current state
    }
  }

  const handleSearch = () => searchEmails(searchQuery)

  const handleOpenEmail = (email: EmailResult) => {
    setOpenEmailSnapshot(email)
    openEmail(email.id, email.folder)
  }

  const handleCloseEmail = () => {
    setOpenEmailSnapshot(null)
    closeEmail()
  }

  const handleLink = async () => {
    if (!linkDialog || !linkTargetId) return
    try {
      await api.post('/inbox/link', {
        link_target: linkTarget,
        target_id: linkTargetId,
        source: linkDialog.source,
        source_ref: linkDialog.ref,
        subject: linkDialog.subject,
        sender: linkDialog.sender,
        date: linkDialog.date,
        snippet: linkDialog.subject.slice(0, 200),
      })
      const targetLabel = linkTarget === 'project' ? 'Projekt' : 'Todo'
      success(`Mit ${targetLabel} verknüpft`, { description: linkDialog.subject })
      setLinkDialog(null)
      setLinkTargetId('')
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : (e as Error).message
      error('Verknüpfen fehlgeschlagen', { description: msg })
    }
  }

  const handleExtractToKnowledge = async (projectId: string) => {
    if (!knowledgeDialog || !projectId) return
    setKnowledgeSubmitting(true)
    try {
      await api.post(`/knowledge/${projectId}/extract/message`, {
        subject: knowledgeDialog.subject,
        sender: knowledgeDialog.sender,
        content: knowledgeDialog.content,
        source: knowledgeDialog.source,
        external_id: knowledgeDialog.externalId,
      })
      setExtractedKeys((prev) => {
        const next = new Set(prev)
        next.add(`${knowledgeDialog.source}:${knowledgeDialog.externalId}`)
        return next
      })
      success('Wissen extrahiert', { description: knowledgeDialog.subject })
      setKnowledgeDialog(null)
      setKnowledgeProjectId('')
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : (e as Error).message
      error('Extraktion fehlgeschlagen', { description: msg })
    } finally {
      setKnowledgeSubmitting(false)
    }
  }

  const handleRetryConnection = async () => {
    try {
      const res = await fetch('/api/settings/ai-assist-status')
      const data = await res.json()
      if (data.connected) {
        success('AI-Assist wieder erreichbar')
        if (activeTab === 'email') searchEmails(searchQuery)
        else fetchWebexRooms(true)
      } else {
        error('AI-Assist weiterhin nicht erreichbar')
      }
    } catch {
      error('Verbindungsprüfung fehlgeschlagen')
    }
  }

  return (
    <div className="flex h-full flex-col p-6">
      {isOffline && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-md border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-400">
          <span>Inbox benötigt AI-Assist Verbindung — aktuell offline.</span>
          <Button size="sm" variant="outline" onClick={handleRetryConnection}>
            <RefreshCw className="h-3 w-3" />
            Neu verbinden
          </Button>
        </div>
      )}

      <Tabs value={activeTab} onValueChange={(v) => setTab(v as 'email' | 'webex')} className="flex min-h-0 flex-1 flex-col">
        <TabsList>
          <TabsTrigger value="email">Email</TabsTrigger>
          <TabsTrigger value="webex">Webex</TabsTrigger>
        </TabsList>

        {/* ── Email ───────────────────────────────────────────────────────── */}
        <TabsContent value="email" className="mt-4 flex min-h-0 flex-1 flex-col">
          <div className="mb-3 flex gap-2">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Emails durchsuchen (Betreff & Inhalt)..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                className="h-9 pl-8"
              />
            </div>
            <Button size="lg" onClick={handleSearch} disabled={emailLoading}>
              {emailLoading ? 'Sucht...' : 'Suchen'}
            </Button>
          </div>

          {!emailLoading && emails.length > 0 && (
            <p className="mb-2 text-xs text-muted-foreground">
              {emails.length} {emails.length === 1 ? 'Ergebnis' : 'Ergebnisse'}
              {searchQuery.trim() && <> für „{searchQuery.trim()}"</>}
            </p>
          )}

          <div className="min-h-0 flex-1 overflow-y-auto">
            {emailLoading && emails.length === 0 ? (
              <ListSkeleton count={5} />
            ) : emails.length === 0 ? (
              <EmptyState
                icon="📭"
                title={searchQuery.trim() ? 'Keine Treffer' : 'Keine Emails gefunden'}
                description={
                  searchQuery.trim()
                    ? 'Keine Email passt zu deiner Suche. Versuche andere Begriffe.'
                    : 'Durchsuche deine Inbox oder warte auf neue Emails. Verknüpfe Emails mit Projekten und Todos.'
                }
              />
            ) : (
              <div className="space-y-1.5">
                {emails.map((email) => {
                  const extId = email.id || email.subject
                  const alreadyExtracted = extractedKeys.has(`email:${extId}`)
                  const senderLabel = email.sender_name || email.sender || 'Unbekannt'
                  return (
                    <Card
                      key={extId}
                      size="sm"
                      className="group flex-row items-center gap-3 px-3 py-2.5 transition-colors hover:bg-accent/40"
                    >
                      <Avatar seed={email.sender || senderLabel} label={senderLabel} />

                      <button
                        type="button"
                        onClick={() => handleOpenEmail(email)}
                        className="min-w-0 flex-1 cursor-pointer rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                        aria-label={`Email öffnen: ${email.subject || '(Kein Betreff)'}`}
                      >
                        <div className="flex items-baseline gap-2">
                          <p className="truncate text-sm font-medium">{email.subject || '(Kein Betreff)'}</p>
                          {email.has_attachments && (
                            <Paperclip className="h-3 w-3 shrink-0 text-muted-foreground" aria-label="Anhang" />
                          )}
                        </div>
                        <p className="truncate text-xs text-muted-foreground">
                          <span className="text-foreground/70">{senderLabel}</span>
                          {email.body_preview ? <> — {email.body_preview}</> : null}
                        </p>
                      </button>

                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                        {fmtDate(email.date)}
                      </span>

                      <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                        {alreadyExtracted ? (
                          <Badge variant="outline" className="border-emerald-500/30 text-emerald-400">✓ Wissen</Badge>
                        ) : (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            title="Nach Wissen extrahieren"
                            onClick={() => {
                              setKnowledgeDialog({
                                source: 'email',
                                externalId: extId,
                                subject: email.subject || '',
                                sender: email.sender || '',
                                content: email.body_preview || email.subject || '',
                              })
                              setKnowledgeProjectId('')
                            }}
                          >
                            <Brain className="h-4 w-4" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          title="Mit Projekt/Todo verknüpfen"
                          onClick={() => setLinkDialog({
                            source: 'email',
                            ref: extId,
                            subject: email.subject || '',
                            sender: email.sender || '',
                            date: email.date || '',
                          })}
                        >
                          <LinkIcon className="h-4 w-4" />
                        </Button>
                      </div>
                    </Card>
                  )
                })}
              </div>
            )}
          </div>
        </TabsContent>

        {/* ── Webex ───────────────────────────────────────────────────────── */}
        <TabsContent value="webex" className="mt-4 flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 gap-4">
            {/* Raum-Liste */}
            <div className="flex w-64 shrink-0 flex-col gap-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Räume filtern..."
                  value={roomQuery}
                  onChange={(e) => setRoomQuery(e.target.value)}
                  className="h-8 pl-8"
                />
              </div>
              <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
                {webexLoading && webexRooms.length === 0 ? (
                  <ListSkeleton count={6} />
                ) : filteredRooms.length === 0 ? (
                  <p className="py-4 text-center text-xs text-muted-foreground">
                    {webexRooms.length === 0 ? 'Keine Räume' : 'Kein Raum passt zum Filter'}
                  </p>
                ) : (
                  filteredRooms.map((room) => (
                    <button
                      key={room.id}
                      onClick={() => { setMessageQuery(''); fetchWebexMessages(room.id) }}
                      className={cn(
                        'w-full rounded-md px-3 py-2 text-left transition-colors',
                        selectedRoom === room.id ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/50',
                      )}
                    >
                      <p className="truncate text-sm font-medium">{room.title || room.id}</p>
                      {room.last_activity && (
                        <p className="text-[11px] text-muted-foreground">{fmtDateTime(room.last_activity)}</p>
                      )}
                    </button>
                  ))
                )}
              </div>
            </div>

            {/* Nachrichten */}
            <div className="flex min-h-0 flex-1 flex-col">
              {!selectedRoom ? (
                <div className="flex flex-1 items-center justify-center">
                  <EmptyState
                    icon="💬"
                    title="Raum auswählen"
                    description="Wähle links einen Webex-Raum, um seine Nachrichten zu sehen und zu durchsuchen."
                  />
                </div>
              ) : (
                <>
                  <div className="relative mb-3">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      placeholder="Nachrichten durchsuchen..."
                      value={messageQuery}
                      onChange={(e) => setMessageQuery(e.target.value)}
                      className="h-9 pl-8"
                    />
                  </div>

                  <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto">
                    {webexMessagesLoading && webexMessages.length === 0 ? (
                      <ListSkeleton count={6} />
                    ) : filteredMessages.length === 0 ? (
                      <p className="py-8 text-center text-sm text-muted-foreground">
                        {webexMessages.length === 0
                          ? 'Keine Nachrichten in diesem Raum'
                          : 'Keine Nachricht passt zur Suche'}
                      </p>
                    ) : (
                      filteredMessages.map((msg) => {
                        const senderLabel = msg.person_display_name || msg.person_email || 'Unbekannt'
                        const alreadyExtracted = extractedKeys.has(`webex:${msg.id}`)
                        return (
                          <Card
                            key={msg.id}
                            size="sm"
                            className="group flex-row items-start gap-3 px-3 py-2.5 transition-colors hover:bg-accent/40"
                          >
                            <Avatar seed={msg.person_email || senderLabel} label={senderLabel} />
                            <div className="min-w-0 flex-1">
                              <div className="flex items-baseline gap-2">
                                <p className="truncate text-sm font-medium">{senderLabel}</p>
                                <span className="shrink-0 text-[11px] text-muted-foreground">
                                  {fmtDateTime(msg.created)}
                                </span>
                              </div>
                              <p className="mt-0.5 whitespace-pre-wrap break-words text-sm text-foreground/90">
                                {msg.text || <span className="italic text-muted-foreground">(kein Text)</span>}
                              </p>
                            </div>
                            <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                              {alreadyExtracted ? (
                                <Badge variant="outline" className="border-emerald-500/30 text-emerald-400">✓ Wissen</Badge>
                              ) : (
                                <Button
                                  variant="ghost"
                                  size="icon-sm"
                                  title="Nach Wissen extrahieren"
                                  onClick={() => {
                                    setKnowledgeDialog({
                                      source: 'webex',
                                      externalId: msg.id,
                                      subject: (msg.text || '').slice(0, 100),
                                      sender: msg.person_email || msg.person_display_name || '',
                                      content: msg.text || '',
                                    })
                                    setKnowledgeProjectId('')
                                  }}
                                >
                                  <Brain className="h-4 w-4" />
                                </Button>
                              )}
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                title="Mit Projekt/Todo verknüpfen"
                                onClick={() => setLinkDialog({
                                  source: 'webex',
                                  ref: msg.id,
                                  subject: (msg.text || '').slice(0, 100),
                                  sender: msg.person_email || msg.person_display_name || '',
                                  date: msg.created || '',
                                })}
                              >
                                <LinkIcon className="h-4 w-4" />
                              </Button>
                            </div>
                          </Card>
                        )
                      })
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </TabsContent>
      </Tabs>

      {/* Link Dialog */}
      <Dialog open={!!linkDialog} onOpenChange={() => setLinkDialog(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>Nachricht verknüpfen</DialogTitle></DialogHeader>
          <div className="space-y-4 py-2">
            {linkDialog && (
              <p className="text-sm text-muted-foreground">
                "{linkDialog.subject}" von {linkDialog.sender}
              </p>
            )}
            <div>
              <label className="mb-1 block text-sm font-medium">Verknüpfen mit</label>
              <Select value={linkTarget} onValueChange={(v) => setLinkTarget(v as 'project' | 'todo')}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="project">Projekt</SelectItem>
                  <SelectItem value="todo">Todo</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Ziel</label>
              <Select value={linkTargetId || '__none__'} onValueChange={(v) => setLinkTargetId(v === '__none__' ? '' : v)}>
                <SelectTrigger><SelectValue placeholder="Auswählen..." /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__" disabled>Auswählen...</SelectItem>
                  {linkTarget === 'project' && projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLinkDialog(null)}>Abbrechen</Button>
            <Button onClick={handleLink} disabled={!linkTargetId}>Verknüpfen</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Knowledge Extract Dialog */}
      <Dialog
        open={!!knowledgeDialog}
        onOpenChange={(open) => {
          if (!open) {
            setKnowledgeDialog(null)
            setKnowledgeProjectId('')
          }
        }}
      >
        <DialogContent>
          <DialogHeader><DialogTitle>Nach Wissen extrahieren</DialogTitle></DialogHeader>
          <div className="space-y-4 py-2">
            {knowledgeDialog && (
              <p className="text-sm text-muted-foreground">
                "{knowledgeDialog.subject}" von {knowledgeDialog.sender}
              </p>
            )}
            <div>
              <label className="mb-1 block text-sm font-medium">Projekt</label>
              <Select
                value={knowledgeProjectId || '__none__'}
                onValueChange={(v) => {
                  const next = v === '__none__' ? '' : v
                  setKnowledgeProjectId(next)
                  if (next) refreshExtractedForProject(next)
                }}
              >
                <SelectTrigger><SelectValue placeholder="Auswählen..." /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__" disabled>Auswählen...</SelectItem>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {knowledgeDialog && knowledgeProjectId && extractedKeys.has(`${knowledgeDialog.source}:${knowledgeDialog.externalId}`) && (
              <p className="text-xs text-emerald-400">Bereits als Wissen gespeichert.</p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setKnowledgeDialog(null); setKnowledgeProjectId('') }}>Abbrechen</Button>
            <Button
              onClick={() => handleExtractToKnowledge(knowledgeProjectId)}
              disabled={!knowledgeProjectId || knowledgeSubmitting}
            >
              {knowledgeSubmitting ? 'Übertrage...' : '→ Wissen'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Email Detail */}
      <Dialog open={!!openEmailSnapshot} onOpenChange={(v) => { if (!v) handleCloseEmail() }}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="pr-6 text-base">
              {emailDetail?.subject || openEmailSnapshot?.subject || '(Kein Betreff)'}
            </DialogTitle>
          </DialogHeader>
          {openEmailSnapshot && (
            <div className="space-y-3 py-2">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span>
                  Von:{' '}
                  <strong className="text-foreground">
                    {emailDetail?.sender_name || emailDetail?.sender || openEmailSnapshot.sender_name || openEmailSnapshot.sender || 'Unbekannt'}
                  </strong>
                </span>
                {(emailDetail?.date || openEmailSnapshot.date) && (
                  <>
                    <span className="text-border">·</span>
                    <span>{fmtDateTime(emailDetail?.date || openEmailSnapshot.date)}</span>
                  </>
                )}
              </div>

              {emailDetail && emailDetail.to.length > 0 && (
                <p className="text-xs text-muted-foreground">An: {emailDetail.to.join(', ')}</p>
              )}
              {emailDetail && emailDetail.cc.length > 0 && (
                <p className="text-xs text-muted-foreground">Cc: {emailDetail.cc.join(', ')}</p>
              )}

              {emailDetail && emailDetail.attachments.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {emailDetail.attachments.map((att) => (
                    <Badge key={att.name} variant="outline">
                      <Paperclip className="h-3 w-3" />
                      {att.name}
                    </Badge>
                  ))}
                </div>
              )}

              {emailDetailLoading ? (
                <div className="space-y-2 rounded border border-border bg-muted/30 p-3">
                  <ListSkeleton count={4} />
                </div>
              ) : (
                <div className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap rounded border border-border bg-muted/30 p-3 text-sm">
                  {emailDetail?.body_text
                    || openEmailSnapshot.body_preview
                    || <span className="italic text-muted-foreground">Kein Inhalt verfügbar (AI-Assist nicht erreichbar?).</span>}
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={handleCloseEmail}>Schließen</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </div>
  )
}
