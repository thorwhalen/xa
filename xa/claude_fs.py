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

All functions are read-only. All paths default to the account's home
(:func:`default_claude_home`: ``$XA_CLAUDE_HOME``, else Claude Code's own
``$CLAUDE_CONFIG_DIR``, else ``~/.claude/``) and accept a ``claude_home``
override. ``XA_CLAUDE_HOME`` is also how tests point xa at a fixture tree.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from xa.claude_env import HOME_ENV_VAR


#: Claude Code's account variable (:mod:`xa.claude_env` owns it).
CLAUDE_HOME_ENV_VAR = HOME_ENV_VAR


def default_claude_home(environ: Optional[dict] = None) -> Path:
    """The Claude home xa reads (and launches sessions under) by default.

    ``$XA_CLAUDE_HOME`` first (xa's own override), then ``$CLAUDE_CONFIG_DIR``
    (so an xa run from a second account's shell sees that account), then
    ``~/.claude``.

    >>> str(default_claude_home({'CLAUDE_CONFIG_DIR': '/h/second'}))
    '/h/second'
    >>> str(default_claude_home({'XA_CLAUDE_HOME': '/x', 'CLAUDE_CONFIG_DIR': '/h/second'}))
    '/x'
    """
    env = os.environ if environ is None else environ
    chosen = env.get("XA_CLAUDE_HOME") or env.get(CLAUDE_HOME_ENV_VAR)
    return Path(chosen).expanduser() if chosen else Path.home() / ".claude"


DEFAULT_CLAUDE_HOME = default_claude_home()

