"""Tests for ``xa.recovery`` and the account-aware resume path (issue #23).

Everything runs against fake transcripts and, for the end-to-end test, a fake
``claude`` inside a private tmux server (``conftest.py``) whose global
environment is deliberately polluted the way the 2026-11-10 recovery found it.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from xa import claude_cli as ccli
from xa import claude_fs as cfs
from xa import recovery as rc


SID_A = "aaaaaaaa-1111-2222-3333-444444444444"
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"
SID_C = "cccccccc-1111-2222-3333-444444444444"


def _write_transcript(home: Path, cwd: str, sid: str, events: list[dict]) -> Path:
    # ':' and '\\' cannot be in a Windows path component; a slug that no
    # longer matches only means launch_facts falls back to the first cwd.
    slug = cfs.encode_project_slug(cwd).replace(":", "-").replace("\\", "-")
    pdir = home / "projects" / slug
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{sid}.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return path


def _session_events(cwd: str, sid: str, *, later_cwd: str, model="claude-sonnet-5-5"):
    return [
        {"type": "custom-title", "customTitle": "the lookout", "sessionId": sid},
        {
            "type": "permission-mode",
            "permissionMode": "bypassPermissions",
            "sessionId": sid,
        },
        {
            "type": "user",
            "cwd": cwd,
            "sessionId": sid,
            "permissionMode": "bypassPermissions",
            "message": {"role": "user", "content": "hi"},
        },
        {
            "type": "assistant",
            "cwd": cwd,
            "sessionId": sid,
            "effort": "high",
            "message": {"role": "assistant", "model": model, "content": []},
        },
        # The session cd-ed elsewhere: later entries record the new cwd.
        {
            "type": "user",
            "cwd": later_cwd,
            "sessionId": sid,
            "permissionMode": "auto",
            "message": {"role": "user", "content": "go"},
        },
        {
            "type": "assistant",
            "cwd": later_cwd,
            "sessionId": sid,
            "message": {"role": "assistant", "model": "<synthetic>", "content": []},
        },
        {"type": "agent-name", "agentName": "lookout", "sessionId": sid},
        {"type": "cost-state", "sessionId": sid},
    ]


# --------------------------------------------------------------------------- #
# launch_facts
# --------------------------------------------------------------------------- #


def test_cwd_is_the_first_cwd_not_the_last(tmp_path: Path) -> None:
    """The 2026-11-10 bug: the last cwd put 32 of 65 sessions in the wrong folder."""
    cwd, later = "/work/proj", "/work/proj/memory"
    path = _write_transcript(
        tmp_path, cwd, SID_A, _session_events(cwd, SID_A, later_cwd=later)
    )
    assert rc.launch_facts(path).cwd == cwd


def test_cwd_prefers_the_one_matching_the_project_folder(tmp_path: Path) -> None:
    """A first entry recorded elsewhere (a hook, a resumed fork) does not win."""
    cwd = "/work/proj"
    events = [{"type": "user", "cwd": "/somewhere/else", "sessionId": SID_A}]
    events += _session_events(cwd, SID_A, later_cwd="/tmp")
    path = _write_transcript(tmp_path, cwd, SID_A, events)
    assert rc.launch_facts(path).cwd == cwd


def test_launch_facts_reads_mode_model_effort_name(tmp_path: Path) -> None:
    cwd = "/work/proj"
    path = _write_transcript(
        tmp_path, cwd, SID_A, _session_events(cwd, SID_A, later_cwd="/tmp")
    )
    f = rc.launch_facts(path)
    assert f.session_id == SID_A
    assert f.permission_mode == "auto"  # the last one recorded
    assert f.bypass_seen is True
    assert f.model == "claude-sonnet-5-5"  # '<synthetic>' is not a model
    assert f.effort == "high"
    assert f.name == "lookout"  # agent name beats custom title
    assert f.last_type == "cost-state"


def test_resume_permission_keeps_bypass_reachable(monkeypatch) -> None:
    monkeypatch.delenv("IS_SANDBOX", raising=False)
    monkeypatch.setenv("CROWSNEST_CONFIG", "/no/such/config.toml")
    f = rc.LaunchFacts(SID_A, Path("t"), "/p", "auto", True, None, None, None, None, 0)
    assert rc.resume_permission(f, euid=501) == ("auto", True, None)
    g = rc.LaunchFacts(
        SID_A, Path("t"), "/p", "bypassPermissions", True, None, None, None, None, 0
    )
    assert rc.resume_permission(g, euid=501) == ("bypassPermissions", False, None)
    mode, allow, warning = rc.resume_permission(g, euid=0)
    assert (mode, allow) == ("", False) and "root" in warning
    import crowsnest.spawn as cn_spawn

    if hasattr(cn_spawn, "SANDBOX_VAR"):  # crowsnest's rule since its #161
        monkeypatch.setenv("IS_SANDBOX", "1")  # where claude accepts bypass as root
        assert rc.resume_permission(g, euid=0) == ("bypassPermissions", False, None)
    h = rc.LaunchFacts(SID_A, Path("t"), "/p", "", False, None, None, None, None, 0)
    assert rc.resume_permission(h, euid=501) == ("", False, None)


# --------------------------------------------------------------------------- #
# argv + env
# --------------------------------------------------------------------------- #


ALL_FLAGS = frozenset(
    {
        "--resume",
        "--name",
        "--model",
        "--effort",
        "--remote-control",
        "--dangerously-skip-permissions",
        "--allow-dangerously-skip-permissions",
        "--permission-mode",
    }
)


def test_claude_argv_permission_flags() -> None:
    argv, _ = ccli._claude_argv(
        "claude",
        supported=ALL_FLAGS,
        resume_id="x",
        model="sonnet",
        permission_mode="bypassPermissions",
        remote_control=True,
    )
    assert argv == [
        "claude",
        "--resume",
        "x",
        "--dangerously-skip-permissions",
        "--model",
        "sonnet",
        "--remote-control",
    ]
    argv, _ = ccli._claude_argv(
        "claude", supported=ALL_FLAGS, permission_mode="auto", allow_bypass=True
    )
    assert argv == [
        "claude",
        "--allow-dangerously-skip-permissions",
        "--permission-mode",
        "auto",
    ]
    argv, skipped = ccli._claude_argv(
        "claude", supported=frozenset(), permission_mode="bypassPermissions"
    )
    assert argv == ["claude"] and skipped == ["--dangerously-skip-permissions"]


def test_account_env_unsets_config_dir_for_the_default_home(monkeypatch) -> None:
    """The default home is reached only by the variable's absence: an unset, not -e."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/h/other")
    monkeypatch.setenv("CLAUDECODE", "1")
    tokens = ccli.account_env_tokens(Path("~/.claude").expanduser())
    assert tokens[0] == "env"
    pairs = list(zip(tokens, tokens[1:]))
    assert ("-u", "CLAUDE_CONFIG_DIR") in pairs
    assert ("-u", "CLAUDECODE") in pairs
    assert ("-u", "CLAUDE_CODE_SESSION_ID") in pairs  # even when this process lacks it
    assert ("-u", "ANTHROPIC_API_KEY") in pairs
    assert not any(t.startswith("CLAUDE_CONFIG_DIR=") for t in tokens)


