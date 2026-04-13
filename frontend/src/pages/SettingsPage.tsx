import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

interface AiAssistStatus {
  connected: boolean
  base_url: string
  sse_subscribers: number
}

interface UpdateCheck {
  available: boolean
  current_version: string
  latest_version?: string
  release_notes?: string
  download_url?: string
  error?: string
}

interface UpdateResult {
  success: boolean
  message?: string
  error?: string
  files_updated?: string[]
  restart_required?: boolean
}

export function SettingsPage() {
  const [status, setStatus] = useState<AiAssistStatus | null>(null)
  const [testing, setTesting] = useState(false)
  const [version, setVersion] = useState<string>('')
  const [updateCheck, setUpdateCheck] = useState<UpdateCheck | null>(null)
  const [checking, setChecking] = useState(false)
  const [installing, setInstalling] = useState(false)
  const [updateResult, setUpdateResult] = useState<UpdateResult | null>(null)

  const checkStatus = async () => {
    setTesting(true)
    try {
      const data = await api.get<AiAssistStatus>('/settings/ai-assist-status')
      setStatus(data)
    } catch {
      setStatus({ connected: false, base_url: 'http://localhost:8000', sse_subscribers: 0 })
    }
    setTesting(false)
  }

  const fetchVersion = async () => {
    try {
      const data = await api.get<{ version: string }>('/update/version')
      setVersion(data.version)
    } catch {
      setVersion('?')
    }
  }

  const checkForUpdates = async () => {
    setChecking(true)
    setUpdateResult(null)
    try {
      const data = await api.get<UpdateCheck>('/update/check')
      setUpdateCheck(data)
    } catch (e) {
      setUpdateCheck({ available: false, current_version: version, error: (e as Error).message })
    }
    setChecking(false)
  }

  const installUpdate = async () => {
    if (!updateCheck?.download_url) return
    setInstalling(true)
    setUpdateResult(null)
    try {
      const data = await api.post<UpdateResult>('/update/install', {
        download_url: updateCheck.download_url,
        create_backup: true,
      })
      setUpdateResult(data)
      if (data.success) {
        fetchVersion()
        setUpdateCheck(null)
      }
    } catch (e) {
      setUpdateResult({ success: false, error: (e as Error).message })
    }
    setInstalling(false)
  }

  const restartServer = async () => {
    try {
      await api.post('/update/restart', {})
    } catch {
      // Expected — server is restarting
    }
  }

  useEffect(() => {
    checkStatus()
    fetchVersion()
  }, [])

  return (
    <div className="p-6 max-w-2xl">
      <h2 className="mb-6 text-xl font-semibold">Einstellungen</h2>

      {/* AI-Assist Connection */}
      <Card className="mb-6 p-5">
        <h3 className="mb-4 text-sm font-semibold">AI-Assist Verbindung</h3>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm">Status</span>
            {status && (
              <Badge variant={status.connected ? 'default' : 'destructive'}>
                {status.connected ? 'Verbunden' : 'Nicht erreichbar'}
              </Badge>
            )}
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm">URL</span>
            <span className="text-sm text-muted-foreground">{status?.base_url || '-'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm">SSE-Abonnenten</span>
            <span className="text-sm text-muted-foreground">{status?.sse_subscribers ?? 0}</span>
          </div>
          <Button variant="outline" size="sm" onClick={checkStatus} disabled={testing}>
            {testing ? 'Prüfe...' : 'Verbindung testen'}
          </Button>
        </div>
      </Card>

      {/* Keyboard Shortcuts */}
      <Card className="mb-6 p-5">
        <h3 className="mb-4 text-sm font-semibold">Tastenkombinationen</h3>
        <div className="space-y-2 text-sm">
          {[
            ['Ctrl+K', 'Globale Suche'],
            ['1', 'Dashboard'],
            ['2', 'Projekte'],
            ['3', 'Kanban'],
            ['4', 'Inbox'],
            ['5', 'Todo-Queue'],
            ['Ctrl+Enter', 'Chat-Nachricht senden'],
          ].map(([key, label]) => (
            <div key={key} className="flex items-center justify-between">
              <span className="text-muted-foreground">{label}</span>
              <kbd className="rounded border border-border bg-muted px-2 py-0.5 text-xs font-mono">{key}</kbd>
            </div>
          ))}
        </div>
      </Card>

      {/* Update */}
      <Card className="mb-6 p-5">
        <h3 className="mb-4 text-sm font-semibold">Update</h3>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm">Aktuelle Version</span>
            <Badge variant="secondary">{version || '...'}</Badge>
          </div>

          {updateCheck && !updateCheck.error && (
            <div className="flex items-center justify-between">
              <span className="text-sm">Neueste Version</span>
              <Badge variant={updateCheck.available ? 'default' : 'secondary'}>
                {updateCheck.latest_version}
              </Badge>
            </div>
          )}

          {updateCheck?.available && updateCheck.release_notes && (
            <div className="rounded bg-muted/50 p-2 text-xs text-muted-foreground">
              {updateCheck.release_notes}
            </div>
          )}

          {updateCheck?.error && (
            <p className="text-sm text-destructive">{updateCheck.error}</p>
          )}

          {updateResult && (
            <div className={cn(
              'rounded p-2 text-xs',
              updateResult.success
                ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                : 'bg-red-500/10 text-red-400 border border-red-500/20'
            )}>
              {updateResult.success
                ? `Update erfolgreich: ${updateResult.message}`
                : `Fehler: ${updateResult.error}`
              }
            </div>
          )}

          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={checkForUpdates} disabled={checking}>
              {checking ? 'Prüfe...' : 'Nach Updates suchen'}
            </Button>

            {updateCheck?.available && (
              <Button size="sm" onClick={installUpdate} disabled={installing}>
                {installing ? 'Installiere...' : 'Update installieren'}
              </Button>
            )}

            {updateResult?.restart_required && (
              <Button variant="destructive" size="sm" onClick={restartServer}>
                Server neustarten
              </Button>
            )}
          </div>
        </div>
      </Card>

      {/* App Info */}
      <Card className="p-5">
        <h3 className="mb-4 text-sm font-semibold">Info</h3>
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Version</span>
            <span>{version || '...'}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Frontend</span>
            <span>React + Vite + shadcn/ui</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Backend</span>
            <span>FastAPI + SQLite</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Datenbank</span>
            <span>SQLite (lokal)</span>
          </div>
        </div>
      </Card>
    </div>
  )
}
