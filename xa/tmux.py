"""Pure tmux wrappers.

No Claude Code knowledge lives here. Every public function takes a
``binary`` keyword so callers can override the tmux executable (tests,
cross-platform installs, remote-host bridges).

Key gotchas encoded below:

- ``session_target(name)`` returns ``f"{name}:"`` — the trailing colon is
  essential. A bare session name can be silently resolved as a window or
  pane spec and mis-target a different session.
- ``list_sessions`` returns an empty list (not raises) when the tmux
  server isn't running; tmux exits non-zero in that case and we absorb it.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_TMUX_BIN = "tmux"


@dataclass(frozen=True)
class TmuxSession:
    """Minimal view of one tmux session, from ``list-sessions``."""

    name: str
    created: int  # unix seconds
    activity: int  # unix seconds of last activity
    attached: bool


@dataclass(frozen=True)
class TmuxPane:
    """Minimal view of one tmux pane, from ``list-panes -a``."""

    target: str  # "session:@window.%pane"
    pid: int
    current_command: str
    current_path: str


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def session_target(name: str) -> str:
    """Return the canonical target string for a session.

    >>> session_target('foo')
    'foo:'
    """
    return f"{name}:"


def pane_target(ref: str) -> str:
    """Target string for pane-scoped commands (capture, send-keys).

    Accepts either a bare session name or a full tmux pane ref
    (``session:@window.%pane`` — the shape claude records in its ephemeral
    session file). A bare name targets the session's *active* pane, which
    may not be the claude pane in a multi-window session — pass the full
    ref whenever you have one. Session names in xa match
    ``[A-Za-z0-9_.-]`` so they can never contain a ``:``.

    >>> pane_target('foo')
    'foo:'
    >>> pane_target('foo:@1.%2')
    'foo:@1.%2'
    """
    return ref if ":" in ref else f"{ref}:"


def _run(args: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )


# --------------------------------------------------------------------------- #
# core operations
# --------------------------------------------------------------------------- #


def list_sessions(*, binary: str = DEFAULT_TMUX_BIN) -> list[TmuxSession]:
    """Return all live tmux sessions; empty list if server isn't running."""
    fmt = "#{session_name}|#{session_created}|#{session_activity}|#{session_attached}"
    try:
        out = _run([binary, "list-sessions", "-F", fmt])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    rows: list[TmuxSession] = []
    for line in out.stdout.splitlines():
        parts = line.split("|")
        if len(parts) < 4:
            continue
        name, created, activity, attached = parts
        try:
            rows.append(
                TmuxSession(
                    name=name,
                    created=int(created),
                    activity=int(activity),
                    attached=attached == "1",
                )
            )
        except ValueError:
            continue
    return rows


def new_session(name: str, *, command: str, binary: str = DEFAULT_TMUX_BIN) -> None:
    """Create a detached tmux session running ``command`` as its pane's program.

    ``command`` is passed to a shell: the caller is responsible for quoting.
    Use ``shlex.quote`` for untrusted parts.
    """
    out = _run([binary, "new-session", "-d", "-s", name, command])
    if out.returncode != 0:
        raise RuntimeError(f"tmux new-session failed: {out.stderr.strip()}")


def kill_session(name: str, *, binary: str = DEFAULT_TMUX_BIN) -> None:
    out = _run([binary, "kill-session", "-t", session_target(name)])
    if out.returncode != 0:
        raise RuntimeError(f"tmux kill-session failed: {out.stderr.strip()}")


def rename_session(
    old_name: str, new_name: str, *, binary: str = DEFAULT_TMUX_BIN
) -> None:
    """Rename a live tmux session.

    Both names must match the strict ``[A-Za-z0-9_.-]{1,48}`` pattern
    used elsewhere in ``xa``; callers should validate before calling.
    """
    out = _run([binary, "rename-session", "-t", session_target(old_name), new_name])
    if out.returncode != 0:
        raise RuntimeError(f"tmux rename-session failed: {out.stderr.strip()}")


def capture_pane(name: str, *, lines: int = 200, binary: str = DEFAULT_TMUX_BIN) -> str:
    """Return the last ``lines`` of the targeted pane, or '' on failure.

    ``name`` may be a session name (targets its active pane) or a full
    pane ref (``session:@w.%p``) for an exact pane.
    """
    out = _run(
        [binary, "capture-pane", "-t", pane_target(name), "-p", "-S", f"-{lines}"]
    )
    return out.stdout if out.returncode == 0 else ""