_SLUG_RE = re.compile(r"[^A-Za-z0-9-]")
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

    Claude Code turns every character that is not a letter, digit or ``-`` into
    ``-`` (``_`` and ``.`` included), not only ``/``.

    >>> encode_project_slug('/root/py/proj/tt/glossa')
    '-root-py-proj-tt-glossa'
    >>> encode_project_slug('/p/i/_zodals/tw_platform.v2')
    '-p-i--zodals-tw-platform-v2'
    >>> encode_project_slug('/')
    '-'
    """
    return _SLUG_RE.sub("-", cwd)


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


def remote_control_at_startup(
    *, claude_home: Path = DEFAULT_CLAUDE_HOME
) -> Optional[bool]:
    """Is this host set to auto-connect Remote Control for every session?

    Reads ``remoteControlAtStartup`` from the user ``settings.json``.
    ``None`` means "can't tell" — no file, unreadable, or the key absent
    (in which case Claude Code follows its own default, which xa does not
    get to observe).

    Deliberately only the *user* settings file. Claude Code also honors
    the key in project, local and managed settings, and a project-level
    ``false`` outranks a user-level ``true`` — so a ``True`` here is
    "configured on for this user", not a guarantee about a given session.
    xa uses it to explain a missing URL, never to decide behavior.
    """
    path = Path(claude_home) / "settings.json"
    try:
        value = json.loads(path.read_text()).get("remoteControlAtStartup")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, bool) else None


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
    momentary state only. NB: claude deletes the file on *clean* exit
    only — a crash / SIGKILL / reboot leaves a stale file behind, so
    callers who care about actual liveness must validate each dict with
    :func:`ephemeral_session_alive` (or an equivalent predicate).
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


_PROC_ROOT = Path("/proc")


def _proc_starttime(pid: int, *, proc_root: Path = _PROC_ROOT) -> Optional[str]:
    """Return the kernel start-time ticks of ``pid`` from ``/proc/<pid>/stat``.

    Field 22 of the stat line, parsed *after* the last ``)`` because the
    ``comm`` field (2) may itself contain spaces or parentheses. Returns
    ``None`` when unreadable (no such pid, no /proc, permission).
    """
    try:
        stat = (proc_root / str(pid) / "stat").read_text()
    except (OSError, ValueError):
        return None
    # pid (comm) state ppid ... starttime is overall field 22, i.e. the
    # 20th whitespace-separated field after the closing paren.
    _, _, rest = stat.rpartition(")")
    fields = rest.split()
    return fields[19] if len(fields) > 19 else None


def _proc_comm(pid: int, *, proc_root: Path = _PROC_ROOT) -> str:
    """Kernel-level process name of ``pid``; '' when unreadable."""
    try:
        return (proc_root / str(pid) / "comm").read_text().strip()
    except (OSError, ValueError):
        return ""


# Kernel comms that legitimately host the claude CLI script (npm/bun
# installs run the js entry point under a runtime instead of the native
# binary). Anything else with "claude" in argv (an editor opening a file
# named claude, a grep…) is NOT a claude process.
_JS_RUNTIME_COMMS = frozenset({"node", "nodejs", "bun"})


def _looks_like_claude(pid: int, *, proc_root: Path = _PROC_ROOT) -> bool:
    """Identity check for pre-``procStart`` ephemeral files.

    The official native binary has ``comm == "claude"``; npm/bun installs
    run under a JS runtime with the claude script in argv — accept either.
    The argv match requires the comm to be a JS runtime AND a token whose
    *basename* is exactly ``claude`` (a bare substring would
    false-positive on e.g. ``vim ~/notes/claude`` or ``test_claude_fs.py``).
    """
    comm = _proc_comm(pid, proc_root=proc_root)
    if comm == "claude":
        return True
    if comm not in _JS_RUNTIME_COMMS:
        return False
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except (OSError, ValueError):
        return False
    argv = (a.decode("utf-8", errors="replace") for a in raw.split(b"\x00") if a)
    return any(a.rsplit("/", 1)[-1] == "claude" for a in argv)


def ephemeral_session_alive(eph: dict, *, proc_root: Path = _PROC_ROOT) -> bool:
    """Is the process behind an ephemeral session dict actually alive?

    The mere presence of ``~/.claude/sessions/<pid>.json`` proves nothing:
    claude removes it on clean exit, but crashes, SIGKILL, and host
    reboots leave stale files behind — and a stale file must not surface
    as a "live" session (with a dead URL, an unkillable card, and no
    postmortem).

    Checks, strongest first:

    - newer claude versions write ``procStart`` (the kernel start-time
      ticks) into the file — when present, it must match
      ``/proc/<pid>/stat``, which also defeats PID reuse;
    - otherwise the pid must exist and look like a claude process
      (``comm == "claude"`` for the native binary, or ``claude`` in the
      cmdline for npm installs running under ``node``);
    - on platforms without ``/proc`` (macOS), fall back to signal-0
      existence (no PID-reuse protection, best available).
    """
    pid = eph.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    if proc_root.is_dir():
        started = _proc_starttime(pid, proc_root=proc_root)
        if started is None:
            return False  # no such pid
        proc_start = eph.get("procStart")
        if proc_start is not None:
            return str(proc_start) == started
        return _looks_like_claude(pid, proc_root=proc_root)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


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
# what one transcript says about its launch (for resuming it as it was)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LaunchFacts:
    """What ``claude --resume`` needs to bring one session back as it was."""

    session_id: str
    transcript: Path
    cwd: Optional[str]
    permission_mode: str  # last recorded, claude's spelling; '' when none
    bypass_seen: bool  # bypass was reached, so the session had the flag
    model: Optional[str]
    effort: Optional[str]
    name: Optional[str]  # agent name, else custom title
    last_type: Optional[str]  # type of the final entry ('cost-state' on exit)
    last_write: float  # transcript mtime
    entrypoint: Optional[str] = None  # 'cli' interactive; 'sdk-cli' headless (-p, SDK)

    @property
    def headless(self) -> bool:
        """A ``claude -p`` / SDK run: nothing to resume into a pane."""
        return bool(self.entrypoint) and self.entrypoint.startswith("sdk")


def launch_facts(path: Path) -> LaunchFacts:
    """Read one transcript for the cwd, mode, model and name it was launched with.

    One forward pass; malformed lines are skipped like everywhere else in xa.
    """
    path = Path(path)
    slug = path.parent.name
    first_cwd: Optional[str] = None
    home_cwd: Optional[str] = None
    mode = ""
    bypass_seen = False
    model: Optional[str] = None
    effort: Optional[str] = None
    agent_name: Optional[str] = None
    title: Optional[str] = None
    session_id: Optional[str] = None
    last_type: Optional[str] = None
    entrypoint: Optional[str] = None

    for ev in iter_transcript_events(path):
        last_type = ev.get("type") or last_type
        session_id = session_id or ev.get("sessionId")
        if entrypoint is None and isinstance(ev.get("entrypoint"), str):
            entrypoint = ev["entrypoint"]
        cwd = ev.get("cwd")
        if isinstance(cwd, str) and cwd:
            first_cwd = first_cwd or cwd
            if home_cwd is None and encode_project_slug(cwd) == slug:
                home_cwd = cwd
        pm = ev.get("permissionMode")
        if isinstance(pm, str) and pm:
            mode = pm
            bypass_seen = bypass_seen or pm == "bypassPermissions"
        if isinstance(ev.get("effort"), str) and ev["effort"]:
            effort = ev["effort"]
        if isinstance(ev.get("agentName"), str) and ev["agentName"]:
            agent_name = ev["agentName"]
        if isinstance(ev.get("customTitle"), str) and ev["customTitle"]:
            title = ev["customTitle"]
        msg = ev.get("message")
        if ev.get("type") == "assistant" and isinstance(msg, dict):
            m = msg.get("model")
            if isinstance(m, str) and m and not m.startswith("<"):
                model = m  # '<synthetic>' marks a turn no model produced

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return LaunchFacts(
        session_id=session_id or path.stem,
        transcript=path,
        cwd=home_cwd or first_cwd,
        permission_mode=mode,
        bypass_seen=bypass_seen,
        model=model,
        effort=effort,
        name=agent_name or title,
        last_type=last_type,
        last_write=mtime,
        entrypoint=entrypoint,
    )


_MODEL_FAMILIES = ("opus", "sonnet", "haiku", "fable")


def _model_family(model: Optional[str]) -> Optional[str]:
    low = (model or "").lower()
    return next((f for f in _MODEL_FAMILIES if f in low), None)


def account_default_model(*, claude_home: Path = DEFAULT_CLAUDE_HOME) -> Optional[str]:
    """The ``model`` the account's ``settings.json`` sets, or ``None``."""
    try:
        value = json.loads((Path(claude_home) / "settings.json").read_text()).get(
            "model"
        )
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


def resume_model(
    facts: "LaunchFacts", *, claude_home: Path = DEFAULT_CLAUDE_HOME
) -> Optional[str]:
    """The ``--model`` that brings ``facts`` back on its own model, or ``None`` for none.

    ``None`` when the transcript's model is in the same family as the account's
    default: the default may carry what the transcript does not record (an
    ``opus[1m]`` default runs a 1M-token window, its turns record only
    ``claude-opus-5-5``), so pinning the bare id would shrink the context of a
    session that may already be past 200k. A different family (a sonnet session
    on an opus account) is pinned, which is the case the flag exists for.

    >>> f = LaunchFacts('s', Path('t'), None, '', False, 'claude-sonnet-5-5', None, None, None, 0.0)
    >>> resume_model(f, claude_home=Path('/no/such/home'))
    'claude-sonnet-5-5'
    """
    if not facts.model:
        return None
    default = account_default_model(claude_home=claude_home)
    if default and _model_family(default) == _model_family(facts.model):
        return None
    return facts.model


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