def test_account_env_states_a_second_home(tmp_path: Path) -> None:
    tokens = ccli.account_env_tokens(tmp_path)
    assert f"CLAUDE_CONFIG_DIR={tmp_path.resolve()}" in tokens


def test_default_claude_home_follows_the_account() -> None:
    assert cfs.default_claude_home({"CLAUDE_CONFIG_DIR": "/h/iq"}) == Path("/h/iq")
    assert cfs.default_claude_home({}) == Path.home() / ".claude"


# --------------------------------------------------------------------------- #
# shutdown detection
# --------------------------------------------------------------------------- #


def test_parse_last_stamp_rolls_the_year_back() -> None:
    jan = time.mktime((2027, 1, 2, 12, 0, 0, 0, 0, -1))
    ts = rc.parse_last_stamp("shutdown time  Thu Dec 31 23:58", before=jan)
    assert time.localtime(ts)[:5] == (2026, 12, 31, 23, 58)
    assert rc.parse_last_stamp("wtmp begins Sun", before=jan) is None


def test_last_shutdown_time_reads_macos_and_linux_shapes(monkeypatch) -> None:
    before = time.mktime((2026, 11, 10, 10, 0, 0, 0, 0, -1))
    mac = "reboot time    Tue Nov 10 09:52\nshutdown time   Tue Nov 10 09:50\n"
    linux = "shutdown system down  6.8.0-45 Tue Nov 10 09:50 - 09:52  (00:01)\n"
    for platform, out in (("darwin", mac), ("linux", linux)):
        monkeypatch.setattr(rc.sys, "platform", platform)
        ts = rc.last_shutdown_time(before=before, run=lambda argv, out=out: out)
        assert time.localtime(ts)[:5] == (2026, 11, 10, 9, 50)


