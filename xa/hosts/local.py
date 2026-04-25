"""Local in-process host.

All discovery and actions hit the local filesystem / tmux / ``claude``
binary directly. Most ``xa`` users never instantiate any other host.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Optional

from xa import claude_cli as ccli
from xa import claude_fs as cfs
from xa import tmux as tm


# Seconds a live session may exist without a transcript before we flag
# it as likely wedged on a startup-time TUI prompt. Two minutes is well
# past any normal first-turn latency.
PRE_FIRST_TURN_GRACE_SEC = 120


class LocalHost:
    """The machine ``xa`` is running on."""

    kind = "local"

    def __init__(
        self,
        name: str = "local",
        *,
        claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
        claude_bin: str = ccli.DEFAULT_CLAUDE_BIN,
        tmux_bin: str = tm.DEFAULT_TMUX_BIN,
    ) -> None:
        self.name = name
        self.claude_home = Path(claude_home)
        self.claude_bin = claude_bin
        self.tmux_bin = tmux_bin

    # ------------------------------------------------------------------ #
    # discovery
    # ------------------------------------------------------------------ #

    def iter_sessions(
        self,
        *,
        project_slug: Optional[str] = None,
        include_live: bool = True,
    ) -> Iterator["Session"]:  # noqa: F821 — forward-ref
        # Local import avoids an import cycle (sessions → hosts → local).
        from xa.sessions import Session, _session_from_transcript_meta

        live_by_cs_id: dict[str, dict] = {}
        tmux_by_pid: dict[int, tm.TmuxSession] = {}
        if include_live:
            for eph in cfs.iter_ephemeral_sessions(claude_home=self.claude_home):
                cs = eph.get("sessionId")
                if cs:
                    live_by_cs_id[cs] = eph
            for t in tm.list_sessions(binary=self.tmux_bin):
                pid = ccli.find_claude_pid(t.name, tmux_bin=self.tmux_bin)
                if pid is not None:
                    tmux_by_pid[pid] = t

        emitted: set[str] = set()
        for path in cfs.iter_transcript_files(
            claude_home=self.claude_home, project_slug=project_slug
        ):
            meta = cfs.transcript_metadata(path)
            base = _session_from_transcript_meta(meta, host=self.name)
            cs_id = base.claude_session_id
            if cs_id and cs_id in live_by_cs_id:
                eph = live_by_cs_id[cs_id]
                pid = eph.get("pid")
                tmux_row = tmux_by_pid.get(pid) if isinstance(pid, int) else None
                bridge = eph.get("bridgeSessionId")
                yield replace(
                    base,
                    state="live",
                    live_pid=pid if isinstance(pid, int) else None,
                    tmux_name=tmux_row.name if tmux_row else None,
                    bridge_session_id=bridge,
                    url=f"{ccli.CLAUDE_WEB_BASE}/{bridge}" if bridge else None,
                    url_source="session_file" if bridge else None,
                )
                emitted.add(cs_id)
            else:
                yield base

        # Live sessions without a transcript yet (just-spawned, or
        # wedged on a startup-time prompt and never made it to the
        # first turn).
        now = time.time()
        for cs_id, eph in live_by_cs_id.items():
            if cs_id in emitted:
                continue
            pid = eph.get("pid")
            tmux_row = tmux_by_pid.get(pid) if isinstance(pid, int) else None
            bridge = eph.get("bridgeSessionId")
            cwd = eph.get("cwd")
            slug = cfs.encode_project_slug(cwd) if cwd else ""
            created = (
                eph.get("startedAt") / 1000
                if isinstance(eph.get("startedAt"), (int, float))
                else None
            )
            pre_first_turn = (
                created is not None
                and (now - created) > PRE_FIRST_TURN_GRACE_SEC
            )
            yield Session(
                id=cs_id,
                claude_session_id=cs_id,
                bridge_session_id=bridge,
                host=self.name,
                cwd=cwd,
                project_slug=slug,
                state="live",
                live_pid=pid if isinstance(pid, int) else None,
                tmux_name=tmux_row.name if tmux_row else None,
                name=None,
                summary=None,
                first_user_message=None,
                turn_count=0,
                forked_from=None,
                created=created,
                modified=None,
                url=f"{ccli.CLAUDE_WEB_BASE}/{bridge}" if bridge else None,
                url_source="session_file" if bridge else None,
                transcript_path=None,
                pre_first_turn=pre_first_turn,
            )

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #

    def spawn(self, name: str, *, cwd: str, **opts) -> ccli.SpawnResult:
        # Host-level defaults are overridable by the caller via opts.
        # Without setdefault, ``**opts`` would collide with the explicit
        # ``claude_bin=`` below (TypeError: got multiple values for …).
        opts.setdefault("claude_bin", self.claude_bin)
        opts.setdefault("claude_home", self.claude_home)
        opts.setdefault("tmux_bin", self.tmux_bin)
        return ccli.spawn_session(name, cwd=cwd, **opts)

    def resume(self, claude_session_id: str, *, cwd: str, **opts) -> ccli.SpawnResult:
        opts.setdefault("claude_bin", self.claude_bin)
        opts.setdefault("claude_home", self.claude_home)
        opts.setdefault("tmux_bin", self.tmux_bin)
        return ccli.resume_session(claude_session_id, cwd=cwd, **opts)

    def kill(self, name: str) -> None:
        tm.kill_session(name, binary=self.tmux_bin)

    def capture_pane(self, name: str, *, lines: int = 200) -> str:
        return tm.capture_pane(name, lines=lines, binary=self.tmux_bin)

    def sync(self, *, force: bool = False) -> None:
        """No-op — local has nothing to sync."""
