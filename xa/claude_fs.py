"""Pure filesystem view of ``~/.claude/``.

This layer reads Claude Code's on-disk state and nothing else: no tmux, no
subprocess, no network. It is the substrate every higher layer of ``xa``
reads from.

What lives under ``~/.claude/`` (as of Claude Code 2.x):

- ``projects/<slug>/<uuid>.jsonl`` — one file per session, full transcript.
  ``<slug>`` is the cwd with ``/`` replaced by ``-`` (leading ``-`` kept).
  ``<uuid>`` is the Claude ``sessionId``.
- ``sessions/<pid>.json`` — per-process ephemeral metadata while a
  ``claude`` process is alive. Holds ``pid``, ``sessionId``,
  ``bridgeSessionId``, ``cwd``, ``startedAt``, ``version``. Deleted when
  the process exits.
- ``history.jsonl`` — global append-only log of every prompt sent. Each
  line carries ``sessionId`` + ``cwd``.
- ``sessions-index.json`` — summary index (not yet consumed here).

All functions are read-only. All paths default to ``~/.claude/`` but accept
a ``claude_home`` override (set ``XA_CLAUDE_HOME`` in the env to point at a
fixture tree during testing).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional


DEFAULT_CLAUDE_HOME = Path(os.environ.get("XA_CLAUDE_HOME") or Path.home() / ".claude")

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PID_RE = re.compile(r"^\d+$")
_USER_INTERRUPT_MARKER = "Request interrupted by user for tool use"


# --------------------------------------------------------------------------- #
# dataclasses (SSOT for the shapes this layer returns)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TranscriptMeta:
    """Summary of a transcript JSONL, cheap enough to compute for listings."""

    path: Path
    session_id: Optional[str]
    cwd: Optional[str]
    project_slug: str
    summary: Optional[str]
    custom_title: Optional[str]
    first_user_message: Optional[str]
    turn_count: int
    forked_from: Optional[str]
    created: Optional[float]
    modified: Optional[float]
    size_bytes: int


@dataclass(frozen=True)
class TranscriptForensics:
    """Postmortem facts extracted by walking a transcript from the end.

    ``user_interrupted`` is the raw marker presence — it is **ambiguous**
    (also fires on phone-standby bridge drops), so do not derive user intent
    from it alone. Callers should corroborate against pane-log tails.
    """

    transcript_path: Optional[Path]
    line_count: int
    last_tool_name: Optional[str]
    last_tool_command: Optional[str]
    last_tool_exit_code: Optional[int]
    last_tool_result_tail: Optional[str]
    final_assistant_text: Optional[str]
    user_interrupted: bool


@dataclass(frozen=True)
class HistoryEntry:
    """One line from ``~/.claude/history.jsonl``."""

    cwd: Optional[str]
    project: Optional[str]
    display: Optional[str]
    pasted_contents: Optional[Any]


# --------------------------------------------------------------------------- #
# slug ↔ cwd
# --------------------------------------------------------------------------- #


def encode_project_slug(cwd: str) -> str:
    """Encode a cwd into the ``~/.claude/projects/`` slug form.

    >>> encode_project_slug('/root/py/proj/tt/glossa')
    '-root-py-proj-tt-glossa'
    >>> encode_project_slug('/')
    '-'
    """
    return cwd.replace("/", "-")


def parse_project_slug(slug: str) -> str:
    """Decode a ``~/.claude/projects/`` slug back to a cwd.

    >>> parse_project_slug('-root-py-proj-tt-glossa')
    '/root/py/proj/tt/glossa'
    >>> parse_project_slug('-Users-thorwhalen-tw-server')
    '/Users/thorwhalen/tw/server'

    NB: This transform is lossy — a cwd with literal ``-`` in segment names
    round-trips to a different slug. Claude Code itself has the same
    limitation, so we accept it.
    """
    return slug.replace("-", "/")


# --------------------------------------------------------------------------- #
# transcript file enumeration
# --------------------------------------------------------------------------- #


def _projects_dir(claude_home: Path) -> Path:
    return claude_home / "projects"


def iter_project_slugs(*, claude_home: Path = DEFAULT_CLAUDE_HOME) -> Iterator[str]:
    """Yield slugs (directory names) under ``~/.claude/projects/``."""
    projects = _projects_dir(claude_home)
    if not projects.is_dir():
        return
    for entry in projects.iterdir():
        if entry.is_dir():
            yield entry.name


def iter_transcript_files(
    *,
    claude_home: Path = DEFAULT_CLAUDE_HOME,
    project_slug: Optional[str] = None,
) -> Iterator[Path]:
    """Yield transcript JSONL paths, optionally restricted to one project.

    Only files whose stem is a valid UUID are yielded — this filters out
    the ``memory/`` subfolder and other non-session artefacts Claude Code
    stores alongside transcripts.
    """
    projects = _projects_dir(claude_home)
    if not projects.is_dir():
        return
    slugs = (
        [project_slug]
        if project_slug
        else list(iter_project_slugs(claude_home=claude_home))
    )
    for slug in slugs:
        pdir = projects / slug
        if not pdir.is_dir():
            continue
        for entry in pdir.iterdir():
            if entry.suffix != ".jsonl":
                continue
            if not _UUID_RE.match(entry.stem):
                continue
            yield entry


# --------------------------------------------------------------------------- #
# ephemeral session files (~/.claude/sessions/<pid>.json)
# --------------------------------------------------------------------------- #


def _sessions_dir(claude_home: Path) -> Path:
    return claude_home / "sessions"


def read_ephemeral_session(
    pid: int, *, claude_home: Path = DEFAULT_CLAUDE_HOME
) -> Optional[dict]:
    """Read the per-process session file for ``pid`` if present."""
    path = _sessions_dir(claude_home) / f"{pid}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def iter_ephemeral_sessions(
    *, claude_home: Path = DEFAULT_CLAUDE_HOME
) -> Iterator[dict]:
    """Yield all live ephemeral session dicts.

    A session file disappears when claude exits, so this reflects
    momentary state only.
    """
    sdir = _sessions_dir(claude_home)
    if not sdir.is_dir():
        return
    for entry in sdir.iterdir():
        if entry.suffix != ".json" or not _PID_RE.match(entry.stem):
            continue
        try:
            yield json.loads(entry.read_text())
        except (OSError, json.JSONDecodeError):
            continue


# --------------------------------------------------------------------------- #
# transcript parsing
# --------------------------------------------------------------------------- #


def _iter_json_lines(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects from a JSONL file; skip malformed lines."""
    try:
        with path.open("r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def iter_transcript_events(path: Path) -> Iterator[dict]:
    """Yield every event from a transcript JSONL.

    Lines that fail to parse are silently skipped — transcripts are
    append-only on a live process, so the tail can be partial.
    """
    yield from _iter_json_lines(path)


def _first_text_content(message: Any) -> Optional[str]:
    """Return the first ``text`` block from a message.content array."""
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if text:
                return text
    return None


def transcript_metadata(path: Path) -> TranscriptMeta:
    """Summarise a transcript without holding it all in memory.

    Walks forward, counting turns and capturing the first user message /
    summary / custom title / fork pointer / cwd / sessionId. Stops reading
    content fields once it has what it needs, but still counts turns to
    the end. For very large transcripts, callers who only need counts
    should use ``iter_transcript_events`` directly.
    """
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    summary: Optional[str] = None
    custom_title: Optional[str] = None
    first_user: Optional[str] = None
    forked_from: Optional[str] = None
    turn_count = 0

    for ev in _iter_json_lines(path):
        t = ev.get("type")
        if t in ("user", "assistant"):
            turn_count += 1
        if session_id is None:
            session_id = ev.get("sessionId")
        if cwd is None:
            cwd = ev.get("cwd")
        if summary is None and t == "summary":
            summary = ev.get("summary")
        if custom_title is None:
            custom_title = ev.get("customTitle") or ev.get("title")
        if forked_from is None:
            ff = ev.get("forkedFrom")
            if isinstance(ff, dict):
                forked_from = ff.get("sessionId")
            elif isinstance(ff, str):
                forked_from = ff
        if first_user is None and t == "user":
            first_user = _first_text_content(ev.get("message"))

    try:
        st = path.stat()
        created: Optional[float] = st.st_ctime
        modified: Optional[float] = st.st_mtime
        size: int = st.st_size
    except OSError:
        created = modified = None
        size = 0

    slug = path.parent.name
    return TranscriptMeta(
        path=path,
        session_id=session_id or path.stem,
        cwd=cwd,
        project_slug=slug,
        summary=summary,
        custom_title=custom_title,
        first_user_message=(first_user[:500] if first_user else None),
        turn_count=turn_count,
        forked_from=forked_from,
        created=created,
        modified=modified,
        size_bytes=size,
    )


def transcript_forensics(path: Path) -> TranscriptForensics:
    """Extract postmortem-relevant facts from the tail of a transcript.

    Walks from the *end* of the file so we find the most recent
    ``tool_use`` / ``tool_result`` / assistant ``text`` without reparsing
    the whole transcript.
    """
    lines: list[str] = []
    try:
        with path.open("r", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return TranscriptForensics(
            transcript_path=None,
            line_count=0,
            last_tool_name=None,
            last_tool_command=None,
            last_tool_exit_code=None,
            last_tool_result_tail=None,
            final_assistant_text=None,
            user_interrupted=False,
        )

    last_tool_use: Optional[dict] = None
    last_tool_result: Optional[dict] = None
    last_assistant_text: Optional[str] = None

    for line in reversed(lines):
        if last_tool_use and last_tool_result and last_assistant_text:
            break
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        role = msg.get("role")
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "tool_use" and last_tool_use is None:
                last_tool_use = block
            elif bt == "tool_result" and last_tool_result is None:
                last_tool_result = block
            elif bt == "text" and last_assistant_text is None and role == "assistant":
                text = block.get("text") or ""
                last_assistant_text = text[:500]

    tool_name: Optional[str] = None
    tool_command: Optional[str] = None
    if last_tool_use:
        tool_name = last_tool_use.get("name")
        inp = last_tool_use.get("input") or {}
        if isinstance(inp, dict):
            cmd = inp.get("command") or inp.get("file_path") or ""
            tool_command = str(cmd)[:500]

    exit_code: Optional[int] = None
    result_tail: Optional[str] = None
    interrupted = False
    if last_tool_result:
        raw = last_tool_result.get("content", "")
        result_str = raw if isinstance(raw, str) else str(raw)
        m = re.search(r"Exit code (\d+)", result_str)
        if m:
            exit_code = int(m.group(1))
        result_tail = result_str[:800]
        interrupted = _USER_INTERRUPT_MARKER in result_str

    return TranscriptForensics(
        transcript_path=path,
        line_count=len(lines),
        last_tool_name=tool_name,
        last_tool_command=tool_command,
        last_tool_exit_code=exit_code,
        last_tool_result_tail=result_tail,
        final_assistant_text=last_assistant_text,
        user_interrupted=interrupted,
    )


# --------------------------------------------------------------------------- #
# history.jsonl
# --------------------------------------------------------------------------- #


def history_iter(*, claude_home: Path = DEFAULT_CLAUDE_HOME) -> Iterator[HistoryEntry]:
    """Yield entries from ``~/.claude/history.jsonl`` in file order (oldest first).

    Useful for cross-project full-text prompt search without loading
    every transcript.
    """
    path = claude_home / "history.jsonl"
    if not path.is_file():
        return
    for ev in _iter_json_lines(path):
        yield HistoryEntry(
            cwd=ev.get("cwd"),
            project=ev.get("project") or ev.get("projectPath"),
            display=ev.get("display"),
            pasted_contents=ev.get("pastedContents"),
        )


# --------------------------------------------------------------------------- #
# transcript lookup by (cwd, session_id)
# --------------------------------------------------------------------------- #


def transcript_path(
    cwd: str,
    session_id: str,
    *,
    claude_home: Path = DEFAULT_CLAUDE_HOME,
) -> Optional[Path]:
    """Return the transcript path for ``(cwd, session_id)`` if it exists.

    >>> from pathlib import Path
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as td:
    ...     home = Path(td)
    ...     (home / 'projects' / '-foo-bar').mkdir(parents=True)
    ...     f = home / 'projects' / '-foo-bar' / 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl'
    ...     _ = f.write_text('')
    ...     p = transcript_path('/foo/bar', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', claude_home=home)
    ...     p == f
    True
    """
    slug = encode_project_slug(cwd)
    path = claude_home / "projects" / slug / f"{session_id}.jsonl"
    return path if path.is_file() else None
