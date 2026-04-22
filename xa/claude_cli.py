"""Spawn, resume, and URL-resolve Claude Code sessions.

Ties ``xa.tmux`` (pane / process control), ``xa.claude_fs`` (read the
ephemeral ``~/.claude/sessions/<pid>.json`` file) and the ``claude`` CLI
binary together. Everything here targets the *local* machine; remote-host
dispatch lives in ``xa.hosts`` (Phase 6+).

Bridge URL format: ``https://claude.ai/code/<bridgeSessionId>``. The
``bridgeSessionId`` already starts with ``session_`` — do not prepend
anything.
"""

from __future__ import annotations

import re
import secrets
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from xa import claude_fs as cfs
from xa import tmux as tm


DEFAULT_CLAUDE_BIN = "claude"
CLAUDE_WEB_BASE = "https://claude.ai/code"

_URL_RE = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9_-]+")

UrlSource = Literal["session_file", "pane_capture"]


@dataclass(frozen=True)
class SpawnResult:
    """What ``spawn_session`` / ``resume_session`` return to the caller."""

    name: str
    cwd: str
    claude_pid: Optional[int]
    claude_session_id: Optional[str]
    bridge_session_id: Optional[str]
    url: Optional[str]
    url_source: Optional[UrlSource]
    warning: Optional[str]


# --------------------------------------------------------------------------- #
# URL resolution
# --------------------------------------------------------------------------- #


def find_claude_pid(
    session_name: str,
    *,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
) -> Optional[int]:
    """Return the ``claude`` process PID living in the session's pane."""
    root = tm.pane_pid(session_name, binary=tmux_bin)
    if root is None:
        return None
    for pid in (root, *tm.descendants(root)):
        if tm.proc_comm(pid) == "claude":
            return pid
    return None


def _extract_url_from_text(text: str) -> Optional[str]:
    m = _URL_RE.search(text)
    return m.group(0) if m else None


def resolve_bridge_url(
    session_name: str,
    *,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
    scrape_lines: int = 400,
) -> tuple[Optional[str], Optional[UrlSource]]:
    """Return ``(url, source)`` for a live tmux-hosted claude session.

    Primary path: find the claude descendant pid → read
    ``~/.claude/sessions/<pid>.json`` → take ``bridgeSessionId``.
    Fallback: regex-scrape ``capture-pane`` output for the full URL.
    """
    pid = find_claude_pid(session_name, tmux_bin=tmux_bin)
    if pid is not None:
        data = cfs.read_ephemeral_session(pid, claude_home=claude_home)
        if data and (bridge := data.get("bridgeSessionId")):
            return f"{CLAUDE_WEB_BASE}/{bridge}", "session_file"
    pane = tm.capture_pane(session_name, lines=scrape_lines, binary=tmux_bin)
    url = _extract_url_from_text(pane)
    if url:
        return url, "pane_capture"
    return None, None


# --------------------------------------------------------------------------- #
# readiness handshake
# --------------------------------------------------------------------------- #


def _dismiss_trust_and_enable_remote_control(
    session_name: str,
    *,
    tmux_bin: str,
    deadline: float,
    auto_remote_control: bool,
) -> None:
    """Poll the pane until claude is ready; dismiss prompts on the way.

    States handled:
      - "trust this folder" → send Enter.
      - Main prompt (``❯``) visible but "remote control active" absent →
        send ``/remote-control`` + Enter (skipped if ``auto_remote_control``
        is False).
    """
    trust_dismissed = False
    rc_sent = False
    while time.time() < deadline:
        pane = tm.capture_pane(session_name, lines=60, binary=tmux_bin).lower()
        if not trust_dismissed and "trust this folder" in pane:
            tm.send_keys(session_name, "Enter", binary=tmux_bin)
            trust_dismissed = True
            time.sleep(1.5)
            continue
        if (
            auto_remote_control
            and not rc_sent
            and "remote control active" not in pane
            and "❯" in pane
        ):
            tm.send_keys(session_name, "/remote-control", "Enter", binary=tmux_bin)
            rc_sent = True
            time.sleep(1.5)
            continue
        return


