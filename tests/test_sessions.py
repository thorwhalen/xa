"""Unit tests for ``xa.sessions`` — fixture-based."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xa import sessions as sess


SID_A = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SID_B = "11111111-2222-3333-4444-555555555555"
SID_C = "99999999-8888-7777-6666-555555555555"


def _mk_transcript(home: Path, slug: str, sid: str, cwd: str, *, forked_from=None) -> Path:
    pdir = home / "projects" / slug
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{sid}.jsonl"
    events = [
        {
            "type": "user",
            "sessionId": sid,
            "cwd": cwd,
            "message": {"role": "user", "content": [{"type": "text", "text": f"hi from {slug}"}]},
        },
        {
            "type": "assistant",
            "sessionId": sid,
            "cwd": cwd,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        },
    ]
    if forked_from is not None:
        events[0]["forkedFrom"] = {"sessionId": forked_from}
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return path


@pytest.fixture()
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / ".claude"
    a = _mk_transcript(home, "-foo-bar", SID_A, "/foo/bar")
    b = _mk_transcript(home, "-foo-baz", SID_B, "/foo/baz", forked_from=SID_A)
    c = _mk_transcript(home, "-foo-baz", SID_C, "/foo/baz")
    # Stagger mtimes so ordering is predictable: C is newest.
    import os, time
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    os.utime(c, (3000, 3000))
    return home


def test_list_sessions_orders_newest_first(fake_home: Path) -> None:
    rows = sess.list_sessions(claude_home=fake_home)
    assert [s.id for s in rows] == [SID_C, SID_B, SID_A]


def test_list_sessions_project_filter(fake_home: Path) -> None:
    rows = sess.list_sessions(project="baz", claude_home=fake_home)
    assert [s.id for s in rows] == [SID_C, SID_B]
    assert sess.list_sessions(project="nowhere", claude_home=fake_home) == []


def test_list_sessions_excludes_forks(fake_home: Path) -> None:
    rows = sess.list_sessions(include_forks=False, claude_home=fake_home)
    # B is a fork of A → excluded.
    assert {s.id for s in rows} == {SID_A, SID_C}


def test_list_sessions_limit(fake_home: Path) -> None:
    rows = sess.list_sessions(limit=1, claude_home=fake_home)
    assert len(rows) == 1
    assert rows[0].id == SID_C


def test_session_state_and_fields(fake_home: Path) -> None:
    row = sess.list_sessions(claude_home=fake_home)[0]
    assert row.state == "transcript_only"
    assert row.host == "local"
    assert row.bridge_session_id is None
    assert row.live_pid is None
    assert row.transcript_path is not None


def test_get_session_exact(fake_home: Path) -> None:
    s = sess.get_session(SID_A, claude_home=fake_home)
    assert s is not None and s.id == SID_A


def test_get_session_prefix(fake_home: Path) -> None:
    s = sess.get_session(SID_A[:8], claude_home=fake_home)
    assert s is not None and s.id == SID_A


def test_get_session_missing(fake_home: Path) -> None:
    assert sess.get_session("deadbeef", claude_home=fake_home) is None


def test_get_session_ambiguous(tmp_path: Path) -> None:
    home = tmp_path / ".claude"
    _mk_transcript(home, "-p", "abcd1111-0000-0000-0000-000000000000", "/p")
    _mk_transcript(home, "-p", "abcd2222-0000-0000-0000-000000000000", "/p")
    with pytest.raises(LookupError):
        sess.get_session("abcd", claude_home=home)


# --------------------------------------------------------------------------- #
# Phase 3: live-state merging
# --------------------------------------------------------------------------- #


def test_live_merge_promotes_matching_transcript(fake_home: Path, monkeypatch) -> None:
    """A transcript whose sessionId matches an ephemeral file is state='live'."""
    from xa import claude_cli as ccli
    from xa import claude_fs as cfs
    from xa import tmux as tm

    fake_tmux = tm.TmuxSession(name="tmux-A", created=0, activity=0, attached=False)
    monkeypatch.setattr(
        cfs, "iter_ephemeral_sessions",
        lambda claude_home=None: iter([
            {"pid": 4242, "sessionId": SID_A, "bridgeSessionId": "session_live_a"}
        ]),
    )
    monkeypatch.setattr(tm, "list_sessions", lambda binary=None: [fake_tmux])
    monkeypatch.setattr(
        ccli, "find_claude_pid",
        lambda name, tmux_bin=None: 4242 if name == "tmux-A" else None,
    )

    rows = sess.list_sessions(claude_home=fake_home)
    live = [r for r in rows if r.state == "live"]
    assert len(live) == 1
    assert live[0].id == SID_A
    assert live[0].bridge_session_id == "session_live_a"
    assert live[0].url == "https://claude.ai/code/session_live_a"
    assert live[0].url_source == "session_file"
    assert live[0].tmux_name == "tmux-A"
    assert live[0].live_pid == 4242
    # And the fork + the other one stay transcript_only.
    assert {r.state for r in rows if r.id != SID_A} == {"transcript_only"}


def test_live_only_surfaces_ephemeral_without_transcript(
    tmp_path: Path, monkeypatch
) -> None:
    """A live claude with no transcript yet still shows up."""
    from xa import claude_cli as ccli
    from xa import claude_fs as cfs
    from xa import tmux as tm

    empty_home = tmp_path / ".claude"
    empty_home.mkdir()
    fake_tmux = tm.TmuxSession(name="tmux-X", created=1000, activity=1000, attached=False)
    NEW_ID = "ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb"
    monkeypatch.setattr(
        cfs, "iter_ephemeral_sessions",
        lambda claude_home=None: iter([
            {
                "pid": 9999,
                "sessionId": NEW_ID,
                "bridgeSessionId": "session_xfresh",
                "cwd": "/tmp/x",
                "startedAt": 1_700_000_000_000,
            }
        ]),
    )
    monkeypatch.setattr(tm, "list_sessions", lambda binary=None: [fake_tmux])
    monkeypatch.setattr(
        ccli, "find_claude_pid",
        lambda name, tmux_bin=None: 9999 if name == "tmux-X" else None,
    )

    rows = sess.list_sessions(claude_home=empty_home)
    assert len(rows) == 1
    assert rows[0].state == "live"
    assert rows[0].id == NEW_ID
    assert rows[0].cwd == "/tmp/x"
    assert rows[0].url == "https://claude.ai/code/session_xfresh"
    assert rows[0].transcript_path is None


def test_pre_first_turn_flag(tmp_path: Path, monkeypatch) -> None:
    """A live session older than the grace period with no transcript is
    flagged as ``pre_first_turn`` (likely wedged on a startup TUI prompt).
    A freshly-spawned one within the grace period is not."""
    import time as _time
    from xa import claude_cli as ccli
    from xa import claude_fs as cfs
    from xa import tmux as tm
    from xa.hosts import local as local_host

    empty_home = tmp_path / ".claude"
    empty_home.mkdir()
    fake_tmux = tm.TmuxSession(name="t", created=0, activity=0, attached=False)
    monkeypatch.setattr(tm, "list_sessions", lambda binary=None: [fake_tmux])
    monkeypatch.setattr(ccli, "find_claude_pid", lambda *a, **kw: 42)

    # Session 1: 10 minutes old, no transcript → flagged.
    # Session 2: just spawned, still within grace → not flagged.
    now_ms = int(_time.time() * 1000)
    monkeypatch.setattr(
        cfs, "iter_ephemeral_sessions",
        lambda claude_home=None: iter([
            {
                "pid": 42,
                "sessionId": "11111111-1111-1111-1111-111111111111",
                "bridgeSessionId": "session_old",
                "cwd": "/tmp/a",
                "startedAt": now_ms - 10 * 60 * 1000,  # 10 min ago
            },
            {
                "pid": 42,
                "sessionId": "22222222-2222-2222-2222-222222222222",
                "bridgeSessionId": "session_fresh",
                "cwd": "/tmp/b",
                "startedAt": now_ms - 5 * 1000,  # 5 s ago
            },
        ]),
    )
    by_id = {r.id: r for r in sess.list_sessions(claude_home=empty_home)}
    assert by_id["11111111-1111-1111-1111-111111111111"].pre_first_turn is True
    assert by_id["22222222-2222-2222-2222-222222222222"].pre_first_turn is False
    # Grace threshold constant is the single source of truth.
    assert local_host.PRE_FIRST_TURN_GRACE_SEC > 5


def test_pre_first_turn_never_set_when_transcript_exists(
    fake_home: Path, monkeypatch
) -> None:
    """A live session matched to an existing transcript must never be
    flagged — it has clearly completed at least one turn."""
    from xa import claude_cli as ccli
    from xa import claude_fs as cfs
    from xa import tmux as tm

    fake_tmux = tm.TmuxSession(name="t", created=0, activity=0, attached=False)
    monkeypatch.setattr(tm, "list_sessions", lambda binary=None: [fake_tmux])
    monkeypatch.setattr(ccli, "find_claude_pid", lambda *a, **kw: 1)
    monkeypatch.setattr(
        cfs, "iter_ephemeral_sessions",
        # SID_A has a transcript in fake_home; starting "1970" is billions
        # of seconds ago, well past the grace period — still must be False.
        lambda claude_home=None: iter([
            {
                "pid": 1,
                "sessionId": SID_A,
                "bridgeSessionId": "session_a",
                "startedAt": 1_000,
            }
        ]),
    )
    rows = sess.list_sessions(claude_home=fake_home)
    live = [r for r in rows if r.state == "live"]
    assert len(live) == 1
    assert live[0].pre_first_turn is False


def test_no_live_flag_skips_tmux_scan(fake_home: Path, monkeypatch) -> None:
    """include_live=False must not touch tmux."""
    from xa import claude_cli as ccli
    from xa import tmux as tm

    def _boom(*a, **kw):
        raise AssertionError("tmux should not be consulted when include_live=False")

    monkeypatch.setattr(tm, "list_sessions", _boom)
    monkeypatch.setattr(ccli, "find_claude_pid", _boom)

    rows = sess.list_sessions(include_live=False, claude_home=fake_home)
    assert all(r.state == "transcript_only" for r in rows)


def test_state_filter(fake_home: Path, monkeypatch) -> None:
    from xa import claude_cli as ccli
    from xa import claude_fs as cfs
    from xa import tmux as tm

    fake_tmux = tm.TmuxSession(name="t", created=0, activity=0, attached=False)
    monkeypatch.setattr(
        cfs, "iter_ephemeral_sessions",
        lambda claude_home=None: iter([
            {"pid": 1, "sessionId": SID_A, "bridgeSessionId": "session_a"}
        ]),
    )
    monkeypatch.setattr(tm, "list_sessions", lambda binary=None: [fake_tmux])
    monkeypatch.setattr(ccli, "find_claude_pid", lambda *a, **kw: 1)

    live_rows = sess.list_sessions(state="live", claude_home=fake_home)
    assert [r.id for r in live_rows] == [SID_A]
    archive_rows = sess.list_sessions(state="transcript_only", claude_home=fake_home)
    assert SID_A not in {r.id for r in archive_rows}


def test_kill_session_rejects_non_live() -> None:
    s = sess.Session(
        id="x", claude_session_id="x", bridge_session_id=None, host="local",
        cwd="/", project_slug="/", state="transcript_only", live_pid=None,
        tmux_name=None, name=None, summary=None, first_user_message=None,
        turn_count=0, forked_from=None, created=None, modified=None,
        url=None, url_source=None, transcript_path=None,
    )
    with pytest.raises(ValueError):
        sess.kill_session(s)
