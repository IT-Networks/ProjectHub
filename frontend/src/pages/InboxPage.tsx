import { useEffect, useState } from 'react'
import { Mail, Link as LinkIcon, Brain, RefreshCw } from 'lucide-react'
import { useInboxStore } from '@/stores/inboxStore'
import { useProjectStore } from '@/stores/projectStore'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Card } from '@/components/ui/card'
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

export function InboxPage() {
  const isOffline = useIsOffline()
  const activeTab = useInboxStore((s) => s.activeTab)
  const setTab = useInboxStore((s) => s.setTab)
  const emails = useInboxStore((s) => s.emails)
  const emailLoading = useInboxStore((s) => s.emailLoading)
  const searchEmails = useInboxStore((s) => s.searchEmails)
  const webexRooms = useInboxStore((s) => s.webexRooms)
  const webexMessages = useInboxStore((s) => s.webexMessages)
  const selectedRoom = useInboxStore((s) => s.selectedRoom)
  const webexLoading = useInboxStore((s) => s.webexLoading)
  const fetchWebexRooms = useInboxStore((s) => s.fetchWebexRooms)
  const fetchWebexMessages = useInboxStore((s) => s.fetchWebexMessages)
  const projects = useProjectStore((s) => s.projects)
  const { toasts, removeToast, success, error } = useToast()
  const [searchQuery, setSearchQuery] = useState('')
  const [linkDialog, setLinkDialog] = useState<{ source: 'email' | 'webex'; ref: string; subject: string; sender: string; date: string } | null>(null)
  const [emailDetail, setEmailDetail] = useState<{ subject: string; sender: string; date: string; body: string } | null>(null)
  const [linkTarget, setLinkTarget] = useState<'project' | 'todo'>('project')
  const [linkTargetId, setLinkTargetId] = useState('')
  const [knowledgeDialog, setKnowledgeDialog] = useState<KnowledgeDialogState>(null)
  const [knowledgeProjectId, setKnowledgeProjectId] = useState('')
  const [knowledgeSubmitting, setKnowledgeSubmitting] = useState(false)
  const [extractedKeys, setExtractedKeys] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (activeTab === 'email') searchEmails()
    else fetchWebexRooms()
  }, [activeTab, searchEmails, fetchWebexRooms])

  useEffect(() => {
    if (projects.length === 0) return
    let cancelled = false
    Promise.all(
      projects.map((p) =>
        api.get<Array<{ source: string; external_id: string | null }>>(`/knowledge/${p.id}/imports/messages`).catch(() => [])
      )
    ).then((results) => {
      if (cancelled) return
      const next = new Set<string>()
      for (const entries of results) {
        for (const entry of entries) {
          if (entry && entry.external_id) {
            next.add(`${entry.source}:${entry.external_id}`)
          }
        }
      }
      setExtractedKeys(next)
    })
    return () => {
      cancelled = true
    }
  }, [projects])

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
        else fetchWebexRooms()
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
          <Button size="sm" variant="outline" onClick={handleRetryConnection} className="gap-1.5">
            <RefreshCw className="h-3 w-3" />
            Neu verbinden
          </Button>
        </div>
      )}

      <Tabs value={activeTab} onValueChange={(v) => setTab(v as 'email' | 'webex')}>
        <TabsList>
          <TabsTrigger value="email">Email</TabsTrigger>
          <TabsTrigger value="webex">Webex</TabsTrigger>
        </TabsList>

        <TabsContent value="email" className="mt-4">
          <div className="mb-4 flex gap-2">
            <Input
              placeholder="Suche in Emails..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="flex-1"
            />
            <Button onClick={handleSearch} disabled={emailLoading}>Suchen</Button>
          </div>

          {emailLoading && emails.length === 0 ? (
            <ListSkeleton count={3} />
          ) : emails.length === 0 ? (
            <EmptyState
              icon="📭"
              title="Keine Emails gefunden"
              description="Durchsuche deine Inbox oder warte auf neue Emails. Verknüpfe Emails mit Projekten und Todos."
            />
          ) : (
            <div className="space-y-2">
              {emails.map((email: any) => {
                const emailExtId = email.id || email.subject
                const alreadyExtracted = extractedKeys.has(`email:${emailExtId}`)
                return (
                  <Card key={emailExtId} className="flex items-center justify-between gap-2 p-3 transition-colors hover:bg-accent/30">
                    <button
                      type="button"
                      onClick={() => setEmailDetail({
                        subject: email.subject || '(Kein Betreff)',
                        sender: email.sender || 'Unbekannt',
                        date: email.date || '',
                        body: email.body || email.subject || '(Kein Inhalt)',
                      })}
                      className="min-w-0 flex-1 cursor-pointer rounded-md px-1 py-0.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                      aria-label={`Email öffnen: ${email.subject || '(Kein Betreff)'}`}
                    >
                      <p className="truncate text-sm font-medium">{email.subject || '(Kein Betreff)'}</p>
                      <p className="text-xs text-muted-foreground">
                        {email.sender} — {email.date ? new Date(email.date).toLocaleDateString('de-DE') : ''}
                      </p>
                    </button>
                    <div className="flex items-center gap-2">
                      {alreadyExtracted && (
                        <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-400">✓ Wissen</span>
                      )}
                      {!alreadyExtracted && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setKnowledgeDialog({
                              source: 'email',
                              externalId: emailExtId,
                              subject: email.subject || '',
                              sender: email.sender || '',
                              content: email.body || email.subject || '',
                            })
                            setKnowledgeProjectId('')
                          }}
                        >
                          <Brain className="w-4 h-4 mr-1" />
                          → Wissen
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setLinkDialog({
                          source: 'email',
                          ref: emailExtId,
                          subject: email.subject || '',
                          sender: email.sender || '',
                          date: email.date || '',
                        })}
                        icon={<LinkIcon className="w-4 h-4" />}
                      >
                        Verknüpfen
                      </Button>
                    </div>
                  </Card>
                )
              })}
            </div>
          )}
        </TabsContent>

        <TabsContent value="webex" className="mt-4">
          <div className="flex gap-4">
            {/* Room list */}
            <div className="w-64 space-y-1">
              {webexRooms.map((room: any) => (
                <button
                  key={room.id}
                  onClick={() => fetchWebexMessages(room.id)}
                  className={cn(
                    'w-full rounded-md px-3 py-2 text-left text-sm transition-colors',
                    selectedRoom === room.id ? 'bg-accent text-accent-foreground' : 'hover:bg-accent/50'
                  )}
                >
                  {room.title || room.id}
                </button>
              ))}
              {webexRooms.length === 0 && !webexLoading && (
                <p className="py-4 text-center text-xs text-muted-foreground">Keine Räume</p>
              )}
            </div>

            {/* Messages */}
            <div className="flex-1 space-y-2">
              {webexMessages.map((msg: any) => {
                const alreadyExtracted = extractedKeys.has(`webex:${msg.id}`)
                return (
                  <Card key={msg.id} className="flex items-start justify-between p-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium text-muted-foreground">{msg.personDisplayName || msg.personEmail}</p>
                      <p className="mt-1 text-sm">{msg.text}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {msg.created ? new Date(msg.created).toLocaleString('de-DE') : ''}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {alreadyExtracted && (
                        <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-400">✓ Wissen</span>
                      )}
                      {!alreadyExtracted && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setKnowledgeDialog({
                              source: 'webex',
                              externalId: msg.id,
                              subject: (msg.text || '').slice(0, 100),
                              sender: msg.personEmail || msg.personDisplayName || '',
                              content: msg.text || '',
                            })
                            setKnowledgeProjectId('')
                          }}
                        >
                          <Brain className="w-4 h-4 mr-1" />
                          → Wissen
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setLinkDialog({
                          source: 'webex',
                          ref: msg.id,
                          subject: (msg.text || '').slice(0, 100),
                          sender: msg.personEmail || msg.personDisplayName || '',
                          date: msg.created || '',
                        })}
                      >
                        Verknüpfen
                      </Button>
                    </div>
                  </Card>
                )
              })}
              {!selectedRoom && (
                <p className="py-8 text-center text-sm text-muted-foreground">Raum auswählen</p>
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
      <Dialog open={!!emailDetail} onOpenChange={(v) => { if (!v) setEmailDetail(null) }}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="pr-6 text-base">{emailDetail?.subject}</DialogTitle>
          </DialogHeader>
          {emailDetail && (
            <div className="space-y-3 py-2">
              <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                <span>Von: <strong className="text-foreground">{emailDetail.sender}</strong></span>
                {emailDetail.date && (
                  <>
                    <span className="text-border">·</span>
                    <span>{new Date(emailDetail.date).toLocaleString('de-DE')}</span>
                  </>
                )}
              </div>
              <div className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap rounded border border-border bg-muted/30 p-3 text-sm">
                {emailDetail.body}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEmailDetail(null)}>Schließen</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ToastContainer toasts={toasts} onDismiss={removeToast} />
    </div>
  )
}
