import { type ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Leichtgewichtiger Markdown-Renderer — KEINE externe Dependency.
 *
 * Deckt die GFM-Teilmenge ab, die LLM-Ausgaben in der Praxis nutzen:
 * Überschriften, geordnete/ungeordnete Listen, Fenced- und Inline-Code,
 * Blockquotes, Tabellen, horizontale Linien, sowie inline **fett**,
 * *kursiv*, `code`, ~~durchgestrichen~~ und [Links](url).
 *
 * Bewusst KEIN vollständiger CommonMark-Parser: unbekannte Konstrukte
 * fallen auf Klartext zurück — also nie schlechter als der bisherige
 * `whitespace-pre-wrap`-Rohtext, nur in den häufigen Fällen deutlich besser.
 *
 * Sicherheit: Es wird ausschließlich nach React-Elementen gerendert (kein
 * `dangerouslySetInnerHTML`) — Textinhalte escaped React selbst. Link-Hrefs
 * werden auf http(s)/mailto bzw. interne Pfade beschränkt.
 *
 * Bekannte Grenzen: `_einfach_` wird NICHT als kursiv interpretiert (zu
 * viele Fehltreffer bei `snake_case`); verschachtelte Listen werden flach
 * gerendert.
 */

const HEADING_TAG: Record<number, 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6'> = {
  1: 'h1', 2: 'h2', 3: 'h3', 4: 'h4', 5: 'h5', 6: 'h6',
}

function safeHref(raw: string): string | undefined {
  const url = raw.trim()
  if (/^(https?:|mailto:)/i.test(url)) return url
  if (url.startsWith('/') || url.startsWith('#')) return url
  return undefined
}

// Inline-Parser: `code` (zuerst — Inhalt nicht weiter parsen), dann
// [Links](url), **fett** / __fett__, *kursiv*, ~~durchgestrichen~~.
const INLINE_PATTERN =
  /(`[^`]+`)|(\[[^\]]+\]\([^)]+\))|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*\s][^*]*\*)|(~~[^~]+~~)/

function parseInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let rest = text
  let n = 0

  while (rest) {
    const m = rest.match(INLINE_PATTERN)
    if (!m || m.index === undefined) {
      nodes.push(rest)
      break
    }
    if (m.index > 0) nodes.push(rest.slice(0, m.index))
    const token = m[0]
    const key = `${keyPrefix}-i${n++}`

    if (m[1]) {
      nodes.push(
        <code key={key} className="rounded bg-muted px-1 py-0.5 text-[0.85em]">
          {token.slice(1, -1)}
        </code>,
      )
    } else if (m[2]) {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
      const href = link ? safeHref(link[2]) : undefined
      if (link && href) {
        nodes.push(
          <a
            key={key}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline underline-offset-2"
          >
            {link[1]}
          </a>,
        )
      } else {
        nodes.push(link ? link[1] : token)
      }
    } else if (m[3]) {
      nodes.push(<strong key={key}>{parseInline(token.slice(2, -2), key)}</strong>)
    } else if (m[4]) {
      nodes.push(<em key={key}>{parseInline(token.slice(1, -1), key)}</em>)
    } else if (m[5]) {
      nodes.push(<del key={key}>{token.slice(2, -2)}</del>)
    }
    rest = rest.slice(m.index + token.length)
  }
  return nodes
}

function splitTableRow(row: string): string[] {
  return row
    .replace(/^\s*\|/, '')
    .replace(/\|\s*$/, '')
    .split('|')
    .map((c) => c.trim())
}

function renderBlocks(src: string, keyPrefix: string): ReactNode[] {
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let b = 0
  const key = () => `${keyPrefix}-b${b++}`

  while (i < lines.length) {
    const line = lines[i]

    if (line.trim() === '') {
      i++
      continue
    }

    // Fenced code block
    if (/^```/.test(line.trim())) {
      const code: string[] = []
      i++
      while (i < lines.length && !/^```/.test(lines[i].trim())) {
        code.push(lines[i])
        i++
      }
      i++ // schließendes ```
      blocks.push(
        <pre key={key()} className="overflow-x-auto rounded bg-muted p-3 text-xs">
          <code>{code.join('\n')}</code>
        </pre>,
      )
      continue
    }

    // Überschrift
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      const Tag = HEADING_TAG[Math.min(h[1].length, 6)]
      const k = key()
      blocks.push(<Tag key={k}>{parseInline(h[2].trim(), k)}</Tag>)
      i++
      continue
    }

    // Horizontale Linie
    if (/^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$/.test(line.trim())) {
      blocks.push(<hr key={key()} />)
      i++
      continue
    }

    // Blockquote (zusammenhängende > Zeilen) — rekursiv gerendert
    if (/^>\s?/.test(line)) {
      const quote: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^>\s?/, ''))
        i++
      }
      const k = key()
      blocks.push(<blockquote key={k}>{renderBlocks(quote.join('\n'), k)}</blockquote>)
      continue
    }

    // Tabelle: Kopfzeile mit | + Trennzeile aus -, :, |, Leerzeichen
    if (
      line.includes('|') &&
      i + 1 < lines.length &&
      /^[\s|:-]+$/.test(lines[i + 1]) &&
      lines[i + 1].includes('-')
    ) {
      const headers = splitTableRow(line)
      i += 2 // Kopf- + Trennzeile überspringen
      const rows: string[][] = []
      while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
        rows.push(splitTableRow(lines[i]))
        i++
      }
      const k = key()
      blocks.push(
        <table key={k}>
          <thead>
            <tr>
              {headers.map((hd, idx) => (
                <th key={`${k}-h${idx}`}>{parseInline(hd, `${k}-h${idx}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={`${k}-r${ri}`}>
                {row.map((cell, ci) => (
                  <td key={`${k}-r${ri}c${ci}`}>{parseInline(cell, `${k}-r${ri}c${ci}`)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }

    // Ungeordnete Liste
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ''))
        i++
      }
      const k = key()
      blocks.push(
        <ul key={k}>
          {items.map((it, idx) => (
            <li key={`${k}-l${idx}`}>{parseInline(it, `${k}-l${idx}`)}</li>
          ))}
        </ul>,
      )
      continue
    }

    // Geordnete Liste
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ''))
        i++
      }
      const k = key()
      blocks.push(
        <ol key={k}>
          {items.map((it, idx) => (
            <li key={`${k}-l${idx}`}>{parseInline(it, `${k}-l${idx}`)}</li>
          ))}
        </ol>,
      )
      continue
    }

    // Absatz: zusammenhängende Nicht-Leer-/Nicht-Sonderzeilen
    const para: string[] = []
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^```/.test(lines[i].trim()) &&
      !/^#{1,6}\s+/.test(lines[i]) &&
      !/^>\s?/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      para.push(lines[i])
      i++
    }
    const k = key()
    const paraNodes: ReactNode[] = []
    para.forEach((p, idx) => {
      if (idx > 0) paraNodes.push(<br key={`${k}-br${idx}`} />)
      paraNodes.push(...parseInline(p, `${k}-p${idx}`))
    })
    blocks.push(<p key={k}>{paraNodes}</p>)
  }

  return blocks
}

interface MarkdownProps {
  children: string
  className?: string
}

/**
 * Rendert einen Markdown-String als gestylte React-Elemente.
 * Nutzt die `prose`-Klassen von `@tailwindcss/typography` für das Styling.
 */
export function Markdown({ children, className }: MarkdownProps) {
  return (
    <div className={cn('prose prose-sm prose-invert max-w-none', className)}>
      {renderBlocks(children || '', 'md')}
    </div>
  )
}
