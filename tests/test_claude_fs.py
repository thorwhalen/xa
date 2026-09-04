"""Unit tests for ``xa.claude_fs`` — fixture-based, no real ~/.claude access."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xa import claude_fs as cfs


SESSION_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FORK_PARENT_UUID = "11111111-2222-3333-4444-555555555555"


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


@pytest.fixture()
def fake_home(tmp_path: Path) -> Path:
    """Build a fake ~/.claude/ with one project, one session, some ephemerals."""
    home = tmp_path / ".claude"
    projects = home / "projects" / "-foo-bar"

    events = [
        {
            "type": "user",
            "sessionId": SESSION_UUID,
            "cwd": "/foo/bar",
            "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        },
        {
            "type": "assistant",
            "sessionId": SESSION_UUID,
            "cwd": "/foo/bar",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "reply one"},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        },
        {
            "type": "user",
            "sessionId": SESSION_UUID,
            "cwd": "/foo/bar",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "content": "total 0\nExit code 0"}
                ],
            },
        },
        {
            "type": "summary",
            "summary": "Talked about files.",
        },
    ]
    _write_jsonl(projects / f"{SESSION_UUID}.jsonl", events)

    # A non-session file inside a project dir (the memory/ subdir case).
    (projects / "memory").mkdir()
    (projects / "memory" / "MEMORY.md").write_text("# memory\n")

    # Ephemeral session file.
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "4242.json").write_text(
        json.dumps(
            {
                "pid": 4242,
                "sessionId": SESSION_UUID,
                "cwd": "/foo/bar",
                "bridgeSessionId": "session_abc123",
            }
        )
    )
    # A bogus file that should be ignored.
    (sessions / "not-a-pid.json").write_text("{}")

    # history.jsonl
    (home / "history.jsonl").write_text(
        json.dumps({"cwd": "/foo/bar", "display": "hello"}) + "\n"
        + json.dumps({"cwd": "/baz", "display": "goodbye"}) + "\n"
    )
    return home


# --------------------------------------------------------------------------- #
# slug round-trip
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "cwd",
    [
        "/foo/bar",
        "/root/py/proj/tt/glossa",
        "/Users/thorwhalen/Dropbox/py/proj",
        "/",
    ],
)
def test_slug_roundtrip(cwd: str) -> None:
    assert cfs.parse_project_slug(cfs.encode_project_slug(cwd)) == cwd


# --------------------------------------------------------------------------- #
# project / transcript enumeration
# --------------------------------------------------------------------------- #


def test_iter_project_slugs(fake_home: Path) -> None:
    assert list(cfs.iter_project_slugs(claude_home=fake_home)) == ["-foo-bar"]


def test_iter_transcript_files_filters_non_uuid(fake_home: Path) -> None:
    files = list(cfs.iter_transcript_files(claude_home=fake_home))
    assert len(files) == 1
    assert files[0].stem == SESSION_UUID
    # The memory/ subdir contents must not be yielded.
    assert "memory" not in str(files[0])


def test_iter_transcript_files_empty_when_no_projects(tmp_path: Path) -> None:
    assert list(cfs.iter_transcript_files(claude_home=tmp_path)) == []


def test_iter_transcript_files_scoped_by_project(fake_home: Path) -> None:
    files = list(
        cfs.iter_transcript_files(claude_home=fake_home, project_slug="-foo-bar")
    )
    assert len(files) == 1
    assert (
        list(
            cfs.iter_transcript_files(
                claude_home=fake_home, project_slug="-does-not-exist"
            )
        )
        == []
    )


# --------------------------------------------------------------------------- #
# ephemeral
# --------------------------------------------------------------------------- #


def test_read_ephemeral_session(fake_home: Path) -> None:
    data = cfs.read_ephemeral_session(4242, claude_home=fake_home)
    assert data is not None
    assert data["bridgeSessionId"] == "session_abc123"
    assert cfs.read_ephemeral_session(9999, claude_home=fake_home) is None


def test_iter_ephemeral_sessions_skips_bogus(fake_home: Path) -> None:
    sessions = list(cfs.iter_ephemeral_sessions(claude_home=fake_home))
    assert len(sessions) == 1
    assert sessions[0]["pid"] == 4242


# --------------------------------------------------------------------------- #
# transcript metadata / forensics
# --------------------------------------------------------------------------- #


def test_transcript_metadata(fake_home: Path) -> None:
    path = next(cfs.iter_transcript_files(claude_home=fake_home))
    meta = cfs.transcript_metadata(path)
    assert meta.session_id == SESSION_UUID
    assert meta.cwd == "/foo/bar"
    assert meta.project_slug == "-foo-bar"
    assert meta.turn_count == 3  # 2 user + 1 assistant
    assert meta.first_user_message == "hello"
    assert meta.summary == "Talked about files."
    assert meta.forked_from is None


def test_transcript_metadata_handles_fork(tmp_path: Path) -> None:
    path = tmp_path / "projects" / "-foo" / f"{SESSION_UUID}.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "sessionId": SESSION_UUID,
                "cwd": "/foo",
                "forkedFrom": {"sessionId": FORK_PARENT_UUID},
                "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            }
        ],
    )
    meta = cfs.transcript_metadata(path)
    assert meta.forked_from == FORK_PARENT_UUID


def test_transcript_forensics(fake_home: Path) -> None:
    path = next(cfs.iter_transcript_files(claude_home=fake_home))
    f = cfs.transcript_forensics(path)
    assert f.last_tool_name == "Bash"
    assert f.last_tool_command == "ls"
    assert f.last_tool_exit_code == 0
    assert f.final_assistant_text == "reply one"
    assert f.user_interrupted is False


def test_transcript_forensics_detects_interrupt_marker(tmp_path: Path) -> None:
    path = tmp_path / "p" / f"{SESSION_UUID}.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "sessionId": SESSION_UUID,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "content": "Request interrupted by user for tool use",
                        }
                    ],
                },
            }
        ],
    )
    f = cfs.transcript_forensics(path)
    assert f.user_interrupted is True


# --------------------------------------------------------------------------- #
# history
# --------------------------------------------------------------------------- #


def test_history_iter(fake_home: Path) -> None:
    entries = list(cfs.history_iter(claude_home=fake_home))
    assert [e.display for e in entries] == ["hello", "goodbye"]


def test_history_iter_missing_file(tmp_path: Path) -> None:
    assert list(cfs.history_iter(claude_home=tmp_path)) == []


# --------------------------------------------------------------------------- #
# transcript_path
# --------------------------------------------------------------------------- #


def test_transcript_path_found_and_missing(fake_home: Path) -> None:
    p = cfs.transcript_path("/foo/bar", SESSION_UUID, claude_home=fake_home)
    assert p is not None and p.is_file()
    assert cfs.transcript_path("/foo/bar", "nope", claude_home=fake_home) is None


# --------------------------------------------------------------------------- #
# ephemeral_session_alive (stale session-file detection)
# --------------------------------------------------------------------------- #


def test_alive_rejects_missing_or_bad_pid() -> None:
    assert cfs.ephemeral_session_alive({}) is False
    assert cfs.ephemeral_session_alive({"pid": "42"}) is False
    assert cfs.ephemeral_session_alive({"pid": -3}) is False
    assert cfs.ephemeral_session_alive({"pid": 0}) is False


def test_alive_rejects_dead_pid() -> None:
    # 4194303 is Linux's default pid_max ceiling — effectively never in
    # use; on non-/proc platforms os.kill(pid, 0) raises for it the same.
    assert cfs.ephemeral_session_alive({"pid": 4194303}) is False


def test_alive_procstart_match_and_mismatch() -> None:
    import os

    if not Path("/proc").is_dir():
        pytest.skip("requires /proc")
    me = os.getpid()
    started = cfs._proc_starttime(me)
    assert started is not None
    # Matching procStart proves the pid wasn't reused — alive even though
    # this process isn't named "claude" (procStart beats the comm gate).
    assert cfs.ephemeral_session_alive({"pid": me, "procStart": started}) is True
    # Mismatching procStart = the recorded process died and the pid was
    # reused — must read as dead.
    assert cfs.ephemeral_session_alive({"pid": me, "procStart": "1"}) is False


def test_alive_comm_gate_without_procstart() -> None:
    import os

    if not Path("/proc").is_dir():
        pytest.skip("requires /proc")
    # No procStart (older claude file shape): fall back to requiring
    # comm == "claude". This test process is python — must be rejected
    # even though the pid is alive.
    assert cfs.ephemeral_session_alive({"pid": os.getpid()}) is False


# --------------------------------------------------------------------------- #
# remoteControlAtStartup
# --------------------------------------------------------------------------- #


def test_remote_control_at_startup_reads_the_setting(tmp_path) -> None:
    home = tmp_path / ".claude"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"remoteControlAtStartup": True}))
    assert cfs.remote_control_at_startup(claude_home=home) is True
    (home / "settings.json").write_text(json.dumps({"remoteControlAtStartup": False}))
    assert cfs.remote_control_at_startup(claude_home=home) is False


@pytest.mark.parametrize(
    "content",
    [
        None,                                    # no file at all
        "{ not json",                            # unreadable
        "{}",                                    # key absent
        '{"remoteControlAtStartup": "yes"}',     # present but not a bool
    ],
)
def test_remote_control_at_startup_is_none_when_unknowable(tmp_path, content) -> None:
    """Every "can't tell" case must be None, never a guessed False.

    A False would read as "this host is configured off" and send the user
    to change a setting that may already be right.
    """
    home = tmp_path / ".claude"
    home.mkdir()
    if content is not None:
        (home / "settings.json").write_text(content)
    assert cfs.remote_control_at_startup(claude_home=home) is None