def _home_with_three_sessions(tmp_path: Path, *, shutdown: float) -> Path:
    home = tmp_path / "home"
    for sid, cwd, mtime in (
        (SID_A, "/w/a", shutdown + 5),  # died with the shutdown
        (SID_B, "/w/b", shutdown - 3600),  # exited an hour earlier
        (SID_C, "/w/c", shutdown + 600),  # resumed after boot
    ):
        path = _write_transcript(
            home, cwd, sid, _session_events(cwd, sid, later_cwd="/t")
        )
        os.utime(path, (mtime, mtime))
    return home


def test_sessions_alive_at_shutdown_picks_the_shutdown_window(tmp_path: Path) -> None:
    shutdown = 1_800_000_000.0
    home = _home_with_three_sessions(tmp_path, shutdown=shutdown)
    scan = rc.sessions_alive_at_shutdown(
        claude_home=home, shutdown=shutdown, boot=shutdown + 120, live_ids=set()
    )
    assert scan.anchor_source == "last"
    assert [f.session_id for f in scan.candidates] == [SID_A]


def test_sessions_alive_at_shutdown_skips_live_sessions(tmp_path: Path) -> None:
    shutdown = 1_800_000_000.0
    home = _home_with_three_sessions(tmp_path, shutdown=shutdown)
    scan = rc.sessions_alive_at_shutdown(
        claude_home=home, shutdown=shutdown, boot=shutdown + 120, live_ids={SID_A}
    )
    assert scan.candidates == []


def test_a_stale_shutdown_record_falls_back_to_the_latest_write(tmp_path: Path) -> None:
    """A record from an earlier power cycle must not open a days-wide window."""
    shutdown = 1_800_000_000.0
    home = _home_with_three_sessions(tmp_path, shutdown=shutdown)
    scan = rc.sessions_alive_at_shutdown(
        claude_home=home, shutdown=shutdown - 86400, boot=shutdown + 120, live_ids=set()
    )
    assert scan.anchor_source == "latest-write"
    assert [f.session_id for f in scan.candidates] == [SID_A]


def test_tmux_name_for_is_unique_and_safe() -> None:
    f = rc.LaunchFacts(SID_A, Path("t"), None, "", False, None, None, "a/b c!", None, 0)
    assert rc.tmux_name_for(f) == "a-b-c"
    g = rc.LaunchFacts(SID_A, Path("t"), None, "", False, None, None, None, None, 0)
    assert rc.tmux_name_for(g) == "resumed-aaaaaaaa"


# --------------------------------------------------------------------------- #
# sessions.resume carries the transcript's launch facts
# --------------------------------------------------------------------------- #


def test_session_resume_fills_launch_facts_from_the_transcript(tmp_path: Path) -> None:
    from xa import sessions as sess

    cwd = str(tmp_path / "proj")
    Path(cwd).mkdir()
    path = _write_transcript(
        tmp_path / "home", cwd, SID_A, _session_events(cwd, SID_A, later_cwd="/t")
    )
    seen = {}

    class _Host:
        def resume(self, sid, *, cwd, **opts):
            seen.update(sid=sid, cwd=cwd, **opts)

    s = sess.Session(
        id=SID_A,
        claude_session_id=SID_A,
        bridge_session_id=None,
        host="h",
        cwd="/t",
        project_slug="x",
        state="transcript_only",
        live_pid=None,
        tmux_name=None,
        name=None,
        summary=None,
        first_user_message=None,
        turn_count=0,
        forked_from=None,
        created=None,
        modified=None,
        url=None,
        url_source=None,
        transcript_path=path,
    )
    sess.resume(s, hosts={"h": _Host()}, model=None)
    assert seen["cwd"] == cwd
    assert seen["model"] == "claude-sonnet-5-5"
    assert seen["effort"] == "high"
    assert (seen["permission_mode"], seen["allow_bypass"]) == ("auto", True)
    sess.resume(s, hosts={"h": _Host()}, model="opus", permission_mode="")
    assert seen["model"] == "opus" and seen["permission_mode"] == ""


