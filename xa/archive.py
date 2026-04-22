"""Postmortem archive of Claude Code sessions spawned by ``xa``.

Every session that ``xa`` spawns emits ``created`` + ``url_acquired`` events
into an append-only JSONL log and its tmux pane is tee'd to a per-session
file via ``tmux pipe-pane``. When a session disappears from the live list,
``reconcile()`` appends a ``gone`` event with an inferred death time and a
classified reason.

Death-reason taxonomy (most specific wins):

- ``replaced``    — same tmux name, different ``tmux_created_ts`` (the
  original died and got taken over by a new session of the same name).
- ``missing``     — no pane log exists (session predates our logging or
  the log was cleaned up).
- ``interrupted`` — transcript shows the user-interrupt marker AND the
  pane tail shows a clean exit. **Ambiguous**: the same marker fires on
  bridge-WebSocket resets (phone standby, reconnect), not only on human
  ESC. Don't derive user intent from this alone.
- ``tool_crash``  — pane tail shows clean exit AND the last tool use
  exited with a non-zero code.
- ``clean_exit``  — pane tail contains ``"Resume this session with:"``.
- ``abrupt``      — killed / crashed / bridge-dropped with no clean-exit
  marker.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional

from xa import claude_fs as cfs
from xa import store as st
from xa import tmux as tm


DeathReason = Literal[
    "clean_exit",
    "abrupt",
    "interrupted",
    "tool_crash",
    "replaced",
    "missing",
]


_CLEAN_EXIT_MARKER = "Resume this session with:"


# --------------------------------------------------------------------------- #
# event emission
# --------------------------------------------------------------------------- #


def append_created(
    events: st.JsonLinesStore,
    *,
    id: str,
    name: str,
    cwd: str,
    claude_bin: str,
    tmux_created_ts: Optional[int] = None,
    pane_log: Optional[str] = None,
    resumed_from: Optional[str] = None,
    resumed_claude_session_id: Optional[str] = None,
) -> None:
    ev: dict = {
        "ts": time.time(),
        "event": "created",
        "id": id,
        "name": name,
        "cwd": cwd,
        "claude_bin": claude_bin,
    }
    if tmux_created_ts is not None:
        ev["tmux_created_ts"] = tmux_created_ts
    if pane_log is not None:
        ev["pane_log"] = pane_log
    if resumed_from is not None:
        ev["resumed_from"] = resumed_from
    if resumed_claude_session_id is not None:
        ev["resumed_claude_session_id"] = resumed_claude_session_id
    events.append(ev)


def append_url_acquired(
    events: st.JsonLinesStore,
    *,
    id: str,
    name: str,
    url: Optional[str] = None,
    claude_session_id: Optional[str] = None,
    claude_pid: Optional[int] = None,
) -> None:
    ev: dict = {
        "ts": time.time(),
        "event": "url_acquired",
        "id": id,
        "name": name,
    }
    if url:
        ev["url"] = url
    if claude_session_id:
        ev["claude_session_id"] = claude_session_id
    if claude_pid is not None:
        ev["claude_pid"] = claude_pid
    events.append(ev)


def append_gone(
    events: st.JsonLinesStore,
    *,
    id: str,
    name: str,
    reason: DeathReason,
    death_ts: Optional[float] = None,
    forensics: Optional[dict] = None,
) -> None:
    ev: dict = {
        "ts": time.time(),
        "event": "gone",
        "id": id,
        "name": name,
        "reason": reason,
    }
    if death_ts is not None:
        ev["death_ts"] = death_ts
    if forensics:
        ev["forensics"] = forensics
    events.append(ev)


# --------------------------------------------------------------------------- #
# death inference
# --------------------------------------------------------------------------- #


def _infer_pane_death(
    panes: st.FileStore, sid: str
) -> tuple[Optional[float], Literal["clean_exit", "abrupt", "missing", "unknown"]]:
    """Return ``(death_ts, pane_kind)`` by inspecting the pane log."""
    if sid not in panes:
        return None, "missing"
    mtime = panes.mtime(sid)
    try:
        tail = panes[sid][-4096:].decode("utf-8", errors="replace")
    except KeyError:
        return mtime, "unknown"
    return mtime, "clean_exit" if _CLEAN_EXIT_MARKER in tail else "abrupt"


def classify_death(
    pane_kind: Literal["clean_exit", "abrupt", "missing", "unknown"],
    *,
    replaced: bool = False,
    forensics: Optional[cfs.TranscriptForensics] = None,
) -> DeathReason:
    """Pick the most specific death reason from available signals."""
    if replaced:
        return "replaced"
    if pane_kind == "missing":
        return "missing"
    if pane_kind == "clean_exit":
        if forensics is not None and forensics.user_interrupted:
            return "interrupted"
        if (
            forensics is not None
            and forensics.last_tool_exit_code not in (None, 0)
        ):
            return "tool_crash"
        return "clean_exit"
    return "abrupt"


# --------------------------------------------------------------------------- #
# reconcile
# --------------------------------------------------------------------------- #


def _index_alive(
    events: st.JsonLinesStore,
) -> dict[str, dict]:
    """Fold event stream into ``{id: {name, cwd, tmux_created_ts, claude_session_id}}``
    for sessions that have a ``created`` event but no ``gone`` event yet.
    """
    alive: dict[str, dict] = {}
    for ev in events:
        sid = ev.get("id")
        if not sid:
            continue
        kind = ev.get("event")
        if kind == "created":
            alive[sid] = {
                "name": ev.get("name"),
                "cwd": ev.get("cwd"),
                "tmux_created_ts": ev.get("tmux_created_ts"),
            }
        elif kind == "url_acquired":
            if sid in alive and ev.get("claude_session_id"):
                alive[sid]["claude_session_id"] = ev["claude_session_id"]
        elif kind == "gone":
            alive.pop(sid, None)
    return alive


def reconcile(
    events: st.JsonLinesStore,
    panes: st.FileStore,
    live_sessions: Iterable[tm.TmuxSession],
    *,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
) -> list[dict]:
    """Emit ``gone`` events for archived sessions missing from ``live_sessions``.

    Returns the list of freshly-emitted events (handy for tests). Idempotent:
    calling twice with the same live list produces no new events the
    second time.
    """
    alive_by_id = _index_alive(events)
    live_by_name: dict[str, int] = {s.name: s.created for s in live_sessions}
    emitted: list[dict] = []

    for sid, meta in alive_by_id.items():
        name = meta["name"]
        archived_tmux_ts = meta.get("tmux_created_ts")
        live_ts = live_by_name.get(name)

        # A session is "gone" if (a) no tmux session with that name exists,
        # or (b) a tmux session with that name exists but was created at a
        # different timestamp (i.e., the original died and was replaced).
        is_gone = live_ts is None or (
            archived_tmux_ts is not None
            and abs(int(live_ts) - int(archived_tmux_ts)) > 2
        )
        if not is_gone:
            continue

        replaced = live_ts is not None and archived_tmux_ts is not None and (
            abs(int(live_ts) - int(archived_tmux_ts)) > 2
        )

        death_ts, pane_kind = _infer_pane_death(panes, sid)

        forensics: Optional[cfs.TranscriptForensics] = None
        cwd = meta.get("cwd")
        cs_id = meta.get("claude_session_id")
        if cwd and cs_id:
            path = cfs.transcript_path(cwd, cs_id, claude_home=claude_home)
            if path is not None:
                try:
                    forensics = cfs.transcript_forensics(path)
                except OSError:
                    forensics = None

        reason = classify_death(pane_kind, replaced=replaced, forensics=forensics)

        forensics_summary: Optional[dict] = None
        if forensics is not None and any(
            [
                forensics.last_tool_name,
                forensics.last_tool_exit_code is not None,
                forensics.user_interrupted,
            ]
        ):
            forensics_summary = {
                "last_tool_name": forensics.last_tool_name,
                "last_tool_command": forensics.last_tool_command,
                "last_tool_exit_code": forensics.last_tool_exit_code,
                "user_interrupted": forensics.user_interrupted,
            }

        append_gone(
            events,
            id=sid,
            name=name,
            reason=reason,
            death_ts=death_ts,
            forensics=forensics_summary,
        )
        emitted.append(
            {"id": sid, "name": name, "reason": reason, "death_ts": death_ts}
        )
    return emitted


# --------------------------------------------------------------------------- #
# records (reduce events into per-session summaries)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ArchiveRecord:
    id: str
    name: Optional[str]
    cwd: Optional[str]
    created: Optional[float]
    url: Optional[str]
    gone: Optional[float]
    gone_detected: Optional[float]
    gone_reason: Optional[DeathReason]
    pane_log_bytes: int
    claude_session_id: Optional[str]
    forensics: Optional[dict]


def records(
    events: st.JsonLinesStore, panes: st.FileStore
) -> list[ArchiveRecord]:
    """Return per-session summaries, newest-first (by creation time)."""
    by_id: dict[str, dict] = {}
    for ev in events:
        sid = ev.get("id")
        if not sid:
            continue
        rec = by_id.setdefault(
            sid,
            {
                "id": sid,
                "name": None,
                "cwd": None,
                "created": None,
                "url": None,
                "gone": None,
                "gone_detected": None,
                "gone_reason": None,
                "claude_session_id": None,
                "forensics": None,
            },
        )
        kind = ev.get("event")
        if kind == "created":
            rec["name"] = ev.get("name")
            rec["cwd"] = ev.get("cwd")
            rec["created"] = ev.get("ts")
        elif kind == "url_acquired":
            if ev.get("url"):
                rec["url"] = ev.get("url")
            if ev.get("claude_session_id"):
                rec["claude_session_id"] = ev["claude_session_id"]
        elif kind == "gone":
            # Prefer inferred death time over reconcile-run time.
            rec["gone"] = ev.get("death_ts") or ev.get("ts")
            rec["gone_detected"] = ev.get("ts")
            rec["gone_reason"] = ev.get("reason")
            if ev.get("forensics"):
                rec["forensics"] = ev["forensics"]

    out: list[ArchiveRecord] = []
    for rec in by_id.values():
        out.append(
            ArchiveRecord(
                id=rec["id"],
                name=rec["name"],
                cwd=rec["cwd"],
                created=rec["created"],
                url=rec["url"],
                gone=rec["gone"],
                gone_detected=rec["gone_detected"],
                gone_reason=rec["gone_reason"],
                pane_log_bytes=panes.size(rec["id"]),
                claude_session_id=rec["claude_session_id"],
                forensics=rec["forensics"],
            )
        )
    out.sort(key=lambda r: r.created or 0, reverse=True)
    return out
