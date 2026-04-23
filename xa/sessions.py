"""The unified ``Session`` domain model and multi-host discovery.

This layer is the single thing CLI / HTTP / UI renderers iterate over.
Discovery and actions are delegated to :mod:`xa.hosts`; this module
composes them into a host-agnostic API.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Literal, Mapping, Optional

from xa import claude_cli as ccli
from xa import claude_fs as cfs


SessionState = Literal["live", "archived", "transcript_only"]


@dataclass(frozen=True)
class Session:
    """Canonical session record — valid across hosts and states.

    Fields that don't apply to a given state are ``None``. Discovery
    functions return fresh instances; callers should treat it as
    immutable.
    """

    # identity
    id: str
    claude_session_id: Optional[str]
    bridge_session_id: Optional[str]
    # location
    host: str
    cwd: Optional[str]
    project_slug: str
    # status
    state: SessionState
    live_pid: Optional[int]
    tmux_name: Optional[str]
    # content
    name: Optional[str]
    summary: Optional[str]
    first_user_message: Optional[str]
    turn_count: int
    forked_from: Optional[str]
    # times
    created: Optional[float]
    modified: Optional[float]
    # derived
    url: Optional[str]
    url_source: Optional[Literal["session_file", "pane_capture"]]
    # provenance
    transcript_path: Optional[Path]


def _session_from_transcript_meta(
    meta: cfs.TranscriptMeta, *, host: str = "local"
) -> Session:
    """Build a transcript-only ``Session`` from a parsed transcript."""
    return Session(
        id=meta.session_id or meta.path.stem,
        claude_session_id=meta.session_id or meta.path.stem,
        bridge_session_id=None,
        host=host,
        cwd=meta.cwd,
        project_slug=meta.project_slug,
        state="transcript_only",
        live_pid=None,
        tmux_name=None,
        name=meta.custom_title,
        summary=meta.summary,
        first_user_message=meta.first_user_message,
        turn_count=meta.turn_count,
        forked_from=meta.forked_from,
        created=meta.created,
        modified=meta.modified,
        url=None,
        url_source=None,
        transcript_path=meta.path,
    )


# --------------------------------------------------------------------------- #
# multi-host discovery
# --------------------------------------------------------------------------- #


def _default_hosts() -> dict[str, "Host"]:  # noqa: F821
    # Local import — avoid the sessions ↔ hosts cycle at module load.
    from xa.hosts import default_hosts

    return default_hosts()


def list_sessions(
    *,
    hosts: Optional[Iterable] = None,
    project: Optional[str] = None,
    include_forks: bool = True,
    include_live: bool = True,
    state: Optional[SessionState] = None,
    limit: Optional[int] = None,
    # Legacy shortcut for the single-local-host path used by tests.
    claude_home: Optional[Path] = None,
    tmux_bin: Optional[str] = None,
) -> list[Session]:
    """Return sessions sorted by recency (live first, then newest-modified).

    When ``hosts`` is ``None`` we default to a single :class:`LocalHost`.
    If ``claude_home`` or ``tmux_bin`` are given, they're forwarded into
    that default :class:`LocalHost` — handy for fixture-based tests.

    Filters:

    - ``project`` — substring match against ``cwd`` (case-insensitive)
    - ``include_forks=False`` — drop sessions with a ``forked_from``
    - ``include_live=False`` — skip live discovery (per host)
    - ``state`` — one of ``"live"`` / ``"archived"`` / ``"transcript_only"``
    """
    if hosts is None:
        if claude_home is not None or tmux_bin is not None:
            from xa.hosts import LocalHost

            kwargs: dict = {}
            if claude_home is not None:
                kwargs["claude_home"] = claude_home
            if tmux_bin is not None:
                kwargs["tmux_bin"] = tmux_bin
            hosts = [LocalHost(**kwargs)]
        else:
            hosts = list(_default_hosts().values())

    rows: list[Session] = []
    for h in hosts:
        for s in h.iter_sessions(include_live=include_live):
            rows.append(s)

    if project is not None:
        needle = project.lower()
        rows = [s for s in rows if s.cwd and needle in s.cwd.lower()]
    if not include_forks:
        rows = [s for s in rows if s.forked_from is None]
    if state is not None:
        rows = [s for s in rows if s.state == state]

    def _recency(s: Session) -> float:
        base = 2e10 if s.state == "live" else 0.0
        return base + (s.modified or s.created or 0.0)

    rows.sort(key=_recency, reverse=True)
    if limit is not None:
        rows = rows[:limit]
    return rows


def iter_local_sessions(
    *,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    project_slug: Optional[str] = None,
    include_live: bool = True,
    tmux_bin: Optional[str] = None,
) -> Iterator[Session]:
    """Legacy local-only iterator.

    Retained for backward compatibility and because some tests construct
    sessions directly from a fake ``~/.claude/``. Wraps a fresh
    :class:`LocalHost`.
    """
    from xa.hosts import LocalHost

    kwargs: dict = {"claude_home": claude_home}
    if tmux_bin is not None:
        kwargs["tmux_bin"] = tmux_bin
    yield from LocalHost(**kwargs).iter_sessions(
        project_slug=project_slug, include_live=include_live
    )


# --------------------------------------------------------------------------- #
# lookup + actions
# --------------------------------------------------------------------------- #


def get_session(
    session_id: str,
    *,
    hosts: Optional[Iterable] = None,
    claude_home: Optional[Path] = None,
) -> Optional[Session]:
    """Find one session by full ID or unique prefix.

    Raises ``LookupError`` if the prefix is ambiguous.
    """
    rows = list_sessions(
        hosts=hosts,
        claude_home=claude_home,
        limit=None,
    )
    exact = [s for s in rows if s.id == session_id]
    if exact:
        return exact[0]
    prefix = [s for s in rows if s.id.startswith(session_id)]
    if not prefix:
        return None
    if len(prefix) > 1:
        names = ", ".join(s.id[:12] for s in prefix[:5])
        more = "" if len(prefix) <= 5 else f" (+{len(prefix) - 5} more)"
        raise LookupError(f"Ambiguous session id '{session_id}' matches: {names}{more}")
    return prefix[0]


def kill_session(
    session: Session,
    *,
    hosts: Optional[Mapping[str, object]] = None,
) -> None:
    """Kill the backing tmux session of a live ``Session``."""
    if session.state != "live" or not session.tmux_name:
        raise ValueError("session is not live — nothing to kill")
    h = _host_for(session, hosts)
    h.kill(session.tmux_name)


def resume(
    session: Session,
    *,
    cwd: Optional[str] = None,
    name: Optional[str] = None,
    hosts: Optional[Mapping[str, object]] = None,
    **opts,
) -> ccli.SpawnResult:
    """Resume a transcript / archived session on its originating host."""
    if not session.claude_session_id:
        raise ValueError("session has no claude_session_id — cannot resume")
    target_cwd = cwd or session.cwd
    if not target_cwd:
        raise ValueError("cannot infer cwd — pass cwd=...")
    h = _host_for(session, hosts)
    return h.resume(session.claude_session_id, cwd=target_cwd, name=name, **opts)


def _host_for(session: Session, hosts: Optional[Mapping[str, object]]) -> object:
    """Resolve ``session.host`` to a concrete Host instance."""
    if hosts is None:
        hosts = _default_hosts()
    h = hosts.get(session.host)
    if h is None:
        # Fall back to a default local host when the session's host name
        # isn't in the registry — keeps single-host CLI calls working.
        from xa.hosts import LocalHost

        h = LocalHost(name=session.host)
    return h
