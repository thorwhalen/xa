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
