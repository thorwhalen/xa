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
- ``oom_killed``  — last tool exited with 137 (SIGKILL) AND the pane log
  tail contains a kernel/shell OOM marker (``Killed``, ``Out of memory``,
  ``MemoryError``). Strongest single signal we can derive without root —
  the kernel's "Killed" message rides through the same tty bash uses to
  print SIGKILL notices, so it ends up in the tee'd pane log.
- ``tool_crash``  — pane tail shows clean exit AND the last tool use
  exited with a non-zero code (and we couldn't promote to ``oom_killed``).
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
    "oom_killed",
    "replaced",
    "missing",
]


_CLEAN_EXIT_MARKER = "Resume this session with:"

# Substrings that indicate a SIGKILL caused by the Linux OOM killer.
# Bash prints "Killed" to stderr when one of its children dies on signal 9
# and the kernel's oom-kill announcement reaches the same tty. Python
# allocators raise MemoryError before the kill; we match that too in case
# the host had enough headroom to surface it.
_OOM_PANE_MARKERS = ("Killed", "Out of memory", "MemoryError")


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


def append_label(events: st.JsonLinesStore, *, id: str, label: Optional[str]) -> None:
    """Set or clear a user-supplied display label for a session.

    ``id`` can be an archive id, a tmux session name, or a
    ``claude_session_id`` — whatever key the caller will use to look it
    up later. Empty-string or ``None`` label clears any prior label.
    """
    events.append(
        {
            "ts": time.time(),
            "event": "labeled",
            "id": id,
            "label": label or "",
        }
    )


def append_hidden(events: st.JsonLinesStore, *, id: str, hidden: bool) -> None:
    """Mark an archived session as hidden (or un-hide it)."""
    events.append(
        {
            "ts": time.time(),
            "event": "hidden",
            "id": id,
            "hidden": bool(hidden),
        }
    )


# --------------------------------------------------------------------------- #
# death inference
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PaneInspection:
    """Read-once view of a pane log tail used by death classification."""

    death_ts: Optional[float]
    kind: Literal["clean_exit", "abrupt", "missing", "unknown"]
    oom_markers: tuple[str, ...]  # which _OOM_PANE_MARKERS were found
    tail: str  # the decoded tail (may be empty)


def _inspect_pane(panes: st.FileStore, sid: str) -> PaneInspection:
    """Read the pane tail once and extract every signal we use downstream."""
    if sid not in panes:
        return PaneInspection(death_ts=None, kind="missing", oom_markers=(), tail="")
    mtime = panes.mtime(sid)
    try:
        tail = panes[sid][-4096:].decode("utf-8", errors="replace")
    except KeyError:
        return PaneInspection(death_ts=mtime, kind="unknown", oom_markers=(), tail="")
    kind: Literal["clean_exit", "abrupt"] = (
        "clean_exit" if _CLEAN_EXIT_MARKER in tail else "abrupt"
    )
    oom = tuple(m for m in _OOM_PANE_MARKERS if m in tail)
    return PaneInspection(death_ts=mtime, kind=kind, oom_markers=oom, tail=tail)


# Back-compat shim — older callers (and tests) used the simpler 2-tuple form.
def _infer_pane_death(
    panes: st.FileStore, sid: str
) -> tuple[Optional[float], Literal["clean_exit", "abrupt", "missing", "unknown"]]:
    insp = _inspect_pane(panes, sid)
    return insp.death_ts, insp.kind


def classify_death(
    pane_kind: Literal["clean_exit", "abrupt", "missing", "unknown"],
    *,
    replaced: bool = False,
    forensics: Optional[cfs.TranscriptForensics] = None,
    oom_markers: tuple[str, ...] = (),
) -> DeathReason:
    """Pick the most specific death reason from available signals.

    ``oom_markers`` is the tuple of OOM-shaped strings observed in the
    pane tail (see ``_OOM_PANE_MARKERS``). When the last tool exited
    with 137 and at least one marker is present, we promote the verdict
    to ``oom_killed`` — that pair is the strongest single signal we can
    get without reading kernel logs (which would need root and is not
    portable).
    """
    if replaced:
        return "replaced"
    if pane_kind == "missing":
        return "missing"
    last_exit = forensics.last_tool_exit_code if forensics is not None else None
    if last_exit == 137 and oom_markers:
        return "oom_killed"
    if pane_kind == "clean_exit":
        if forensics is not None and forensics.user_interrupted:
            return "interrupted"
        if last_exit not in (None, 0):
            return "tool_crash"
        return "clean_exit"
    return "abrupt"


