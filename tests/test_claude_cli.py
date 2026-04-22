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
