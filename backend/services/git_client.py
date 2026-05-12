"""Git operations for local repositories.

This is the code side of the `git_repo` source: we shell out to the
`git` CLI (already installed with most dev environments). Remote-only
repos without local checkout are out of scope here — those would need
AI-Assist support.

All commands run with a bounded timeout and reject paths that do not
contain a `.git` directory to avoid accidental shell escapes.
"""

import asyncio
import logging
import os
import pathlib
import shlex
from dataclasses import dataclass

logger = logging.getLogger("projecthub.git")

GIT_TIMEOUT_S = 30
MAX_OUTPUT_CHARS = 200_000  # cap any single git-output read

# Directories we don't want to crawl for baseline
_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".cache", "target", "out", ".turbo",
    "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache",
}

# Candidate manifest/doc files for baseline (first match wins per category)
_MANIFEST_CANDIDATES = [
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
    "composer.json", "Gemfile", "requirements.txt",
]
_README_CANDIDATES = ["README.md", "README.rst", "README.txt", "README"]


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    timestamp: int  # unix seconds
    subject: str
    body: str
    additions: int = 0
    deletions: int = 0
    files_changed: int = 0
    top_files: list[str] | None = None


def _validate_repo_path(path: str) -> pathlib.Path:
    p = pathlib.Path(path).expanduser().resolve()
    if not p.is_dir():
        raise RuntimeError(f"Git-Repo-Pfad existiert nicht: {path}")
    if not (p / ".git").exists():
        raise RuntimeError(f"Kein Git-Repository unter: {path}")
    return p


async def _run_git(cwd: pathlib.Path, *args: str) -> str:
    """Execute `git <args>` in `cwd`. Return stdout (truncated)."""
    cmd = ["git", "-C", str(cwd), *args]
    logger.debug("git %s", " ".join(shlex.quote(a) for a in cmd[2:]))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"git {args[0] if args else ''} timed out after {GIT_TIMEOUT_S}s")

    if proc.returncode != 0:
        msg = (err.decode("utf-8", errors="replace") or out.decode("utf-8", errors="replace")).strip()
        raise RuntimeError(f"git failed ({proc.returncode}): {msg[:500]}")

    text = out.decode("utf-8", errors="replace")
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n…[truncated]"
    return text


# --- Commit log ------------------------------------------------------------

# Format: SHA\x1FAUTHOR\x1FEMAIL\x1FUNIX\x1FSUBJECT\x1FBODY\x1E
# Using unit-separators avoids collisions with real commit content.
_LOG_FORMAT = "%H%x1f%an%x1f%ae%x1f%at%x1f%s%x1f%b%x1e"


async def commits_since(
    repo_path: str,
    since_iso: str | None = None,
    branch: str | None = None,
    limit: int = 500,
) -> list[Commit]:
    """Return commits reachable from HEAD (or `branch`), newest first.

    If `since_iso` is given, only commits after that timestamp are returned.
    Per-commit stats (additions/deletions/files) are fetched in a second
    pass via `git show --shortstat` for each SHA.
    """
    p = _validate_repo_path(repo_path)

    log_args = ["log", f"--pretty=format:{_LOG_FORMAT}", f"-{limit}"]
    if since_iso:
        log_args.append(f"--since={since_iso}")
    if branch:
        log_args.append(branch)

    raw = await _run_git(p, *log_args)
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) < 5:
            continue
        sha, author, email, ts, subject = parts[:5]
        body = parts[5] if len(parts) > 5 else ""
        try:
            ts_int = int(ts)
        except (ValueError, TypeError):
            ts_int = 0
        commits.append(Commit(
            sha=sha.strip(),
            author=author.strip(),
            email=email.strip(),
            timestamp=ts_int,
            subject=subject.strip(),
            body=body.strip(),
        ))
    return commits


