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


def test_spawn_and_resume_accept_kwarg_overrides(monkeypatch) -> None:
    """LocalHost.spawn/resume must tolerate callers that forward
    ``claude_bin`` / ``claude_home`` via **opts (as the HTTP API does).
    Regression test for the double-kwarg TypeError that surfaced as an
    opaque 500 'Internal Server Error' on resume.
    """
    from xa import claude_cli as ccli

    captured: dict = {}

    def _fake_resume(cs_id, **kw):
        captured.update(kw)
        captured["_cs_id"] = cs_id
        return "ok"

    def _fake_spawn(name, **kw):
        captured.update(kw)
        captured["_name"] = name
        return "ok"

    monkeypatch.setattr(ccli, "resume_session", _fake_resume)
    monkeypatch.setattr(ccli, "spawn_session", _fake_spawn)

    h = LocalHost(
        claude_bin="/host/default/claude",
        claude_home=Path("/host/default/home"),
    )

    # Pre-fix, both of these raised TypeError about duplicate claude_bin.
    h.resume("abc", cwd="/tmp", claude_bin="/override/claude",
             claude_home=Path("/override/home"))
    assert captured["claude_bin"] == "/override/claude"
    assert captured["claude_home"] == Path("/override/home")

    captured.clear()
    h.spawn("sess", cwd="/tmp", claude_bin="/over/claude")
    assert captured["claude_bin"] == "/over/claude"
    # Fell through to host default since the caller didn't override it.
    assert captured["claude_home"] == Path("/host/default/home")

    # And without any overrides, host defaults flow through.
    captured.clear()
    h.resume("abc", cwd="/tmp")
    assert captured["claude_bin"] == "/host/default/claude"
    assert captured["claude_home"] == Path("/host/default/home")