# --------------------------------------------------------------------------- #
# spawn / resume
# --------------------------------------------------------------------------- #


def _wait_for_url(
    session_name: str,
    *,
    claude_home: Path,
    tmux_bin: str,
    deadline: float,
    auto_remote_control: bool,
) -> tuple[Optional[str], Optional[UrlSource]]:
    """Poll for the bridge URL, dismissing prompts along the way.

    Returns ``(url, source)`` or ``(None, None)`` if the deadline passes.
    """
    while time.time() < deadline:
        url, src = resolve_bridge_url(
            session_name, claude_home=claude_home, tmux_bin=tmux_bin
        )
        if url:
            return url, src
        _dismiss_trust_and_enable_remote_control(
            session_name,
            tmux_bin=tmux_bin,
            deadline=min(deadline, time.time() + 3),
            auto_remote_control=auto_remote_control,
        )
        time.sleep(0.5)
    return None, None


def _run_spawn(
    name: str,
    *,
    cwd: str,
    shell_cmd: str,
    claude_home: Path,
    tmux_bin: str,
    url_timeout_sec: float,
    auto_remote_control: bool,
    pane_log_path: Optional[Path],
    archive_ctx: Optional["_ArchiveCtx"] = None,
) -> SpawnResult:
    if not Path(cwd).is_dir():
        raise FileNotFoundError(f"cwd does not exist: {cwd}")
    tm.new_session(name, command=shell_cmd, binary=tmux_bin)
    if pane_log_path is not None:
        pane_log_path.parent.mkdir(parents=True, exist_ok=True)
        tm.pipe_pane_to_file(name, path=pane_log_path, binary=tmux_bin)

    if archive_ctx is not None:
        archive_ctx.emit_created(
            name=name, cwd=cwd, pane_log=pane_log_path, tmux_bin=tmux_bin
        )

    deadline = time.time() + url_timeout_sec
    url, src = _wait_for_url(
        name,
        claude_home=claude_home,
        tmux_bin=tmux_bin,
        deadline=deadline,
        auto_remote_control=auto_remote_control,
    )

    claude_pid = find_claude_pid(name, tmux_bin=tmux_bin)
    data = (
        cfs.read_ephemeral_session(claude_pid, claude_home=claude_home)
        if claude_pid is not None
        else None
    )
    result = SpawnResult(
        name=name,
        cwd=cwd,
        claude_pid=claude_pid,
        claude_session_id=(data or {}).get("sessionId"),
        bridge_session_id=(data or {}).get("bridgeSessionId"),
        url=url,
        url_source=src,
        warning=None
        if url
        else "Session created but no remote-control URL detected yet — try refreshing.",
    )
    if archive_ctx is not None and url:
        archive_ctx.emit_url_acquired(
            name=name,
            url=url,
            claude_session_id=result.claude_session_id,
            claude_pid=claude_pid,
        )
    return result


@dataclass
class _ArchiveCtx:
    """Internal adapter passed to ``_run_spawn`` for event emission.

    Kept private so ``xa.archive`` owns the schema end-to-end. Callers
    use :func:`spawn_session` / :func:`resume_session` with the public
    ``archive_store`` / ``pane_store`` / ``archive_id`` kwargs instead.
    """

    events_store: object
    archive_id: str

    def emit_created(
        self, *, name: str, cwd: str, pane_log: Optional[Path], tmux_bin: str
    ) -> None:
        from xa import archive as arch  # local import to avoid cycle

        tmux_ts: Optional[int] = None
        for t in tm.list_sessions(binary=tmux_bin):
            if t.name == name:
                tmux_ts = t.created
                break
        arch.append_created(
            self.events_store,
            id=self.archive_id,
            name=name,
            cwd=cwd,
            claude_bin=DEFAULT_CLAUDE_BIN,
            tmux_created_ts=tmux_ts,
            pane_log=str(pane_log) if pane_log else None,
        )

    def emit_url_acquired(
        self, *, name: str, url: str, claude_session_id, claude_pid
    ) -> None:
        from xa import archive as arch

        arch.append_url_acquired(
            self.events_store,
            id=self.archive_id,
            name=name,
            url=url,
            claude_session_id=claude_session_id,
            claude_pid=claude_pid,
        )


