"""Bring back the sessions a shutdown killed, each as it was launched.

Two questions, answered from the transcripts alone (the processes are gone):

1. **Which sessions were alive at the last shutdown?** When a machine shuts
   down, every running claude writes a final ``cost-state`` entry on its way
   out, so its transcript's last write lands within seconds of the shutdown.
   :func:`sessions_alive_at_shutdown` returns the transcripts whose last write
   falls in ``[shutdown - window, boot)``. The shutdown time comes from
   ``last`` (:func:`last_shutdown_time`), the boot time from the kernel
   (:func:`boot_time`), and the record counts only when it belongs to this
   boot. Sessions resumed since then, running now, or already sitting in a tmux
   pane started with ``--resume <id>`` are never candidates, so applying twice
   resumes nothing twice. Headless runs (``claude -p``, the SDK) are skipped.

2. **How was each one launched?** :func:`xa.claude_fs.launch_facts` reads one transcript
   for what ``claude --resume`` needs in order to come back the same:

   - the **cwd**: the *first* ``cwd`` whose slug is the transcript's project
     folder. A session that ``cd``-ed elsewhere records later cwds too, and
     resuming in the last one puts it in the wrong folder (thorwhalen/xa#23);
   - the **permission mode**: the last one recorded, plus whether bypass was
     ever reached (only a session started with the bypass flag can reach it);
   - the **model** of the last assistant turn, pinned only when its family
     differs from the account default's (:func:`xa.claude_fs.resume_model`).

What a shutdown with no clean exit (a power cut, a kernel panic) leaves is not
covered: no process wrote anything at the moment it died, so there is nothing
to time it by.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from xa import claude_env as cenv
from xa import claude_fs as cfs
from xa.claude_fs import LaunchFacts, launch_facts


#: How long before the shutdown record a session's last write may land and
#: still count as "alive at shutdown". ``last`` has minute resolution, so this
#: has to cover at least the minute it rounds down.
DFLT_WINDOW_SEC = 120.0

_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_LAST_STAMP_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\s+(\d{2}):(\d{2})"
)
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_RESUME_ID_RE = re.compile(
    r"--resume[\s=']+'?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


# --------------------------------------------------------------------------- #
# naming
# --------------------------------------------------------------------------- #


def tmux_name_for(facts: LaunchFacts, *, taken: Iterable[str] = ()) -> str:
    """A tmux session name for ``facts``: its own name, sanitised, made unique.

    >>> f = LaunchFacts('0123456789', Path('t'), None, '', False, None, None, 'my session', None, 0.0)
    >>> tmux_name_for(f), tmux_name_for(f, taken={'my-session'})
    ('my-session', 'my-session-2')
    """
    taken = set(taken)
    base = _NAME_RE.sub("-", facts.name or "").strip("-.")[:40]
    base = base or f"resumed-{facts.session_id[:8]}"
    if base not in taken:
        return base
    return next(f"{base}-{i}" for i in range(2, 1000) if f"{base}-{i}" not in taken)


# --------------------------------------------------------------------------- #
# when the machine went down
# --------------------------------------------------------------------------- #


def _run(argv: list[str]) -> str:
    try:
        out = subprocess.run(
            argv, capture_output=True, text=True, timeout=10.0, check=False
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return ""
    return out.stdout


def boot_time(*, run: Callable[[list[str]], str] = _run) -> Optional[float]:
    """Unix time the machine last booted, or ``None`` when it cannot be read.

    ``/proc/stat``'s ``btime`` on Linux, ``sysctl kern.boottime`` elsewhere.
    """
    stat = Path("/proc/stat")
    if stat.is_file():
        try:
            for line in stat.read_text().splitlines():
                if line.startswith("btime "):
                    return float(line.split()[1])
        except (OSError, ValueError):
            return None
    m = re.search(r"sec\s*=\s*(\d+)", run(["sysctl", "-n", "kern.boottime"]))
    return float(m.group(1)) if m else None


def parse_last_stamp(text: str, *, before: float) -> Optional[float]:
    """The first ``Tue Nov 10 09:50``-style stamp in ``text``, as unix time.

    ``last`` prints no year, so the year is the latest one that puts the stamp
    at or before ``before`` (local time).

    >>> import time as t
    >>> before = t.mktime((2026, 11, 10, 10, 0, 0, 0, 0, -1))
    >>> ts = parse_last_stamp('shutdown time   Tue Nov 10 09:50', before=before)
    >>> t.localtime(ts)[:5]
    (2026, 11, 10, 9, 50)
    """
    m = _LAST_STAMP_RE.search(text)
    if not m:
        return None
    mon, day, hh, mm = _MONTHS.index(m.group(1)) + 1, *map(int, m.groups()[1:])
    year = time.localtime(before).tm_year
    for y in (year, year - 1):
        ts = time.mktime((y, mon, day, hh, mm, 0, 0, 0, -1))
        if ts <= before + 60:
            return ts
    return None


def last_shutdown_time(
    *, before: Optional[float] = None, run: Callable[[list[str]], str] = _run
) -> Optional[float]:
    """Unix time of the shutdown that preceded the current boot, or ``None``.

    Read from ``last`` (minute resolution), newest record first: the current
    boot's ``reboot`` line, then its shutdown. A second ``reboot`` line before
    any shutdown means this boot followed a crash (no clean shutdown), and an
    older record must not stand in for it. ``last -x reboot shutdown`` on
    Linux; macOS prints both kinds under ``last reboot``.
    """
    before = time.time() if before is None else before
    argv = (
        ["last", "-x", "reboot", "shutdown"]
        if sys.platform.startswith("linux")
        else ["last", "reboot"]
    )
    reboots = 0
    for line in run(argv).splitlines():
        word = line.split(maxsplit=1)[0] if line.strip() else ""
        if word == "reboot":
            reboots += 1
            if reboots > 1:
                return None
        elif word == "shutdown":
            return parse_last_stamp(line, before=before)
    return None


# --------------------------------------------------------------------------- #
# which sessions were alive at that moment
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ShutdownScan:
    """The candidates, and the times they were picked by (for the dry run)."""

    candidates: list[LaunchFacts]
    shutdown: Optional[float]
    boot: Optional[float]
    anchor_source: str  # 'last' | 'latest-write' | 'none'
    lo: Optional[float]
    hi: Optional[float]


def live_session_ids(*, claude_home: Path, tmux_bin: Optional[str] = None) -> set[str]:
    """Session ids running right now: a live claude, or a tmux pane resuming them.

    The pane scan covers a resume that has not written anything yet (it is
    still at a trust or login prompt), which the session files cannot show.
    """
    from xa import tmux as tm

    ids = {
        eph["sessionId"]
        for eph in cfs.iter_ephemeral_sessions(claude_home=claude_home)
        if eph.get("sessionId") and cfs.ephemeral_session_alive(eph)
    }
    out = tm._run(
        [
            tmux_bin or tm.DEFAULT_TMUX_BIN,
            "list-panes",
            "-a",
            "-F",
            "#{pane_start_command}",
        ]
    )
    if out.returncode == 0:
        ids |= set(_RESUME_ID_RE.findall(out.stdout))
    return ids


def sessions_alive_at_shutdown(
    *,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    window_sec: float = DFLT_WINDOW_SEC,
    shutdown: Optional[float] = None,
    boot: Optional[float] = None,
    live_ids: Optional[set[str]] = None,
    tmux_bin: Optional[str] = None,
) -> ShutdownScan:
    """The sessions of ``claude_home`` whose last write fell at the last shutdown.

    The window is ``[shutdown - window_sec, boot)``. With no shutdown record
    for this boot (a crash, or ``last`` unreadable), the anchor is the latest
    transcript write before boot instead, and the window is
    ``[anchor - window_sec, anchor]``: the dry run says which was used.
    ``shutdown`` / ``boot`` / ``live_ids`` default to what this machine reports.
    Sessions in ``live_ids`` (running now) and headless runs are never candidates.
    """
    boot = boot_time() if boot is None else boot
    if shutdown is None:
        shutdown = last_shutdown_time(before=boot)
    if live_ids is None:
        live_ids = live_session_ids(claude_home=claude_home, tmux_bin=tmux_bin)

    stats: list[tuple[Path, float]] = []
    for path in cfs.iter_transcript_files(claude_home=claude_home):
        try:
            stats.append((path, path.stat().st_mtime))
        except OSError:
            continue
    before_boot = [mt for _, mt in stats if boot is None or mt < boot]

    if shutdown is not None and boot is not None and shutdown <= boot:
        source, lo, hi = "last", shutdown - window_sec, boot
    elif before_boot:
        anchor = max(before_boot)
        source, lo, hi = "latest-write", anchor - window_sec, anchor + 1e-6
    else:
        return ShutdownScan([], shutdown, boot, "none", None, None)

    picked = [
        launch_facts(path)
        for path, mt in stats
        if lo <= mt < hi and path.stem not in live_ids
    ]
    picked = [f for f in picked if f.session_id not in live_ids and not f.headless]
    picked.sort(key=lambda f: f.last_write)
    return ShutdownScan(picked, shutdown, boot, source, lo, hi)


# --------------------------------------------------------------------------- #
# bring them back
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Resumed:
    """One candidate's outcome: a :class:`~xa.claude_cli.SpawnResult`, or why not."""

    facts: LaunchFacts
    result: Optional[object]  # SpawnResult when launched
    skipped: Optional[str] = None  # the reason, when not launched
    warning: Optional[str] = None