# --------------------------------------------------------------------------- #
# end to end: a fake claude in a private, polluted tmux server
# --------------------------------------------------------------------------- #


_FAKE_CLAUDE = """#!/bin/sh
if [ "$1" = "--help" ]; then
  echo "--resume --model --effort --remote-control --permission-mode"
  echo "--dangerously-skip-permissions --allow-dangerously-skip-permissions"
  exit 0
fi
{{ pwd; printf '%s\\n' "$@"; echo ---; env; }} > {record}/"$$".txt
echo "Do you trust the files in this folder?"
sleep 30
"""


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_resume_all_end_to_end_in_a_polluted_tmux_server(
    tmp_path: Path, monkeypatch
) -> None:
    from xa import tmux as tm

    # The fake claude is a shell script, which xa rightly does not take for a
    # claude process; point the lookup at the pane's own program instead.
    monkeypatch.setattr(
        ccli,
        "find_claude_pid",
        lambda name, *, tmux_bin=tm.DEFAULT_TMUX_BIN: tm.pane_pid(
            name, binary=tmux_bin
        ),
    )
    record = tmp_path / "record"
    record.mkdir()
    claude = tmp_path / "bin" / "claude"
    claude.parent.mkdir()
    claude.write_text(_FAKE_CLAUDE.format(record=shlex.quote(str(record))))
    claude.chmod(0o755)

    # The server the panes inherit from was started by another account's
    # session: its global env carries that account and that session's identity.
    polluted = {
        "CLAUDE_CONFIG_DIR": "/h/other-account",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_SESSION_ID": "dead-parent",
        "CLAUDE_EFFORT": "low",
    }
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", "xa-test-keepalive", "sleep 60"], check=True
    )
    for k, v in polluted.items():
        subprocess.run(["tmux", "set-environment", "-g", k, v], check=True)
    try:
        cwd = tmp_path / "proj"
        cwd.mkdir()
        home = Path("~/.claude").expanduser()  # the default account
        path = _write_transcript(
            tmp_path / "fakehome",
            str(cwd),
            SID_A,
            _session_events(str(cwd), SID_A, later_cwd=str(tmp_path)),
        )
        facts = rc.launch_facts(path)
        done = rc.resume_all(
            [facts],
            claude_home=home,
            claude_bin=str(claude),
            url_timeout_sec=2.0,
            euid=501,
        )
        assert len(done) == 1 and done[0].skipped is None
        result = done[0].result
        assert result.name == "lookout"
        assert result.url is None and result.attention == "needs_trust"

        files = list(record.glob("*.txt"))
        assert len(files) == 1
        pwd, rest = files[0].read_text().split("\n", 1)
        args, env_dump = rest.split("---\n", 1)
        env_names = {line.split("=", 1)[0] for line in env_dump.splitlines()}
        assert Path(pwd).resolve() == cwd.resolve()  # the first cwd, not the last
        args = args.split()
        assert args[:2] == ["--resume", SID_A]
        assert "--allow-dangerously-skip-permissions" in args
        assert args[args.index("--permission-mode") + 1] == "auto"
        assert args[args.index("--model") + 1] == "claude-sonnet-5-5"
        assert args[args.index("--effort") + 1] == "high"
        for leaked in (
            "CLAUDE_CONFIG_DIR",
            "CLAUDECODE",
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_EFFORT",
        ):
            assert leaked not in env_names, leaked
    finally:
        for k in polluted:
            subprocess.run(["tmux", "set-environment", "-gu", k], capture_output=True)
        subprocess.run(
            ["tmux", "kill-session", "-t", "xa-test-keepalive:"], capture_output=True
        )
        subprocess.run(["tmux", "kill-session", "-t", "lookout:"], capture_output=True)