def send_keys(name: str, *keys: str, binary: str = DEFAULT_TMUX_BIN) -> None:
    """Send one or more keys/strings to the targeted pane.

    Always pass ``"Enter"`` for newline. ``name`` may be a session name
    (targets its active pane) or a full pane ref for an exact pane.
    """
    out = _run([binary, "send-keys", "-t", pane_target(name), *keys])
    if out.returncode != 0:
        raise RuntimeError(f"tmux send-keys failed: {out.stderr.strip()}")


def pipe_pane_to_file(name: str, *, path: Path, binary: str = DEFAULT_TMUX_BIN) -> None:
    """Start streaming the pane's output to ``path`` (append mode).

    Uses ``-o`` so a duplicate call toggles the pipe off, matching edualc's
    behavior. tmux stops piping automatically when the pane dies.
    """
    shell = f"cat >> {shlex.quote(str(path))}"
    out = _run([binary, "pipe-pane", "-t", session_target(name), "-o", shell])
    if out.returncode != 0:
        raise RuntimeError(f"tmux pipe-pane failed: {out.stderr.strip()}")


def pane_pids(name: str, *, binary: str = DEFAULT_TMUX_BIN) -> list[int]:
    """PIDs of every pane program in the session (all windows, all panes).

    Empty list when the session is gone. ``name`` may be a session name
    or a full pane ref — ``-s`` scopes to the containing session either way.
    """
    out = _run(
        [binary, "list-panes", "-s", "-t", pane_target(name), "-F", "#{pane_pid}"]
    )
    if out.returncode != 0:
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return pids


def list_panes(*, binary: str = DEFAULT_TMUX_BIN) -> list[TmuxPane]:
    """Every pane on the server, or ``[]`` when no server is running.

    ``target`` is the full pane ref, the same shape claude records in its
    ephemeral session file, so the two views join without parsing.
    """
    fmt = (
        "#{session_name}:#{window_id}.#{pane_id}|#{pane_pid}"
        "|#{pane_current_command}|#{pane_current_path}"
    )
    try:
        out = _run([binary, "list-panes", "-a", "-F", fmt])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    panes: list[TmuxPane] = []
    for line in out.stdout.splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        target, pid, command, path = parts
        try:
            panes.append(
                TmuxPane(
                    target=target,
                    pid=int(pid),
                    current_command=command,
                    current_path=path,
                )
            )
        except ValueError:
            continue
    return panes


def pane_current_path(name: str, *, binary: str = DEFAULT_TMUX_BIN) -> Optional[str]:
    """Working directory of the targeted pane's program, or None.

    This is where a pane's shell would run a command, which is what
    ``claude remote-control -c`` keys its per-directory record on.
    """
    out = _run(
        [
            binary,
            "display-message",
            "-p",
            "-t",
            pane_target(name),
            "#{pane_current_path}",
        ]
    )
    if out.returncode != 0:
        return None
    path = out.stdout.strip()
    return path or None


def pane_count(name: str, *, binary: str = DEFAULT_TMUX_BIN) -> int:
    """Number of panes (across all windows) in the session; 0 if gone."""
    return len(pane_pids(name, binary=binary))


def pane_pid(name: str, *, binary: str = DEFAULT_TMUX_BIN) -> Optional[int]:
    """Return the pid of the first pane's program, or None if the session is gone."""
    pids = pane_pids(name, binary=binary)
    return pids[0] if pids else None


# --------------------------------------------------------------------------- #
# /proc-based process tree walk (no pstree dependency)
# --------------------------------------------------------------------------- #


def descendants(pid: int) -> list[int]:
    """All transitive descendant PIDs of ``pid``.

    Scans ``/proc/*/status``; silently tolerates races (processes dying
    mid-scan). Not available on non-Linux platforms — returns ``[]`` if
    ``/proc`` is absent.
    """
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return []

    children_of: dict[int, list[int]] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("PPid:"):
                    ppid = int(line.split()[1])
                    children_of.setdefault(ppid, []).append(int(entry.name))
                    break
        except (
            FileNotFoundError,
            PermissionError,
            ProcessLookupError,
            OSError,
            ValueError,
        ):
            # Races: process dies between iterdir and read. Also tolerate
            # OSError, which covers Linux's ESRCH surfaced as OSError.
            continue

    seen: list[int] = []
    stack = [pid]
    while stack:
        p = stack.pop()
        for c in children_of.get(p, []):
            if c not in seen:
                seen.append(c)
                stack.append(c)
    return seen


def proc_comm(pid: int) -> str:
    """Return the ``comm`` name of a pid (kernel-level process name), '' if unreadable."""
    try:
        return (Path("/proc") / str(pid) / "comm").read_text().strip()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return ""
