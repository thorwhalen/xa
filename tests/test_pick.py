"""Test the interactive picker end-to-end via subprocess + stdin piping.

The picker is a plain ``input()``-driven loop so we exercise it by
feeding stdin. Focus: the listing renders, invalid input exits with
nonzero, and ``q`` exits cleanly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SID_A = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _fake_home(tmp_path: Path) -> Path:
    home = tmp_path / ".claude"
    pdir = home / "projects" / "-foo-bar"
    pdir.mkdir(parents=True)
    with (pdir / f"{SID_A}.jsonl").open("w") as f:
        f.write(json.dumps({
            "type": "user", "sessionId": SID_A, "cwd": "/foo/bar",
            "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        }) + "\n")
    return home


def _run(args, *, env_home: Path, stdin: str, timeout: float = 15.0):
    env = {**os.environ, "XA_CLAUDE_HOME": str(env_home),
           "XA_CONFIG": str(env_home / "_no_config.toml")}
    return subprocess.run(
        [sys.executable, "-m", "xa", *args],
        env=env, input=stdin, capture_output=True, text=True, timeout=timeout,
    )


def test_pick_lists_and_quits(tmp_path: Path) -> None:
    home = _fake_home(tmp_path)
    r = _run(["pick"], env_home=home, stdin="q\n")
    assert r.returncode == 0, r.stderr
    assert SID_A[:8] in r.stdout
    # Prompt is emitted even on immediate quit.
    assert "pick #" in r.stdout


def test_pick_empty_prints_no_sessions(tmp_path: Path) -> None:
    # Empty ~/.claude → "no sessions."
    home = tmp_path / ".claude"
    home.mkdir()
    r = _run(["pick"], env_home=home, stdin="")
    assert r.returncode == 0
    assert "no sessions" in r.stdout.lower()


def test_pick_invalid_number_exits_nonzero(tmp_path: Path) -> None:
    home = _fake_home(tmp_path)
    r = _run(["pick"], env_home=home, stdin="99\n")
    assert r.returncode != 0
    assert "out of range" in r.stderr.lower()
