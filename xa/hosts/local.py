"""Local in-process host.

All discovery and actions hit the local filesystem / tmux / ``claude``
binary directly. Most ``xa`` users never instantiate any other host.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterator, Optional

from xa import claude_cli as ccli
from xa import claude_fs as cfs
from xa import revive as rv
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
        alive_predicate: Optional[Callable[[dict], bool]] = None,
    ) -> None:
        self.name = name
        self.claude_home = Path(claude_home)
        self.claude_bin = claude_bin
        self.tmux_bin = tmux_bin
        # DI seam: how to decide an ephemeral session dict is backed by a
        # real process. Defaults to the /proc-verified check; tests (and
        # exotic hosts) inject their own. Resolved at construction time so
        # a monkeypatched ``cfs.ephemeral_session_alive`` still takes.
        self.alive_predicate = alive_predicate or cfs.ephemeral_session_alive

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
                # A stale ephemeral file (claude crashed / was killed /
                # host rebooted) must not surface as a live session — the
                # transcript path below still emits it as transcript_only.
                if cs and self.alive_predicate(eph):
                    live_by_cs_id[cs] = eph
            for t in tm.list_sessions(binary=self.tmux_bin):
                pid = ccli.find_claude_pid(t.name, tmux_bin=self.tmux_bin)
                if pid is not None:
                    tmux_by_pid[pid] = t

        def _tmux_target_for(eph: dict) -> tuple[Optional[str], Optional[str]]:
            """Return ``(tmux_name, tmux_pane)`` for an ephemeral session.

            ``tmux_name`` (session-scoped: kill decisions, attach hints)
            comes from the pid walk, or from the session part of the
            pane ref newer claudes record in the ephemeral file.
            ``tmux_pane`` is that full pane ref ("name:@w.%p") — the only
            target that reliably addresses the *claude* pane in a
            multi-window workspace.
            """
            pane_ref = eph.get("tmux")
            tmux_pane = (
                pane_ref if isinstance(pane_ref, str) and ":" in pane_ref else None
            )
            pid = eph.get("pid")
            tmux_row = tmux_by_pid.get(pid) if isinstance(pid, int) else None
            if tmux_row is not None:
                return tmux_row.name, tmux_pane
            if tmux_pane:
                return tmux_pane.split(":", 1)[0] or None, tmux_pane
            return None, None

        def _attention_for(
            eph: dict, tmux_name: Optional[str], tmux_pane: Optional[str]
        ) -> tuple[Optional[str], Optional[str]]:
            """Adverse-TUI classification — only for bridgeless sessions.

            A bridged session's pane renders conversation text, which can
            legitimately mention /login; classifying it would produce
            false alarms. Captures the exact claude pane when known — a
            bare session name resolves to the *active* pane, which could
            be an unrelated window of a shared workspace.

            Delegates to :mod:`xa.revive`, the one rule engine that reads
            a claude pane, so a listing and ``xa revive`` can never
            disagree about what a pane says.
            """
            target = tmux_pane or tmux_name
            if eph.get("bridgeSessionId") or not target:
                return None, None
            pane = tm.capture_pane(target, lines=60, binary=self.tmux_bin)
            pid = eph.get("pid")
            ref = rv.PaneRef(
                target=target, claude_pid=pid if isinstance(pid, int) else None
            )
            verdict = rv.classify(rv.Probe(text=pane.lower(), ref=ref))
            return verdict, rv.hint_for(verdict, tmux_name=tmux_name)

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
                bridge = eph.get("bridgeSessionId")
                tmux_name, tmux_pane = _tmux_target_for(eph)
                attention, attention_hint = _attention_for(eph, tmux_name, tmux_pane)
                yield replace(
                    base,
                    state="live",
                    live_pid=pid if isinstance(pid, int) else None,
                    tmux_name=tmux_name,
                    tmux_pane=tmux_pane,
                    bridge_session_id=bridge,
                    name=base.name or eph.get("name"),
                    url=f"{ccli.CLAUDE_WEB_BASE}/{bridge}" if bridge else None,
                    url_source="session_file" if bridge else None,
                    attention=attention,
                    attention_hint=attention_hint,
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
            bridge = eph.get("bridgeSessionId")
            tmux_name, tmux_pane = _tmux_target_for(eph)
            attention, attention_hint = _attention_for(eph, tmux_name, tmux_pane)
            cwd = eph.get("cwd")
            slug = cfs.encode_project_slug(cwd) if cwd else ""
            created = (
                eph.get("startedAt") / 1000
                if isinstance(eph.get("startedAt"), (int, float))
                else None
            )
            pre_first_turn = (
                created is not None and (now - created) > PRE_FIRST_TURN_GRACE_SEC
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
                tmux_name=tmux_name,
                name=eph.get("name"),
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
                attention=attention,
                attention_hint=attention_hint,
                tmux_pane=tmux_pane,
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
