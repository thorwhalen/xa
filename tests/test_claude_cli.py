"""Unit tests for ``xa.claude_cli`` — no real ``claude`` binary needed.

Tests here cover the pure bits (URL regex, session-file lookup path).
Integration-style tests for ``spawn_session`` require claude + are gated
behind ``XA_RUN_INTEGRATION=1``.
"""

from __future__ import annotations

import json
import os
import shutil
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


def test_supported_cli_flags_scrapes_help(tmp_path: Path) -> None:
    fake = tmp_path / "fake-claude"
    fake.write_text(
        "#!/bin/sh\n"
        "echo '  -n, --name <name>   Set a display name'\n"
        "echo '  --model <model>     Model for the session'\n"
        "echo '  --remote-control [name]  Enable RC'\n"
    )
    fake.chmod(0o755)
    flags = ccli.supported_cli_flags(str(fake))
    assert {"--name", "--model", "--remote-control"} <= flags
    assert "--effort" not in flags


def test_supported_cli_flags_missing_binary_is_empty() -> None:
    assert ccli.supported_cli_flags("/nonexistent/claude-nope") == frozenset()


# --------------------------------------------------------------------------- #
# pane attention classification
# --------------------------------------------------------------------------- #


def test_classify_pane_attention_login_markers() -> None:
    assert (
        ccli.classify_pane_attention("● Unknown command: /remote-control")
        == ccli.ATTENTION_LOGIN_REQUIRED
    )
    assert (
        ccli.classify_pane_attention("Please run /login to continue")
        == ccli.ATTENTION_LOGIN_REQUIRED
    )
    assert (
        ccli.classify_pane_attention("OAuth token has expired.")
        == ccli.ATTENTION_LOGIN_REQUIRED
    )


def test_classify_pane_attention_trust_prompt() -> None:
    assert (
        ccli.classify_pane_attention("Do you trust the files in this folder?")
        == ccli.ATTENTION_TRUST_PROMPT
    )


def test_classify_pane_attention_healthy_is_none() -> None:
    assert ccli.classify_pane_attention("❯ waiting for input…") is None
    assert ccli.classify_pane_attention("") is None


def test_attention_hint_carries_fix_commands() -> None:
    hint = ccli.attention_hint(ccli.ATTENTION_LOGIN_REQUIRED, tmux_name="s1")
    assert "/login" in hint and "tmux attach -t s1" in hint
    trust = ccli.attention_hint(ccli.ATTENTION_TRUST_PROMPT, tmux_name="s1")
    assert "trust" in trust.lower()
    assert ccli.attention_hint(None) is None