# --------------------------------------------------------------------------- #
# diagnosis (human-readable hints from the same signals classify_death uses)
# --------------------------------------------------------------------------- #


def synthesize_diagnosis(
    *,
    state: Literal["live", "archived", "transcript_only"],
    reason: Optional[DeathReason] = None,
    forensics: Optional[cfs.TranscriptForensics] = None,
    oom_markers: tuple[str, ...] = (),
) -> str:
    """One-paragraph plain-English hint about what likely happened.

    Designed to be useful to both a human reading the UI and an LLM agent
    deciding whether to retry or escalate. The hint surfaces the actionable
    bit (e.g., "add swap", "re-run with --resume") rather than just naming
    the verdict — the structured fields next to it already do that.
    """
    if state == "live":
        return "Session is currently live. No postmortem to render."

    last_exit = forensics.last_tool_exit_code if forensics else None
    last_tool = forensics.last_tool_name if forensics else None
    last_cmd = forensics.last_tool_command if forensics else None
    user_marker = bool(forensics and forensics.user_interrupted)

    # Exit code 137 = SIGKILL. On a Linux server with Claude Code's tool
    # subtree, the dominant cause is the kernel OOM killer (other causes:
    # manual `kill -9`, container OOM cgroup, OS shutdown). When the pane
    # also shows "Killed" / "Out of memory", confidence is very high
    # (oom_killed). When it doesn't — usually because the kill hit a deep
    # grandchild and the kernel message went to dmesg, not the tty — we
    # still surface the likelihood since 137 alone is suggestive.
    sigkill = last_exit == 137
    pane_oom = bool(oom_markers)

    if reason == "oom_killed" or (sigkill and pane_oom):
        markers = ", ".join(repr(m) for m in oom_markers) or "none"
        cmd_part = f" while running `{last_cmd}`" if last_cmd else ""
        return (
            f"Last tool ({last_tool or 'unknown'}) exited 137{cmd_part} and the "
            f"pane log contains OOM-shaped markers ({markers}). Almost certainly "
            f"the Linux OOM killer. Mitigations: add swap, lower the process's "
            f"peak memory (stream instead of buffering), or split the work into "
            f"smaller batches. Resume after fixing the root cause — re-running "
            f"the same command on the same host will OOM again."
        )

    def _sigkill_caveat() -> str:
        return (
            " Exit 137 is SIGKILL — on a Linux host the dominant cause is the "
            "kernel OOM killer (the OOM message goes to dmesg, not the tty, so "
            "the pane log alone may not show it). Check `journalctl -k --since` "
            "around the death time and `free -h` for headroom; if confirmed, "
            "add swap or lower the process's peak memory before resuming."
        )

    if reason == "interrupted":
        cmd_part = f" while running `{last_cmd}`" if last_cmd else ""
        base = (
            f"Claude's tool runner emitted the user-interrupt marker"
            f"{cmd_part}. This marker is ambiguous: it fires on a real ESC, on "
            f"phone-standby/bridge resets, and on remote-stop. If no human input "
            f"is in the transcript leading up to it, blame infrastructure "
            f"(bridge WebSocket reset) rather than the user."
        )
        return base + _sigkill_caveat() if sigkill else base

    if reason == "tool_crash":
        cmd_part = f" `{last_cmd}`" if last_cmd else ""
        base = (
            f"Last tool ({last_tool or 'unknown'}){cmd_part} exited "
            f"{last_exit}. Pane closed cleanly afterwards, so claude itself shut "
            f"down rather than the orchestrator killing it. Open the transcript "
            f"to see the tool result."
        )
        return base + _sigkill_caveat() if sigkill else base

    if reason == "clean_exit":
        return (
            "Session exited cleanly (claude printed its 'Resume this session "
            "with:' marker). Safe to resume from this transcript."
        )

    if reason == "abrupt":
        hint = (
            "Session disappeared without a clean-exit marker. Common causes: "
            "tmux pane killed, the host rebooted, or claude crashed."
        )
        if user_marker:
            hint += (
                " The transcript also contains an interrupt marker — "
                "may have been a bridge reset cascading into a kill."
            )
        return hint

    if reason == "replaced":
        return (
            "A new tmux session took over this name. The original is gone; "
            "this archive entry is its postmortem."
        )

    if reason == "missing":
        return (
            "No pane log exists for this session — predates xa's logging or "
            "the log was cleaned up. We can still resume from the transcript."
        )

    return "No classified reason available; inspect the pane log and transcript."


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
        # different timestamp (the original died and got replaced).
        replaced = (
            live_ts is not None
            and archived_tmux_ts is not None
            and abs(int(live_ts) - int(archived_tmux_ts)) > 2
        )
        is_gone = live_ts is None or replaced
        if not is_gone:
            continue

        insp = _inspect_pane(panes, sid)
        death_ts, pane_kind = insp.death_ts, insp.kind

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

        reason = classify_death(
            pane_kind,
            replaced=replaced,
            forensics=forensics,
            oom_markers=insp.oom_markers,
        )

        forensics_summary: Optional[dict] = None
        if (
            forensics is not None
            and any(
                [
                    forensics.last_tool_name,
                    forensics.last_tool_exit_code is not None,
                    forensics.user_interrupted,
                ]
            )
            or insp.oom_markers
        ):
            forensics_summary = {
                "last_tool_name": forensics.last_tool_name if forensics else None,
                "last_tool_command": forensics.last_tool_command if forensics else None,
                "last_tool_exit_code": (
                    forensics.last_tool_exit_code if forensics else None
                ),
                "user_interrupted": (
                    forensics.user_interrupted if forensics else False
                ),
            }
            if insp.oom_markers:
                forensics_summary["oom_signals"] = list(insp.oom_markers)

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
    label: Optional[str] = None  # user-set display label overlay
    hidden: bool = False  # user-set "hide from default view"


