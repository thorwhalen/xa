"""Tests for ``xa.service`` — driven via FastAPI's ``TestClient``.

Auto-skipped if fastapi/httpx aren't installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from xa import archive as arch  # noqa: E402
from xa import service as svc  # noqa: E402
from xa import store as st  # noqa: E402


@pytest.fixture()
def app_and_stores(tmp_path: Path):
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    fake_claude_home = tmp_path / ".claude"
    (fake_claude_home / "projects").mkdir(parents=True)
    api = svc.build_api(
        events_store=events, pane_store=panes, claude_home=fake_claude_home
    )
    return TestClient(api), events, panes


def test_health_open(app_and_stores) -> None:
    client, *_ = app_and_stores
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_sessions_list_empty(app_and_stores) -> None:
    client, *_ = app_and_stores
    r = client.get("/sessions")
    assert r.status_code == 200
    assert r.json()["sessions"] == []


def test_archive_list_populated(app_and_stores) -> None:
    client, events, panes = app_and_stores
    arch.append_created(events, id="abc123", name="s", cwd="/tmp", claude_bin="claude")
    arch.append_url_acquired(events, id="abc123", name="s", url="https://claude.ai/code/session_x")
    r = client.get("/archive")
    assert r.status_code == 200
    body = r.json()
    assert len(body["sessions"]) == 1
    row = body["sessions"][0]
    assert row["id"] == "abc123"
    assert row["url"] == "https://claude.ai/code/session_x"


def test_archive_log_reads_pane(app_and_stores) -> None:
    client, events, panes = app_and_stores
    arch.append_created(events, id="abc123", name="s", cwd="/tmp", claude_bin="claude")
    panes["abc123"] = b"some pane bytes"
    r = client.get("/archive/abc123/log")
    assert r.status_code == 200
    assert r.text == "some pane bytes"


def test_archive_log_tail_kb(app_and_stores) -> None:
    client, events, panes = app_and_stores
    arch.append_created(events, id="abcdef", name="s", cwd="/tmp", claude_bin="claude")
    panes["abcdef"] = b"x" * 5000
    r = client.get("/archive/abcdef/log?tail_kb=1")
    assert r.status_code == 200
    assert len(r.text) == 1024


def test_archive_log_rejects_bad_id(app_and_stores) -> None:
    client, *_ = app_and_stores
    # ``NOTHEX`` fails the [0-9a-f]{6,64} regex → 400, not a 404 for missing log.
    r = client.get("/archive/NOTHEX/log")
    assert r.status_code == 400


def test_delete_nonexistent(app_and_stores) -> None:
    client, *_ = app_and_stores
    r = client.request("DELETE", "/sessions/does-not-exist", json={})
    assert r.status_code == 404


def test_basic_auth_enforced(tmp_path: Path) -> None:
    fake_home = tmp_path / ".claude"
    (fake_home / "projects").mkdir(parents=True)
    api = svc.build_api(
        auth=svc.make_basic_auth("u", "p"),
        events_store=st.JsonLinesStore(tmp_path / "events.jsonl"),
        pane_store=st.FileStore(tmp_path / "panes", suffix=".log"),
        claude_home=fake_home,
    )
    client = TestClient(api)
    assert client.get("/sessions").status_code == 401
    assert client.get("/sessions", auth=("u", "p")).status_code == 200
    assert client.get("/sessions", auth=("u", "wrong")).status_code == 401
    # /health stays open.
    assert client.get("/health").status_code == 200


def test_captcha_issue_and_check() -> None:
    c = svc.Captcha(key="testkey")
    token, challenge, ttl = c.issue()
    assert len(challenge) == 4 and ttl > 0
    assert c.check(token, challenge) is True
    assert c.check(token, "WRONG") is False
    assert c.check("garbage", challenge) is False


def test_captcha_endpoint_and_gated_delete(tmp_path: Path) -> None:
    fake_home = tmp_path / ".claude"
    (fake_home / "projects").mkdir(parents=True)
    api = svc.build_api(
        captcha=svc.Captcha(key="k"),
        events_store=st.JsonLinesStore(tmp_path / "events.jsonl"),
        pane_store=st.FileStore(tmp_path / "panes", suffix=".log"),
        claude_home=fake_home,
    )
    client = TestClient(api)
    cap = client.get("/captcha").json()
    # Wrong captcha → 400 (since the tmux session doesn't exist, real flow would
    # reach 404; but a bad captcha short-circuits to 400 before the 404 check).
    r = client.request(
        "DELETE",
        "/sessions/does-not-exist",
        json={"captcha_token": cap["token"], "captcha_answer": "XXXX"},
    )
    assert r.status_code == 400 and "captcha" in r.json()["detail"].lower()
    # Right captcha → hit the 404 branch (session doesn't exist).
    r = client.request(
        "DELETE",
        "/sessions/does-not-exist",
        json={"captcha_token": cap["token"], "captcha_answer": cap["challenge"]},
    )
    assert r.status_code == 404
