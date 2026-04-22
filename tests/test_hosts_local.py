"""Unit tests for ``xa.hosts.LocalHost``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xa.hosts import LocalHost


SID_A = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture()
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / ".claude"
    pdir = home / "projects" / "-foo-bar"
    pdir.mkdir(parents=True)
    events = [
        {
            "type": "user",
            "sessionId": SID_A,
            "cwd": "/foo/bar",
            "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        },
        {
            "type": "assistant",
            "sessionId": SID_A,
            "cwd": "/foo/bar",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        },
    ]
    with (pdir / f"{SID_A}.jsonl").open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return home


def test_localhost_lists_transcripts(fake_home: Path) -> None:
    h = LocalHost(claude_home=fake_home)
    # include_live=False avoids any tmux access during tests.
    sessions = list(h.iter_sessions(include_live=False))
    assert len(sessions) == 1
    s = sessions[0]
    assert s.id == SID_A
    assert s.host == "local"
    assert s.state == "transcript_only"
    assert s.cwd == "/foo/bar"


def test_localhost_named(fake_home: Path) -> None:
    h = LocalHost(name="mybox", claude_home=fake_home)
    assert h.name == "mybox"
    sessions = list(h.iter_sessions(include_live=False))
    assert sessions[0].host == "mybox"


def test_localhost_sync_noop() -> None:
    # Should not raise, should not consult the filesystem.
    LocalHost().sync(force=True)
