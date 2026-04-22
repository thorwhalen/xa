"""Integration tests for ``xa.cli`` — drive the CLI as a subprocess.

Uses ``XA_CLAUDE_HOME`` to point at a fake ~/.claude tree so the tests
are hermetic and don't read the user's real sessions.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from xa import claude_fs as cfs
# Reuse the fake_home builder from the sessions test module.
from tests.test_sessions import SID_A, SID_B, SID_C, fake_home  # noqa: F401


def _run(args: list[str], *, env_home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "XA_CLAUDE_HOME": str(env_home)}
    return subprocess.run(
        [sys.executable, "-m", "xa", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_list_prints_all_sessions(fake_home: Path) -> None:
    r = _run(["list"], env_home=fake_home)
    assert r.returncode == 0, r.stderr
    # Short ids should be visible.
    for sid in (SID_A, SID_B, SID_C):
        assert sid[:8] in r.stdout


def test_list_json_out(fake_home: Path) -> None:
    r = _run(["list", "--json-out"], env_home=fake_home)
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(lines) == 3
    ids = {json.loads(ln)["id"] for ln in lines}
    assert ids == {SID_A, SID_B, SID_C}


def test_list_project_filter(fake_home: Path) -> None:
    r = _run(["list", "--project", "baz"], env_home=fake_home)
    assert r.returncode == 0, r.stderr
    assert SID_C[:8] in r.stdout
    assert SID_B[:8] in r.stdout
    assert SID_A[:8] not in r.stdout


def test_info_prefix_lookup(fake_home: Path) -> None:
    r = _run(["info", SID_A[:8]], env_home=fake_home)
    assert r.returncode == 0, r.stderr
    assert SID_A in r.stdout
    assert "cwd:" in r.stdout and "/foo/bar" in r.stdout


def test_info_missing(fake_home: Path) -> None:
    r = _run(["info", "deadbeef"], env_home=fake_home)
    assert r.returncode != 0
    assert "no session" in r.stderr.lower()


def test_gen_secret_is_hex_and_right_length(fake_home: Path) -> None:
    r = _run(["gen-secret"], env_home=fake_home)
    assert r.returncode == 0
    out = r.stdout.strip()
    assert len(out) == 64  # default 32 bytes → 64 hex chars
    int(out, 16)  # must parse


def test_gen_secret_honors_length(fake_home: Path) -> None:
    r = _run(["gen-secret", "--length", "16"], env_home=fake_home)
    assert r.returncode == 0 and len(r.stdout.strip()) == 32


def test_serve_refuses_public_bind_without_auth(fake_home: Path) -> None:
    """The hard guardrail: --host 0.0.0.0 without --username is rejected."""
    r = _run(["serve", "--host", "0.0.0.0", "--port", "18010"], env_home=fake_home)
    assert r.returncode == 2
    assert "refusing to bind" in r.stderr.lower()
    # Help text should point the user at the fix.
    assert "gen-secret" in r.stderr
    assert "--captcha" in r.stderr


def test_serve_allows_loopback_without_auth(monkeypatch) -> None:
    """127.0.0.1 is fine without auth (only reachable by this machine).

    Runs in-process with uvicorn.run stubbed so we don't actually bind.
    """
    import uvicorn
    from xa import cli as xa_cli

    called = {}

    def fake_run(app, host, port):
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr(uvicorn, "run", fake_run)
    xa_cli.serve_cmd(host="127.0.0.1", port=18010)
    assert called == {"host": "127.0.0.1", "port": 18010}


def test_serve_bypass_with_insecure_flag(monkeypatch) -> None:
    """--i-know-its-insecure lets the public-bind-without-auth through."""
    import uvicorn
    from xa import cli as xa_cli

    called = {}
    monkeypatch.setattr(
        uvicorn, "run",
        lambda app, host, port: called.update(host=host, port=port),
    )
    xa_cli.serve_cmd(host="0.0.0.0", port=18010, i_know_its_insecure=True)
    assert called == {"host": "0.0.0.0", "port": 18010}


def test_history_with_search(tmp_path: Path) -> None:
    home = tmp_path / ".claude"
    home.mkdir()
    (home / "history.jsonl").write_text(
        json.dumps({"cwd": "/a", "display": "hello world"}) + "\n"
        + json.dumps({"cwd": "/b", "display": "goodbye world"}) + "\n"
        + json.dumps({"cwd": "/c", "display": "nothing here"}) + "\n"
    )
    r = _run(["history", "--search", "world"], env_home=home)
    assert r.returncode == 0, r.stderr
    assert "hello world" in r.stdout
    assert "goodbye world" in r.stdout
    assert "nothing here" not in r.stdout
