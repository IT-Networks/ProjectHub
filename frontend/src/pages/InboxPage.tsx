import { useEffect, useState } from 'react'
import { useInboxStore } from '@/stores/inboxStore'
import { useProjectStore } from '@/stores/projectStore'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useIsOffline } from '@/hooks/useOffline'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

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
  const [searchQuery, setSearchQuery] = useState('')
  const [linkDialog, setLinkDialog] = useState<{ source: 'email' | 'webex'; ref: string; subject: string; sender: string; date: string } | null>(null)
  const [linkTarget, setLinkTarget] = useState<'project' | 'todo'>('project')
  const [linkTargetId, setLinkTargetId] = useState('')

  useEffect(() => {
    if (activeTab === 'email') searchEmails()
    else fetchWebexRooms()
  }, [activeTab, searchEmails, fetchWebexRooms])

  const handleSearch = () => searchEmails(searchQuery)

  const handleLink = async () => {
    if (!linkDialog || !linkTargetId) return
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
    setLinkDialog(null)
    setLinkTargetId('')
  }

  return (
    <div className="flex h-full flex-col p-6">
      {isOffline && (
        <p className="mb-4 text-sm text-yellow-400">Inbox benötigt AI-Assist Verbindung</p>
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

          <div className="space-y-2">
            {emailLoading && <p className="text-sm text-muted-foreground">Laden...</p>}
            {emails.map((email: any) => (
              <Card key={email.id || email.subject} className="flex items-center justify-between p-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{email.subject || '(Kein Betreff)'}</p>
                  <p className="text-xs text-muted-foreground">
                    {email.sender} — {email.date ? new Date(email.date).toLocaleDateString('de-DE') : ''}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setLinkDialog({
                    source: 'email',
                    ref: email.id || email.subject,
                    subject: email.subject || '',
                    sender: email.sender || '',
                    date: email.date || '',
                  })}
                >
                  Verknüpfen
                </Button>
              </Card>
            ))}
            {!emailLoading && emails.length === 0 && (
              <p className="py-8 text-center text-sm text-muted-foreground">Keine Emails gefunden</p>
            )}
          </div>
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
              {webexMessages.map((msg: any) => (
                <Card key={msg.id} className="flex items-start justify-between p-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium text-muted-foreground">{msg.personDisplayName || msg.personEmail}</p>
                    <p className="mt-1 text-sm">{msg.text}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {msg.created ? new Date(msg.created).toLocaleString('de-DE') : ''}
                    </p>
                  </div>
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
                </Card>
              ))}
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
    </div>
  )
}
