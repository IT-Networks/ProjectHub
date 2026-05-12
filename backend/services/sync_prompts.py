"""Prompt templates for the sync-pipeline analyze stage.

Each prompt asks the LLM to return JSON with a consistent shape so the
analyzer can parse the output uniformly across source types.

Shape:
  {
    "relevance": "core" | "related" | "irrelevant",
    "reason":    "<1 sentence why>",
    "summary":   "<2-3 sentences: essence of this change for the project>",
    "category":  "architecture" | "business_logic" | "infrastructure" |
                 "process" | "decision" | "reference" | "custom",
    "tags":      ["tag1", "tag2", ...],
    "title":     "<short knowledge-item title, max 80 chars>",
    "confidence": 0.0-1.0
  }

We do NOT .format() these because they contain JSON braces — use
`.replace()` on the sentinel tokens instead.
"""

PR_PROMPT = """Du bist ein Projekt-Analyst. Beurteile, ob der folgende GitHub-Pull-Request
für das Projekt relevant ist und fasse das Wesentliche zusammen.

Projekt: {project_name}
Projektbeschreibung: {project_description}
Bestehendes Projektwissen (Top-5 Titel):
{existing_titles}

Pull-Request: {external_ref}
Titel: {title}
Status: {state}
Autor: {author}
Geändert am: {updated_at}
Beschreibung (gekürzt):
{body_snippet}

Diff-Stats: +{additions} / -{deletions} über {changed_files} Dateien

Wichtigste geänderte Dateien:
{top_files}

Antworte AUSSCHLIESSLICH mit JSON:
{
  "relevance": "core" | "related" | "irrelevant",
  "reason":    "1 Satz",
  "summary":   "2-3 Sätze: Was macht dieser PR aus Projektsicht?",
  "category":  "architecture" | "business_logic" | "infrastructure" | "process" | "decision" | "reference" | "custom",
  "tags":      ["tag1", "tag2"],
  "title":     "Kurztitel max 80 Zeichen",
  "confidence": 0.0-1.0
}

Regeln:
- "core" = betrifft den Projekt-Scope direkt
- "related" = angrenzend / unterstützend
- "irrelevant" = Dependabot, Tippfehler, CI-only, Submodule-Bump, nichts Projekt-spezifisches
- Confidence < 0.5 wenn Beschreibung fehlt oder Diff nicht aussagekräftig
"""


BUILD_PROMPT = """Du bist ein Projekt-Analyst. Beurteile, ob dieser Jenkins-Build-Vorfall
für das Projekt relevant ist und fasse ihn zusammen.

Projekt: {project_name}
Projektbeschreibung: {project_description}

Build: {external_ref}
Status: {state}
Dauer: {duration}
Letzter Log-Auszug:
{log_tail}

Antworte AUSSCHLIESSLICH mit JSON (gleiche Shape wie PR):
{
  "relevance": "core" | "related" | "irrelevant",
  "reason":    "1 Satz",
  "summary":   "Was bedeutet dieser Build-Status für das Projekt? Ist Handeln nötig?",
  "category":  "infrastructure" | "process" | "architecture" | "reference" | "custom",
  "tags":      ["tag1"],
  "title":     "Kurztitel max 80 Zeichen",
  "confidence": 0.0-1.0
}

- failed/unstable Builds sind meist "core" (blockieren Release)
- erfolgreiche Builds nach langer Rot-Phase = "core"
- routine-grüne Builds = "irrelevant"
"""


COMMIT_PROMPT = """Du bist ein Projekt-Analyst. Beurteile, ob dieser Commit zum Projekt
beiträgt oder außerhalb des Scopes liegt.

Projekt: {project_name}
Projektbeschreibung: {project_description}
Bestehendes Projektwissen (Top-5 Titel):
{existing_titles}

Commit: {external_ref}
Autor: {author}
Message:
{message}

Diff-Stats: +{additions} / -{deletions} über {changed_files} Dateien
Wichtigste geänderte Dateien:
{top_files}

Antworte AUSSCHLIESSLICH mit JSON (gleiche Shape wie PR):
{
  "relevance": "core" | "related" | "irrelevant",
  "reason":    "1 Satz",
  "summary":   "Was macht dieser Commit für das Projekt?",
  "category":  "architecture" | "business_logic" | "infrastructure" | "process" | "decision" | "reference" | "custom",
  "tags":      ["tag1", "tag2"],
  "title":     "Kurztitel max 80 Zeichen",
  "confidence": 0.0-1.0
}

- Merge-Commits ohne Änderung, Version-Bumps, Lint-Fixes = "irrelevant"
- Feature-Commits, Bugfixes in Projekt-Scope = "core"
- Test-Only, Doc-Only mit Inhalt = "related"
"""


