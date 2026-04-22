"""SSH-backed host.

Mirrors the cc-sessions transport pattern: pull the remote's
``~/.claude/`` tree into a local cache via ``rsync -az --delete -e ssh``,
then read the cache with the same ``claude_fs`` code paths we use for
local discovery. Actions (``spawn`` / ``resume`` / ``kill`` / tmux queries)
dispatch to the remote via ``ssh <host> <cmd>``.

No Python SSH library is pulled in: we shell out to the system ``ssh`` /
``rsync`` binaries, which means the user's ``~/.ssh/config``, agent, and
host keys Just Work. Install overhead: zero.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Optional

from xa import claude_cli as ccli
from xa import claude_fs as cfs
from xa import tmux as tm


DEFAULT_CACHE_DIR = Path.home() / ".cache" / "xa" / "remotes"


class SSHHost:
    """Transcripts via rsync; actions via ssh exec.

    Parameters
    ----------
    name:
        Logical name used in ``Session.host`` and elsewhere.
    host:
        SSH hostname / alias. Matches cc-sessions: either an
        ``~/.ssh/config`` alias (``devbox``) or a raw host (``1.2.3.4``).
    user:
        Optional SSH user for raw hosts.
    remote_claude_home:
        Remote path to ``~/.claude/``. Defaults to ``~/.claude`` which
        resolves relative to the SSH user's home on the remote side.
    cache_dir:
        Where to stage the rsync'd tree locally.
    stale_threshold_sec:
        Re-sync when the cached copy is older than this. ``0`` means
        always sync.
    """

    kind = "ssh"

    def __init__(
        self,
        name: str,
        *,
        host: str,
        user: Optional[str] = None,
        remote_claude_home: str = "~/.claude",
        cache_dir: Path = DEFAULT_CACHE_DIR,
        stale_threshold_sec: int = 3600,
        claude_bin: str = ccli.DEFAULT_CLAUDE_BIN,
        tmux_bin: str = tm.DEFAULT_TMUX_BIN,
        ssh_bin: str = "ssh",
        rsync_bin: str = "rsync",
    ) -> None:
        self.name = name
        self.host = host
        self.user = user
        self.remote_claude_home = remote_claude_home
        self.cache_dir = Path(cache_dir) / name
        self.stale_threshold_sec = stale_threshold_sec
        self.claude_bin = claude_bin
        self.tmux_bin = tmux_bin
        self.ssh_bin = ssh_bin
        self.rsync_bin = rsync_bin

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #

    @property
    def _target(self) -> str:
        """``user@host`` or ``host`` for the rsync/ssh source spec."""
        return f"{self.user}@{self.host}" if self.user else self.host

    def _remote_path(self, rel: str) -> str:
        """Build a remote path spec for ``rsync``/``ssh``."""
        return f"{self._target}:{self.remote_claude_home}/{rel}"

    def _rsync_cmd(self, rel_src: str, dest: Path) -> list[str]:
        """Build the rsync argv for a directory pull.

        The trailing slash on the source is significant — it copies
        *contents*, not the directory itself, matching cc-sessions'
        convention.
        """
        return [
            self.rsync_bin,
            "-a", "-z", "--delete",
            "-e", self.ssh_bin,
            f"{self._remote_path(rel_src)}/",
            f"{dest}/",
        ]

    def _ssh_cmd(self, remote_cmd: str) -> list[str]:
        return [self.ssh_bin, self._target, remote_cmd]

    def _is_stale(self, dest: Path) -> bool:
        if not dest.exists():
            return True
        if self.stale_threshold_sec <= 0:
            return True
        try:
            age = time.time() - dest.stat().st_mtime
        except OSError:
            return True
        return age > self.stale_threshold_sec

    def _run(self, argv: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )

    # ------------------------------------------------------------------ #
    # sync
    # ------------------------------------------------------------------ #

    def sync(self, *, force: bool = False) -> None:
        """Pull ``<remote_claude_home>/projects/`` and ``/sessions/`` into the cache.

        Non-existent remote directories are tolerated (rsync returns 23).
        """
        projects_dest = self.cache_dir / "projects"
        sessions_dest = self.cache_dir / "sessions"
        if not force and not self._is_stale(projects_dest):
            return

        projects_dest.mkdir(parents=True, exist_ok=True)
        sessions_dest.mkdir(parents=True, exist_ok=True)

        # Best-effort — don't blow up when the remote tree is partly empty.
        for rel, dest in [("projects", projects_dest), ("sessions", sessions_dest)]:
            try:
                result = self._run(self._rsync_cmd(rel, dest), timeout=120.0)
                # rsync exit 23 = "partial transfer due to vanished files" —
                # happens on active trees, tolerable.
                if result.returncode not in (0, 23):
                    # Don't silence completely — attach for debugging.
                    self._last_sync_error = result.stderr.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                self._last_sync_error = str(e)

    # ------------------------------------------------------------------ #
    # remote queries
    # ------------------------------------------------------------------ #

    def _remote_tmux_list(self) -> list[tm.TmuxSession]:
        """Run ``tmux list-sessions`` over SSH and parse the output."""
        fmt = "#{session_name}|#{session_created}|#{session_activity}|#{session_attached}"
        cmd = f"{shlex.quote(self.tmux_bin)} list-sessions -F {shlex.quote(fmt)}"
        try:
            result = self._run(self._ssh_cmd(cmd))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        rows: list[tm.TmuxSession] = []
        for line in result.stdout.splitlines():
            parts = line.split("|")
            if len(parts) < 4:
                continue
            name, created, activity, attached = parts
            try:
                rows.append(tm.TmuxSession(
                    name=name,
                    created=int(created),
                    activity=int(activity),
                    attached=attached == "1",
                ))
            except ValueError:
                continue
        return rows

    # ------------------------------------------------------------------ #
    # discovery
    # ------------------------------------------------------------------ #

    def iter_sessions(
        self,
        *,
        project_slug: Optional[str] = None,
        include_live: bool = True,
    ) -> Iterator["Session"]:  # noqa: F821
        from xa.sessions import Session, _session_from_transcript_meta

        self.sync()

        cached_home = self.cache_dir

        live_by_cs_id: dict[str, dict] = {}
        live_tmux_rows: list[tm.TmuxSession] = []
        if include_live:
            for eph in cfs.iter_ephemeral_sessions(claude_home=cached_home):
                cs = eph.get("sessionId")
                if cs:
                    live_by_cs_id[cs] = eph
            live_tmux_rows = self._remote_tmux_list()

        # We can't walk /proc on the remote cheaply, so match tmux ↔ ephemeral
        # by cwd (best-effort): for each tmux session, pick the ephemeral
        # whose cwd best matches the tmux activity. In practice we only need
        # *some* tmux_name attribution, and per-machine it tends to be
        # unambiguous (one claude per cwd).
        def _pick_tmux(eph: dict) -> Optional[tm.TmuxSession]:
            # If only one tmux session exists, attribute any live eph to it.
            if len(live_tmux_rows) == 1:
                return live_tmux_rows[0]
            # Otherwise, we simply return None — the UI still shows the URL
            # from the ephemeral file; the tmux_name is advisory.
            return None

        emitted: set[str] = set()
        for path in cfs.iter_transcript_files(
            claude_home=cached_home, project_slug=project_slug
        ):
            meta = cfs.transcript_metadata(path)
            base = _session_from_transcript_meta(meta, host=self.name)
            cs_id = base.claude_session_id
            if cs_id and cs_id in live_by_cs_id:
                eph = live_by_cs_id[cs_id]
                tmux_row = _pick_tmux(eph)
                bridge = eph.get("bridgeSessionId")
                yield replace(
                    base,
                    state="live",
                    live_pid=eph.get("pid") if isinstance(eph.get("pid"), int) else None,
                    tmux_name=tmux_row.name if tmux_row else None,
                    bridge_session_id=bridge,
                    url=f"{ccli.CLAUDE_WEB_BASE}/{bridge}" if bridge else None,
                    url_source="session_file" if bridge else None,
                )
                emitted.add(cs_id)
            else:
                yield base

        for cs_id, eph in live_by_cs_id.items():
            if cs_id in emitted:
                continue
            bridge = eph.get("bridgeSessionId")
            cwd = eph.get("cwd")
            yield Session(
                id=cs_id,
                claude_session_id=cs_id,
                bridge_session_id=bridge,
                host=self.name,
                cwd=cwd,
                project_slug=cfs.encode_project_slug(cwd) if cwd else "",
                state="live",
                live_pid=eph.get("pid") if isinstance(eph.get("pid"), int) else None,
                tmux_name=(_pick_tmux(eph).name if _pick_tmux(eph) else None),
                name=None,
                summary=None,
                first_user_message=None,
                turn_count=0,
                forked_from=None,
                created=(
                    eph.get("startedAt") / 1000
                    if isinstance(eph.get("startedAt"), (int, float))
                    else None
                ),
                modified=None,
                url=f"{ccli.CLAUDE_WEB_BASE}/{bridge}" if bridge else None,
                url_source="session_file" if bridge else None,
                transcript_path=None,
            )

    # ------------------------------------------------------------------ #
    # actions (SSH-dispatched)
    # ------------------------------------------------------------------ #

    def spawn(self, name: str, *, cwd: str, **opts) -> ccli.SpawnResult:
        """Spawn ``tmux new-session -d 's <name>' 'cd <cwd> && exec claude'`` over SSH.

        URL detection is left to the caller's next :meth:`sync` + listing —
        we don't round-trip waiting for the bridge URL here (that would
        hold an SSH connection open for up to 2 minutes).
        """
        if not re.match(r"^[A-Za-z0-9_.-]{1,48}$", name):
            raise ValueError("invalid tmux session name")
        remote_cmd = (
            f"{shlex.quote(self.tmux_bin)} new-session -d "
            f"-s {shlex.quote(name)} "
            f"{shlex.quote(f'cd {shlex.quote(cwd)} && exec {shlex.quote(self.claude_bin)}')}"
        )
        result = self._run(self._ssh_cmd(remote_cmd))
        if result.returncode != 0:
            raise RuntimeError(f"ssh spawn failed: {result.stderr.strip()}")
        return ccli.SpawnResult(
            name=name,
            cwd=cwd,
            claude_pid=None,
            claude_session_id=None,
            bridge_session_id=None,
            url=None,
            url_source=None,
            warning="SSH spawn initiated — run `xa sync` then `xa list` to see the URL.",
        )

    def resume(
        self, claude_session_id: str, *, cwd: str, **opts
    ) -> ccli.SpawnResult:
        name = opts.get("name") or f"resumed-{claude_session_id[:8]}"
        if not re.match(r"^[A-Za-z0-9_.-]{1,48}$", name):
            raise ValueError("invalid tmux session name")
        remote_cmd = (
            f"{shlex.quote(self.tmux_bin)} new-session -d "
            f"-s {shlex.quote(name)} "
            f"{shlex.quote(f'cd {shlex.quote(cwd)} && exec {shlex.quote(self.claude_bin)} --resume {shlex.quote(claude_session_id)}')}"
        )
        result = self._run(self._ssh_cmd(remote_cmd))
        if result.returncode != 0:
            raise RuntimeError(f"ssh resume failed: {result.stderr.strip()}")
        return ccli.SpawnResult(
            name=name,
            cwd=cwd,
            claude_pid=None,
            claude_session_id=claude_session_id,
            bridge_session_id=None,
            url=None,
            url_source=None,
            warning="SSH resume initiated — run `xa sync` then `xa list` to see the URL.",
        )

    def kill(self, name: str) -> None:
        if not re.match(r"^[A-Za-z0-9_.-]{1,48}$", name):
            raise ValueError("invalid tmux session name")
        remote_cmd = (
            f"{shlex.quote(self.tmux_bin)} kill-session -t {shlex.quote(name + ':')}"
        )
        result = self._run(self._ssh_cmd(remote_cmd))
        if result.returncode != 0:
            raise RuntimeError(f"ssh kill failed: {result.stderr.strip()}")

    def capture_pane(self, name: str, *, lines: int = 200) -> str:
        if not re.match(r"^[A-Za-z0-9_.-]{1,48}$", name):
            raise ValueError("invalid tmux session name")
        remote_cmd = (
            f"{shlex.quote(self.tmux_bin)} capture-pane -t "
            f"{shlex.quote(name + ':')} -p -S -{int(lines)}"
        )
        result = self._run(self._ssh_cmd(remote_cmd))
        return result.stdout if result.returncode == 0 else ""
