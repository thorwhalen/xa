"""Unit tests for ``xa.claude_cli`` — no real ``claude`` binary needed.

Tests here cover the pure bits (URL regex, session-file lookup path).
Integration-style tests for ``spawn_session`` require claude + are gated
behind ``XA_RUN_INTEGRATION=1``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from xa import claude_cli as ccli
from xa import tmux as tm


def test_url_regex_extracts_bridge_url() -> None:
    pane = (
        "random noise above\n"
        "Remote control active: https://claude.ai/code/session_01AbCdEfGhIjKlMn\n"
        "trailing text\n"
    )
    assert (
        ccli._extract_url_from_text(pane)
        == "https://claude.ai/code/session_01AbCdEfGhIjKlMn"
    )


def test_url_regex_returns_none_without_match() -> None:
    assert ccli._extract_url_from_text("nothing here") is None


def test_resolve_bridge_url_via_session_file(tmp_path: Path, monkeypatch) -> None:
    """Simulate: a pid with a matching ephemeral session file → URL from file."""
    claude_home = tmp_path / ".claude"
    (claude_home / "sessions").mkdir(parents=True)
    (claude_home / "sessions" / "1234.json").write_text(
        json.dumps({"pid": 1234, "bridgeSessionId": "session_testbridge"})
    )
    monkeypatch.setattr(ccli, "find_claude_pid", lambda *a, **kw: 1234)
    url, src = ccli.resolve_bridge_url("ignored", claude_home=claude_home)
    assert url == "https://claude.ai/code/session_testbridge"
    assert src == "session_file"


def test_resolve_bridge_url_falls_back_to_pane_scrape(
    tmp_path: Path, monkeypatch
) -> None:
    claude_home = tmp_path / ".claude"
    (claude_home / "sessions").mkdir(parents=True)
    # No session file → primary path returns nothing.
    monkeypatch.setattr(ccli, "find_claude_pid", lambda *a, **kw: None)
    monkeypatch.setattr(
        tm,
        "capture_pane",
        lambda *a, **kw: "Some pane output https://claude.ai/code/session_panehit tail",
    )
    url, src = ccli.resolve_bridge_url("ignored", claude_home=claude_home)
    assert url == "https://claude.ai/code/session_panehit"
    assert src == "pane_capture"


def test_find_claude_pid_returns_none_when_no_match() -> None:
    """Spawn a dummy tmux session with no 'claude' descendant."""
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")
    name = f"xa-test-{uuid.uuid4().hex[:8]}"
    tm.new_session(name, command="sh -c 'sleep 30'")
    try:
        time.sleep(0.2)
        assert ccli.find_claude_pid(name) is None
    finally:
        try:
            tm.kill_session(name)
        except RuntimeError:
            pass


# --------------------------------------------------------------------------- #
# Gated integration test: real `claude` spawn.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    os.environ.get("XA_RUN_INTEGRATION") != "1",
    reason="set XA_RUN_INTEGRATION=1 to run real claude spawn test",
)
def test_spawn_session_real(tmp_path) -> None:
    """Spawn a real claude process in a throwaway cwd and verify the URL appears.

    Requires: ``claude`` binary on PATH, network to register the bridge,
    and tmux. Slow (~30-120s). Cleaning up kills the tmux session after.
    """
    if shutil.which("claude") is None or shutil.which("tmux") is None:
        pytest.skip("claude or tmux missing")
    name = f"xa-test-{uuid.uuid4().hex[:8]}"
    result = ccli.spawn_session(
        name,
        cwd=str(tmp_path),
        url_timeout_sec=120.0,
    )
    try:
        assert result.url is not None
        assert result.url.startswith("https://claude.ai/code/session_")
        assert result.url_source in ("session_file", "pane_capture")
    finally:
        try:
            tm.kill_session(name)
        except RuntimeError:
            pass


# --------------------------------------------------------------------------- #
# CLI capability detection + argv composition
# --------------------------------------------------------------------------- #


def test_claude_argv_composes_supported_flags() -> None:
    supported = frozenset(
        {"--name", "--model", "--effort", "--remote-control", "--resume"}
    )
    argv, skipped = ccli._claude_argv(
        "claude",
        supported=supported,
        claude_name="n1",
        model="opus",
        effort="high",
        remote_control=True,
    )
    assert argv == [
        "claude", "--name", "n1", "--model", "opus", "--effort", "high",
        "--remote-control",
    ]
    assert skipped == []
    # --remote-control must come last and bare: its optional value could
    # otherwise swallow the next argument.
    assert argv[-1] == "--remote-control"


def test_claude_argv_skips_unsupported_flags() -> None:
    argv, skipped = ccli._claude_argv(
        "claude",
        supported=frozenset(),
        claude_name="n1",
        model="opus",
        effort="high",
        remote_control=True,
    )
    assert argv == ["claude"]
    assert set(skipped) == {"--name", "--model", "--effort", "--remote-control"}


def test_claude_argv_resume_always_included() -> None:
    argv, _ = ccli._claude_argv(
        "claude", supported=frozenset(), resume_id="abc-123"
    )
    assert argv == ["claude", "--resume", "abc-123"]


_FAKE_HELP = (
    "  -n, --name <name>   Set a display name\n"
    "  --model <model>     Model for the session\n"
    "  --remote-control [name]  Enable RC\n"
)


def test_supported_cli_flags_scrapes_help(monkeypatch) -> None:
    """The scrape itself — runs everywhere, since it fakes the process.

    Previously this shelled out to a ``#!/bin/sh`` fixture, which Windows
    cannot execute: the probe returned an empty set and the suite was red
    on Windows for a reason that had nothing to do with the parsing.
    """
    monkeypatch.setattr(
        ccli, "_flags_cache", {}
    )  # the real cache is process-global
    monkeypatch.setattr(
        ccli.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, _FAKE_HELP, ""),
    )
    flags = ccli.supported_cli_flags("whatever-claude")
    assert {"--name", "--model", "--remote-control"} <= flags
    assert "--effort" not in flags


@pytest.mark.skipif(
    os.name != "posix", reason="fixture is a #!/bin/sh script; Windows can't exec it"
)
def test_supported_cli_flags_runs_a_real_binary(tmp_path: Path) -> None:
    """End-to-end: really execute something and scrape its output."""
    fake = tmp_path / "fake-claude"
    fake.write_text("#!/bin/sh\n" + "".join(f"echo '{l}'\n" for l in _FAKE_HELP.splitlines()))
    fake.chmod(0o755)
    flags = ccli.supported_cli_flags(str(fake))
    assert {"--name", "--model", "--remote-control"} <= flags


def test_supported_cli_flags_missing_binary_is_empty() -> None:
    assert ccli.supported_cli_flags("/nonexistent/claude-nope") == frozenset()


# --------------------------------------------------------------------------- #
# pane attention classification
# --------------------------------------------------------------------------- #


def test_diagnose_bridgeless_delegates_to_revive(monkeypatch) -> None:
    """The spawn-timeout explanation comes from revive's rule engine.

    Pane classification lives in one place now; claude_cli only supplies
    the capture and passes the verdict through with its hint.
    """
    from xa import revive as rv

    monkeypatch.setattr(
        ccli.tm, "capture_pane", lambda *a, **k: "Please run /login to continue"
    )
    verdict, hint = ccli.diagnose_bridgeless("s1", claude_pid=42)
    assert verdict == rv.NEEDS_LOGIN
    assert "/login" in hint and "tmux attach -t s1" in hint


def test_diagnose_bridgeless_reports_trust_prompt(monkeypatch) -> None:
    from xa import revive as rv

    monkeypatch.setattr(
        ccli.tm, "capture_pane", lambda *a, **k: "Do you trust the files in this folder?"
    )
    verdict, hint = ccli.diagnose_bridgeless("s1", claude_pid=42)
    assert verdict == rv.NEEDS_TRUST
    assert "trust" in hint.lower()


def test_spawn_no_longer_types_into_the_pane(monkeypatch) -> None:
    """The handshake is gone: waiting for the URL sends no keystrokes.

    Regression guard for the whole point of retiring it — a poll that
    silently regrew a send_keys call would be invisible otherwise.
    """
    monkeypatch.setattr(
        ccli.tm,
        "send_keys",
        lambda *a, **k: pytest.fail("wait_for_bridge_url must not type into the pane"),
    )
    monkeypatch.setattr(ccli, "resolve_bridge_url", lambda *a, **k: (None, None))
    url, src = ccli.wait_for_bridge_url(
        "s1", claude_home=Path("/nonexistent"), tmux_bin="/nonexistent/tmux",
        deadline=time.time() + 0.05, poll_sec=0.01,
    )
    assert (url, src) == (None, None)
