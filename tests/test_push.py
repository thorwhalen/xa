"""``xa push``: the mirror holds exactly the live sessions, their transcripts, and a stamp."""

import json
import os
import shutil
import subprocess
import time

import pytest

from xa.push import MIRROR_STAMP, live_records, push_commands


def _home(tmp_path):
    home = tmp_path / "home"
    (home / "sessions").mkdir(parents=True)
    (home / "projects" / "-w").mkdir(parents=True)
    for pid, sid in [(111, "live-a"), (222, "dead-b")]:
        (home / "sessions" / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": sid}))
        (home / "projects" / "-w" / f"{sid}.jsonl").write_text('{"x": 1}\n')
    return home


def test_live_records_keep_only_live_pids(tmp_path):
    home = _home(tmp_path)
    found = list(live_records(home, is_alive=lambda pid: pid == 111))
    assert [r.record.name for r in found] == ["111.json"]
    assert found[0].transcript == home / "projects" / "-w" / "live-a.jsonl"


@pytest.mark.skipif(shutil.which("rsync") is None, reason="needs rsync (not on Windows runners)")
def test_push_mirrors_live_sessions_and_drops_ended_ones(tmp_path):
    """Runs the real rsync, with a local directory standing in for the hub."""
    home = _home(tmp_path)
    hub = tmp_path / "hub" / "mac"
    (hub / "sessions").mkdir(parents=True)
    (hub / "sessions" / "999.json").write_text("{}")  # a session that has since ended
    records = list(live_records(home, is_alive=lambda pid: pid == 111))
    cmds = push_commands(records, home=home, target="ignored", dest=str(hub), staging=tmp_path / "stage")
    for argv in cmds:
        if argv[0] == "ssh":
            continue  # local stand-in: no ssh, and the dirs exist
        argv = [a.replace("ignored:", "") for a in argv if a not in ("-e", "ssh")]
        subprocess.run(argv, check=True)
    assert sorted(p.name for p in (hub / "sessions").iterdir()) == ["111.json"]
    assert (hub / "projects" / "-w" / "live-a.jsonl").read_text() == '{"x": 1}\n'
    assert not (hub / "projects" / "-w" / "dead-b.jsonl").exists()
    assert time.time() - os.stat(hub / MIRROR_STAMP).st_mtime < 60