def overlays(events: st.JsonLinesStore) -> dict[str, dict]:
    """Fold ``labeled`` / ``hidden`` events into ``{id: {label, hidden}}``.

    Later events win. Useful when rendering live sessions too — callers
    look up by whatever id they know (archive id, claude_session_id,
    tmux name) and apply the overlay if present.
    """
    out: dict[str, dict] = {}
    for ev in events:
        kind = ev.get("event")
        sid = ev.get("id")
        if not sid:
            continue
        if kind == "labeled":
            slot = out.setdefault(sid, {})
            label = ev.get("label") or ""
            slot["label"] = label if label else None
        elif kind == "hidden":
            slot = out.setdefault(sid, {})
            slot["hidden"] = bool(ev.get("hidden"))
    return out


def records(events: st.JsonLinesStore, panes: st.FileStore) -> list[ArchiveRecord]:
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

    overlay = overlays(events)

    out: list[ArchiveRecord] = []
    for rec in by_id.values():
        ov = overlay.get(rec["id"], {})
        # Also honor an overlay keyed by the session's archive *name* —
        # lets a "rename live" flow that didn't yet know the archive id
        # survive a later lookup.
        if rec["name"] and rec["name"] in overlay:
            ov = {**overlay[rec["name"]], **ov}
        # And by claude_session_id — so overlays set on a transcript id
        # follow the session into its archive record.
        if rec["claude_session_id"] and rec["claude_session_id"] in overlay:
            ov = {**overlay[rec["claude_session_id"]], **ov}
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
                label=ov.get("label"),
                hidden=bool(ov.get("hidden", False)),
            )
        )
    out.sort(key=lambda r: r.created or 0, reverse=True)
    return out
