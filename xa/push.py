"""``xa push``: mirror this machine's live sessions into another host's xa cache.

``xa sync`` pulls a remote home down, which needs this machine to reach the other one. A
laptop behind NAT can reach a server but not the reverse, so the server cannot pull it.
``push`` is the other direction: it writes a *mirror* of the local home into
``<host>:<cache_dir>/<as_name>/``, the same place the server's own ``xa sync`` would put it:

- ``sessions/``: the registry records whose process is alive here, and nothing else
  (``--delete``, so a session that ended disappears from the mirror);
- ``projects/<slug>/<session-id>.jsonl``: the transcript of each of those sessions;
- ``.mirrored-at``: a stamp, written last, whose mtime says when the mirror was complete.

A reader on the hub (crowsnest, a home with ``liveness = "mirror"``) counts every record in
it as live while the stamp is fresh, so an idle session still shows and a machine that
stops pushing drops out. Transcripts of ended sessions are pruned on the hub after
``keep_days``.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

#: The stamp's name. crowsnest reads the same name (crowsnest.registry.MIRROR_STAMP).
MIRROR_STAMP = ".mirrored-at"
#: Relative to the remote home: ssh and rsync both start there, and a quoted ``~`` would
#: not be expanded by the remote shell.
DFLT_REMOTE_CACHE = ".cache/xa/remotes"
DFLT_KEEP_DAYS = 7


@dataclass(frozen=True)
class LiveRecord:
    """One registry record whose process is alive here, and its transcript (if found)."""

    record: Path
    transcript: Path | None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # alive, owned by someone else
        return True
    return True


def live_records(
    home: str | Path, *, is_alive: Callable[[int], bool] = _pid_alive
) -> Iterator[LiveRecord]:
    """The registry records under ``home`` whose pid is alive, with their transcripts."""
    root = Path(home).expanduser()
    registry = root / "sessions"
    if not registry.is_dir():
        return
    for path in sorted(registry.glob("*.json")):
        if not path.stem.isdigit() or not is_alive(int(path.stem)):
            continue
        try:
            sid = json.loads(path.read_text(encoding="utf-8")).get("sessionId") or ""
        except (OSError, ValueError):
            continue
        hits = sorted((root / "projects").glob(f"*/{sid}.jsonl")) if sid else []
        yield LiveRecord(path, hits[0] if hits else None)


def _rsync(rsync_bin: str, ssh_bin: str, *args: str) -> list[str]:
    return [rsync_bin, "-a", "-e", ssh_bin, *args]


def push_commands(
    records: list[LiveRecord],
    *,
    home: Path,
    target: str,
    dest: str,
    staging: Path,
    keep_days: int = DFLT_KEEP_DAYS,
    rsync_bin: str = "rsync",
    ssh_bin: str = "ssh",
) -> list[list[str]]:
    """The commands one push runs, in order. Pure apart from writing into ``staging``.

    Transcripts go first and the registry plus stamp last, so a fresh stamp always comes
    with the transcripts its records name.
    """
    (staging / "sessions").mkdir(parents=True, exist_ok=True)
    for r in records:
        (staging / "sessions" / r.record.name).write_bytes(r.record.read_bytes())
    (staging / MIRROR_STAMP).touch()
    files = staging.parent / f"{staging.name}.files"
    files.write_text(
        "".join(f"{r.transcript.relative_to(home)}\n" for r in records if r.transcript)
    )
    q = shlex.quote
    return [
        [ssh_bin, target, f"mkdir -p {q(dest)}/sessions {q(dest)}/projects"],
        # Transcripts only grow; --append-verify sends the new tail, checked.
        _rsync(rsync_bin, ssh_bin, "--append-verify", f"--files-from={files}", f"{home}/", f"{target}:{dest}/"),
        # The registry mirrors exactly what is live; projects/ is protected from --delete.
        _rsync(rsync_bin, ssh_bin, "--delete", "--filter=P projects/***", f"{staging}/", f"{target}:{dest}/"),
        [ssh_bin, target,
         f"find {q(dest)}/projects -name '*.jsonl' -mtime +{int(keep_days)} -delete 2>/dev/null; true"],
    ]  # fmt: skip


def push(
    host,
    *,
    as_name: str,
    home: str | Path = "~/.claude",
    remote_cache: str = DFLT_REMOTE_CACHE,
    keep_days: int = DFLT_KEEP_DAYS,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout: float = 300.0,
) -> int:
    """Mirror ``home``'s live sessions to ``host`` (an :class:`~xa.hosts.ssh.SSHHost`).

    Returns how many live sessions were pushed. Raises ``RuntimeError`` naming the step
    that failed.
    """
    home = Path(home).expanduser()
    records = list(live_records(home))
    remote_cache = remote_cache[2:] if remote_cache.startswith("~/") else remote_cache
    dest = f"{remote_cache.rstrip('/')}/{as_name}"
    with tempfile.TemporaryDirectory(prefix="xa-push-") as tmp:
        cmds = push_commands(
            records, home=home, target=host._target, dest=dest, staging=Path(tmp) / "mirror",
            keep_days=keep_days, rsync_bin=host.rsync_bin, ssh_bin=host.ssh_bin,
        )  # fmt: skip
        for argv in cmds:
            done = run(argv, capture_output=True, text=True, timeout=timeout)
            # rsync 23/24: a file vanished or changed mid-copy, which a live tree does.
            if done.returncode not in (0, 23, 24):
                raise RuntimeError(
                    f"{argv[0]} failed ({done.returncode}): {done.stderr.strip()[:300]}"
                )
    return len(records)