def resume_all(
    candidates: Iterable[LaunchFacts],
    *,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    claude_bin: Optional[str] = None,
    tmux_bin: Optional[str] = None,
    url_timeout_sec: float = 120.0,
    auto_remote_control: bool = True,
    permission_mode: Optional[str] = None,
    euid: Optional[int] = None,
) -> list[Resumed]:
    """Resume every candidate in its own tmux session, each as it was launched.

    All panes are launched first and only then waited on, so the whole batch
    takes about one ``url_timeout_sec``, not one per session. A pane that comes
    up without a Remote Control URL gets the :mod:`xa.revive` verdict for what
    it is showing (``needs_trust`` for a folder the account has not trusted,
    ``needs_login``, ...) in ``result.attention``, rather than a hang.
    ``permission_mode`` overrides every candidate's recorded mode (``''``:
    claude's default).
    """
    from xa import claude_cli as ccli
    from xa import tmux as tm

    claude_bin = claude_bin or ccli.DEFAULT_CLAUDE_BIN
    tmux_bin = tmux_bin or tm.DEFAULT_TMUX_BIN
    taken = {s.name for s in tm.list_sessions(binary=tmux_bin)}
    launched: list[tuple[LaunchFacts, object, Optional[str]]] = []
    out: list[Resumed] = []
    for facts in candidates:
        if not facts.cwd or not Path(facts.cwd).is_dir():
            out.append(Resumed(facts, None, skipped=f"cwd gone: {facts.cwd}"))
            continue
        if permission_mode is None:
            mode, allow, warning = cenv.resume_permission(
                facts.permission_mode, bypass_seen=facts.bypass_seen, euid=euid
            )
        else:
            mode, allow, warning = permission_mode, False, None
        name = tmux_name_for(facts, taken=taken)
        taken.add(name)
        try:
            pending = ccli.prepare_spawn(
                name,
                cwd=facts.cwd,
                resume_id=facts.session_id,
                claude_bin=claude_bin,
                claude_home=Path(claude_home),
                tmux_bin=tmux_bin,
                url_timeout_sec=url_timeout_sec,
                auto_remote_control=auto_remote_control,
                model=cfs.resume_model(facts, claude_home=Path(claude_home)),
                permission_mode=mode,
                allow_bypass=allow,
            )
        except (FileNotFoundError, RuntimeError) as e:
            out.append(Resumed(facts, None, skipped=str(e)))
            continue
        launched.append((facts, pending, warning))
    for facts, pending, warning in launched:
        out.append(Resumed(facts, ccli.complete_spawn(pending), warning=warning))
    return out