def new_archive_id() -> str:
    """Fresh 12-char hex id used as the key for pane logs + archive events."""
    return secrets.token_hex(6)


def _build_archive_ctx(
    *, archive_store, pane_store, archive_id: Optional[str]
) -> tuple[Optional[_ArchiveCtx], Optional[Path], Optional[str]]:
    if archive_store is None:
        return None, None, None
    sid = archive_id or new_archive_id()
    pane_log = pane_store.path_for(sid) if pane_store is not None else None
    return _ArchiveCtx(events_store=archive_store, archive_id=sid), pane_log, sid


def spawn_session(
    name: str,
    *,
    cwd: str,
    claude_bin: str = DEFAULT_CLAUDE_BIN,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
    url_timeout_sec: float = 120.0,
    auto_remote_control: bool = True,
    pane_log_path: Optional[Path] = None,
    archive_store=None,
    pane_store=None,
    archive_id: Optional[str] = None,
) -> SpawnResult:
    """Create a detached tmux session running ``claude`` in ``cwd``.

    Waits up to ``url_timeout_sec`` for the bridge URL to appear, dismissing
    the "trust this folder" prompt and issuing ``/remote-control`` as needed.

    If ``archive_store`` (and optionally ``pane_store``) are given, emits
    ``created`` + ``url_acquired`` events to the archive and pipes the pane
    output to a file under ``pane_store``.
    """
    ctx, ctx_pane_log, _sid = _build_archive_ctx(
        archive_store=archive_store, pane_store=pane_store, archive_id=archive_id
    )
    shell_cmd = f"cd {shlex.quote(cwd)} && exec {shlex.quote(claude_bin)}"
    return _run_spawn(
        name,
        cwd=cwd,
        shell_cmd=shell_cmd,
        claude_home=claude_home,
        tmux_bin=tmux_bin,
        url_timeout_sec=url_timeout_sec,
        auto_remote_control=auto_remote_control,
        pane_log_path=pane_log_path or ctx_pane_log,
        archive_ctx=ctx,
    )


def resume_session(
    claude_session_id: str,
    *,
    cwd: str,
    name: Optional[str] = None,
    claude_bin: str = DEFAULT_CLAUDE_BIN,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
    url_timeout_sec: float = 120.0,
    auto_remote_control: bool = True,
    pane_log_path: Optional[Path] = None,
    archive_store=None,
    pane_store=None,
    archive_id: Optional[str] = None,
) -> SpawnResult:
    """Launch ``claude --resume <id>`` in a new detached tmux session.

    ``--resume`` reuses the cwd's trusted-folder setting, so we skip the
    trust-prompt dismiss; the ``/remote-control`` handshake still runs.
    """
    if name is None:
        # Auto-name: <claude_session_id-short>-r{n}.
        live = {s.name for s in tm.list_sessions(binary=tmux_bin)}
        base = f"resumed-{claude_session_id[:8]}"
        name = next(
            (f"{base}-r{i}" for i in range(1, 100) if f"{base}-r{i}" not in live),
            base,
        )
    ctx, ctx_pane_log, _sid = _build_archive_ctx(
        archive_store=archive_store, pane_store=pane_store, archive_id=archive_id
    )
    shell_cmd = (
        f"cd {shlex.quote(cwd)} && "
        f"exec {shlex.quote(claude_bin)} --resume {shlex.quote(claude_session_id)}"
    )
    return _run_spawn(
        name,
        cwd=cwd,
        shell_cmd=shell_cmd,
        claude_home=claude_home,
        tmux_bin=tmux_bin,
        url_timeout_sec=url_timeout_sec,
        auto_remote_control=auto_remote_control,
        pane_log_path=pane_log_path or ctx_pane_log,
        archive_ctx=ctx,
    )