JIRA_PROMPT = """Du bist ein Projekt-Analyst. Beurteile dieses Jira-Ticket-Update.

Projekt: {project_name}
Projektbeschreibung: {project_description}

Ticket: {external_ref}
Titel: {title}
Status: {state}
Priorität: {priority}
Typ: {issue_type}
Beschreibung (gekürzt):
{body_snippet}

Antworte AUSSCHLIESSLICH mit JSON (gleiche Shape wie PR):
{
  "relevance": "core" | "related" | "irrelevant",
  "reason":    "1 Satz",
  "summary":   "Worum geht es in diesem Ticket und was bedeutet der aktuelle Stand?",
  "category":  "business_logic" | "decision" | "process" | "architecture" | "reference" | "custom",
  "tags":      ["tag1", "tag2"],
  "title":     "Kurztitel max 80 Zeichen",
  "confidence": 0.0-1.0
}
"""


BASELINE_PROMPT = """Du bist ein Projekt-Analyst. Erstelle eine initiale Architektur-Übersicht
für ein Codebase-Projekt, das gerade erstmals in die Wissensbasis importiert wird.

Projekt (ProjectHub-Ebene): {project_name}
Projektbeschreibung: {project_description}

Repository: {root_name}
Branch: {branch}
HEAD: {head_sha}

README ({readme_name}):
{readme_content}

Projekt-Manifeste:
{manifests_text}

Verzeichnis-Struktur (Top 2 Ebenen):
{tree_text}

Antworte AUSSCHLIESSLICH mit JSON:
{
  "relevance": "core",
  "reason":    "Baseline-Import",
  "summary":   "3-5 Sätze: Was ist das für ein Projekt? Welche Sprache/Framework? Welche Hauptmodule? Was ist der Scope?",
  "category":  "architecture",
  "tags":      ["tag1", "tag2", "tag3"],
  "title":     "Codebase-Baseline: <Kurzname>",
  "confidence": 0.9
}

- Relevance ist IMMER "core" bei einer Baseline
- Category ist IMMER "architecture"
- Tags: Sprache, Framework, Architektur-Stil, Domäne (max 5)
- Summary soll dem LLM später bei Commit-Analysen als Grundverständnis dienen
"""


COMMIT_BATCH_PROMPT = """Du bist ein Projekt-Analyst. Fasse eine Commit-Serie zusammen — es gab
in diesem Sync zu viele Einzel-Commits, also bekommst du sie als Batch.

Projekt: {project_name}
Projektbeschreibung: {project_description}

Zeitraum: {date_range}
Anzahl Commits: {commit_count}
Top-Autoren: {top_authors}

Commit-Liste (gekürzt):
{commit_list}

Antworte AUSSCHLIESSLICH mit JSON:
{
  "relevance": "core" | "related" | "irrelevant",
  "reason":    "1 Satz",
  "summary":   "Was wurde in diesem Zeitraum getan? Welche Themen dominieren? Welche Teile des Systems wurden berührt?",
  "category":  "architecture" | "business_logic" | "infrastructure" | "process" | "decision" | "reference" | "custom",
  "tags":      ["tag1", "tag2"],
  "title":     "Aktivität <Zeitraum> — <Hauptthema>",
  "confidence": 0.0-1.0
}

- Wenn die Commits überwiegend Dependabot / Merge / CI / Format sind → "irrelevant"
- Wenn Feature-Arbeit dominiert → "core"
- Wenn gemischt → "related"
"""


PROMPTS = {
    "pr": PR_PROMPT,
    "build": BUILD_PROMPT,
    "commit": COMMIT_PROMPT,
    "jira": JIRA_PROMPT,
    "codebase_baseline": BASELINE_PROMPT,
    "commit_batch": COMMIT_BATCH_PROMPT,
}


def render_prompt(change_type: str, ctx: dict) -> str:
    """Render the prompt for a change_type by substituting {key} tokens.

    Uses plain str.replace (not str.format) because the JSON example in
    each prompt contains literal curly braces.
    """
    tpl = PROMPTS.get(change_type)
    if not tpl:
        return ""
    out = tpl
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", str(v))
    return out