async def enrich_with_stats(repo_path: str, commit: Commit) -> None:
    """Populate additions/deletions/files_changed/top_files for a commit."""
    p = _validate_repo_path(repo_path)
    try:
        raw = await _run_git(
            p,
            "show",
            "--numstat",
            "--format=",
            commit.sha,
        )
    except RuntimeError:
        return
    additions = deletions = files = 0
    top_files: list[tuple[str, int]] = []  # (path, total churn)
    for line in raw.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, path = parts
        try:
            ai = int(a) if a.isdigit() else 0
            di = int(d) if d.isdigit() else 0
        except ValueError:
            ai = di = 0
        additions += ai
        deletions += di
        files += 1
        top_files.append((path, ai + di))
    top_files.sort(key=lambda x: -x[1])
    commit.additions = additions
    commit.deletions = deletions
    commit.files_changed = files
    commit.top_files = [p for p, _ in top_files[:10]]


# --- Baseline scan ---------------------------------------------------------

def _should_skip_dir(name: str) -> bool:
    return name in _IGNORE_DIRS or name.startswith(".")


def _read_file_truncated(path: pathlib.Path, max_chars: int = 5000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > max_chars:
        return text[:max_chars] + "\n…[gekürzt]"
    return text


def _scan_tree(root: pathlib.Path, depth: int = 2) -> list[str]:
    """Return a list of path strings (relative), up to `depth` levels deep."""
    out: list[str] = []
    root = root.resolve()

    def walk(cur: pathlib.Path, level: int):
        if level > depth:
            return
        try:
            entries = sorted(cur.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError:
            return
        for entry in entries:
            if _should_skip_dir(entry.name):
                continue
            rel = entry.relative_to(root).as_posix()
            if entry.is_dir():
                out.append(rel + "/")
                walk(entry, level + 1)
            else:
                out.append(rel)
            if len(out) >= 200:
                return

    walk(root, 1)
    return out


async def build_baseline(repo_path: str) -> dict:
    """Assemble a snapshot of the repo suitable for an initial baseline prompt.

    Returns a dict with keys: readme, manifests, tree, head_sha, branch.
    """
    p = _validate_repo_path(repo_path)

    readme = ""
    readme_name = ""
    for cand in _README_CANDIDATES:
        fp = p / cand
        if fp.exists() and fp.is_file():
            readme = _read_file_truncated(fp, 5000)
            readme_name = cand
            break

    manifests: dict[str, str] = {}
    for cand in _MANIFEST_CANDIDATES:
        fp = p / cand
        if fp.exists() and fp.is_file():
            manifests[cand] = _read_file_truncated(fp, 2000)

    tree = _scan_tree(p, depth=2)

    head_sha = ""
    branch = ""
    try:
        head_sha = (await _run_git(p, "rev-parse", "HEAD")).strip()
    except Exception:
        pass
    try:
        branch = (await _run_git(p, "rev-parse", "--abbrev-ref", "HEAD")).strip()
    except Exception:
        pass

    return {
        "readme": readme,
        "readme_name": readme_name,
        "manifests": manifests,
        "tree": tree,
        "head_sha": head_sha,
        "branch": branch,
        "root_name": p.name,
    }


def format_baseline_prompt_payload(baseline: dict) -> dict:
    """Flatten baseline dict into fields the prompt template expects."""
    manifests_text = ""
    for name, content in baseline["manifests"].items():
        manifests_text += f"\n— {name} —\n{content}\n"
    tree_text = "\n".join(baseline["tree"]) if baseline["tree"] else "(leer)"

    return {
        "root_name": baseline["root_name"],
        "branch": baseline["branch"] or "?",
        "head_sha": baseline["head_sha"][:12] if baseline["head_sha"] else "?",
        "readme_name": baseline["readme_name"] or "(keine README)",
        "readme_content": baseline["readme"] or "(keine README vorhanden)",
        "manifests_text": manifests_text or "(keine Manifeste gefunden)",
        "tree_text": tree_text,
    }
