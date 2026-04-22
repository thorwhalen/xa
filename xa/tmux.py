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


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def session_target(name: str) -> str:
    """Return the canonical target string for a session.

    >>> session_target('foo')
    'foo:'
    """
    return f"{name}:"


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


def capture_pane(name: str, *, lines: int = 200, binary: str = DEFAULT_TMUX_BIN) -> str:
    """Return the last ``lines`` of the session's first pane, or '' on failure."""
    out = _run(
        [binary, "capture-pane", "-t", session_target(name), "-p", "-S", f"-{lines}"]
    )
    return out.stdout if out.returncode == 0 else ""


def send_keys(name: str, *keys: str, binary: str = DEFAULT_TMUX_BIN) -> None:
    """Send one or more keys/strings to the pane. Always pass ``"Enter"`` for newline."""
    out = _run([binary, "send-keys", "-t", session_target(name), *keys])
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


def pane_pid(name: str, *, binary: str = DEFAULT_TMUX_BIN) -> Optional[int]:
    """Return the pid of the first pane's program, or None if the session is gone."""
    out = _run(
        [binary, "list-panes", "-s", "-t", session_target(name), "-F", "#{pane_pid}"]
    )
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return int(out.stdout.splitlines()[0].strip())
    except ValueError:
        return None


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
