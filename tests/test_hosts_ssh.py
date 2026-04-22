"""Unit tests for ``xa.hosts.SSHHost`` — no real SSH access.

Exercise the *wiring* (command construction, sync staleness logic,
discovery off a cached tree, action argv assembly). Real end-to-end
ssh/rsync coverage is the user's responsibility (``XA_RUN_INTEGRATION=1``
could be extended to target a real host; we deliberately don't gate on
one).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from xa.hosts import SSHHost


SID = "99999999-8888-7777-6666-555555555555"


def _seed_cache(cache_dir: Path) -> None:
    """Drop a minimal ``projects/<slug>/<uuid>.jsonl`` into the cache."""
    pdir = cache_dir / "projects" / "-remote-workdir"
    pdir.mkdir(parents=True)
    (pdir / f"{SID}.jsonl").write_text(
        json.dumps({"type": "user", "sessionId": SID, "cwd": "/remote/workdir",
                    "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}}) + "\n"
    )
    (cache_dir / "sessions").mkdir(parents=True, exist_ok=True)


def test_rsync_and_ssh_cmd_shape(tmp_path: Path) -> None:
    h = SSHHost(
        name="dev", host="devbox", user="ec2-user",
        remote_claude_home="/home/ec2-user/.claude",
        cache_dir=tmp_path,
    )
    argv = h._rsync_cmd("projects", tmp_path / "proj")
    assert argv[0] == "rsync"
    assert "-a" in argv and "-z" in argv and "--delete" in argv
    assert "ec2-user@devbox:/home/ec2-user/.claude/projects/" in argv
    assert str(tmp_path / "proj") + "/" in argv

    ssh_argv = h._ssh_cmd("tmux list-sessions")
    assert ssh_argv[0] == "ssh"
    assert ssh_argv[1] == "ec2-user@devbox"
    assert "list-sessions" in ssh_argv[2]


def test_rsync_target_without_user(tmp_path: Path) -> None:
    h = SSHHost(name="dev", host="devbox", cache_dir=tmp_path)
    argv = h._rsync_cmd("projects", tmp_path / "x")
    assert "devbox:~/.claude/projects/" in argv


def test_is_stale_logic(tmp_path: Path) -> None:
    h = SSHHost(
        name="dev", host="devbox", cache_dir=tmp_path, stale_threshold_sec=3600
    )
    dest = tmp_path / "proj"
    assert h._is_stale(dest) is True          # doesn't exist → stale
    dest.mkdir()
    assert h._is_stale(dest) is False         # fresh mkdir → not stale
    # Age it beyond the threshold by mtime manipulation.
    import os
    old = time.time() - 7200
    os.utime(dest, (old, old))
    assert h._is_stale(dest) is True          # > threshold → stale
    h2 = SSHHost(
        name="dev", host="devbox", cache_dir=tmp_path, stale_threshold_sec=0
    )
    assert h2._is_stale(dest) is True         # threshold=0 → always stale


def test_sync_invokes_rsync(tmp_path: Path, monkeypatch) -> None:
    """Monkeypatch ``_run`` to capture the commands rsync would be called with."""
    h = SSHHost(name="dev", host="devbox", cache_dir=tmp_path, stale_threshold_sec=0)

    calls: list[list[str]] = []

    class FakeResult:
        returncode = 0
        stderr = ""

    def fake_run(self: Any, argv: list[str], timeout: float = 0) -> Any:
        calls.append(argv)
        return FakeResult()

    monkeypatch.setattr(SSHHost, "_run", fake_run)
    h.sync(force=True)
    # Two rsyncs: projects + sessions
    assert len(calls) == 2
    assert any("projects/" in " ".join(c) for c in calls)
    assert any("sessions/" in " ".join(c) for c in calls)


def test_iter_sessions_uses_cached_tree(tmp_path: Path, monkeypatch) -> None:
    cache_root = tmp_path / "cache"
    h = SSHHost(name="dev", host="devbox", cache_dir=cache_root, stale_threshold_sec=3600)
    _seed_cache(h.cache_dir)

    # Prevent any real sync.
    monkeypatch.setattr(SSHHost, "sync", lambda self, *, force=False: None)
    monkeypatch.setattr(SSHHost, "_remote_tmux_list", lambda self: [])

    rows = list(h.iter_sessions(include_live=False))
    assert len(rows) == 1
    s = rows[0]
    assert s.host == "dev"
    assert s.id == SID
    assert s.cwd == "/remote/workdir"
    assert s.state == "transcript_only"


def test_action_argv_rejects_bad_name(tmp_path: Path) -> None:
    h = SSHHost(name="dev", host="devbox", cache_dir=tmp_path)
    with pytest.raises(ValueError):
        h.spawn("bad name with spaces", cwd="/tmp")
    with pytest.raises(ValueError):
        h.kill("bad;rm -rf /")
