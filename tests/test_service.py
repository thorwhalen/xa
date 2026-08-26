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


def test_label_overlay_roundtrips(app_and_stores) -> None:
    """PATCH /sessions/{id}/label writes an event; /sessions reports it."""
    client, events, panes = app_and_stores
    # Emit a transcript so /sessions returns something.
    from xa import archive as arch
    arch.append_created(events, id="abc123", name="oldname", cwd="/tmp", claude_bin="claude")
    arch.append_url_acquired(
        events, id="abc123", name="oldname",
        url="https://claude.ai/code/session_x",
        claude_session_id="deadbeefdeadbeefdeadbeefdeadbeef",
    )
    r = client.patch(
        "/sessions/abc123/label", json={"label": "my-nice-name"}
    )
    assert r.status_code == 200 and r.json()["label"] == "my-nice-name"
    # The next /archive listing should carry the label.
    body = client.get("/archive").json()
    rec = next(s for s in body["sessions"] if s["id"] == "abc123")
    assert rec["label"] == "my-nice-name"


def test_label_rejects_bad_chars(app_and_stores) -> None:
    client, events, panes = app_and_stores
    from xa import archive as arch
    arch.append_created(events, id="abc123", name="x", cwd="/tmp", claude_bin="claude")
    r = client.patch(
        "/sessions/abc123/label", json={"label": "bad name with spaces"}
    )
    assert r.status_code == 400


def test_hide_and_unhide_archive_records(app_and_stores) -> None:
    client, events, panes = app_and_stores
    from xa import archive as arch
    arch.append_created(events, id="abcdef", name="s", cwd="/tmp", claude_bin="claude")
    arch.append_gone(events, id="abcdef", name="s", reason="clean_exit")
    # Hide it.
    r = client.post("/archive/abcdef/hide", json={"hidden": True})
    assert r.status_code == 200 and r.json()["hidden"] is True
    rec = next(s for s in client.get("/archive").json()["sessions"] if s["id"] == "abcdef")
    assert rec["hidden"] is True
    # Unhide via DELETE.
    r = client.delete("/archive/abcdef/hide")
    assert r.status_code == 200 and r.json()["hidden"] is False
    rec = next(s for s in client.get("/archive").json()["sessions"] if s["id"] == "abcdef")
    assert rec["hidden"] is False


def test_fs_default_reports_configured_default(tmp_path: Path) -> None:
    fake_home = tmp_path / ".claude"
    (fake_home / "projects").mkdir(parents=True)
    chosen = tmp_path / "projects_root"
    chosen.mkdir()
    api = svc.build_api(
        events_store=st.JsonLinesStore(tmp_path / "events.jsonl"),
        pane_store=st.FileStore(tmp_path / "panes", suffix=".log"),
        claude_home=fake_home,
        default_folder=chosen,
    )
    client = TestClient(api)
    r = client.get("/fs/default")
    assert r.status_code == 200 and r.json()["path"] == str(chosen)


def test_fs_list_lists_dirs_and_files(tmp_path: Path, app_and_stores) -> None:
    client, *_ = app_and_stores
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("hi")

    r = client.get(f"/fs/list?path={tmp_path}")
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(tmp_path.resolve())
    assert body["parent"] == str(tmp_path.parent)
    names = [e["name"] for e in body["entries"]]
    # Dirs before files, alphabetical within each group; hidden excluded.
    assert names == ["alpha", "beta", "file.txt"]
    assert body["entries"][0]["is_dir"] is True
    assert body["entries"][2]["is_dir"] is False

    # show_hidden=true surfaces dotfiles.
    r = client.get(f"/fs/list?path={tmp_path}&show_hidden=true")
    names = [e["name"] for e in r.json()["entries"]]
    assert ".hidden" in names


def test_fs_list_defaults_to_configured_folder(tmp_path: Path) -> None:
    fake_home = tmp_path / ".claude"
    (fake_home / "projects").mkdir(parents=True)
    default = tmp_path / "default_root"
    default.mkdir()
    (default / "child").mkdir()
    api = svc.build_api(
        events_store=st.JsonLinesStore(tmp_path / "events.jsonl"),
        pane_store=st.FileStore(tmp_path / "panes", suffix=".log"),
        claude_home=fake_home,
        default_folder=default,
    )
    client = TestClient(api)
    r = client.get("/fs/list")
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(default.resolve())
    assert [e["name"] for e in body["entries"]] == ["child"]


def test_fs_list_404_on_missing_path(app_and_stores) -> None:
    client, *_ = app_and_stores
    r = client.get("/fs/list?path=/definitely/not/a/real/path/xa-test")
    assert r.status_code == 404


def test_fs_list_400_on_file_path(tmp_path: Path, app_and_stores) -> None:
    client, *_ = app_and_stores
    f = tmp_path / "plain.txt"
    f.write_text("nope")
    r = client.get(f"/fs/list?path={f}")
    assert r.status_code == 400


def test_webui_served_when_enabled(tmp_path: Path) -> None:
    fake_home = tmp_path / ".claude"
    (fake_home / "projects").mkdir(parents=True)
    api = svc.build_api(
        events_store=st.JsonLinesStore(tmp_path / "events.jsonl"),
        pane_store=st.FileStore(tmp_path / "panes", suffix=".log"),
        claude_home=fake_home,
        include_webui=True,
    )
    client = TestClient(api)
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>xa</title>" in r.text
    assert "/sessions" in r.text     # webui fetches /sessions


def test_webui_not_served_by_default(app_and_stores) -> None:
    client, *_ = app_and_stores
    # Default: no /  route mounted (Starlette returns 404 for unknown static).
    r = client.get("/")
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# resume / info / diagnose — archive-id resolution
# --------------------------------------------------------------------------- #


def _seed_transcript(claude_home: Path, cs_id: str, *, cwd: str = "/tmp") -> Path:
    """Drop a minimal transcript at ~/.claude/projects/<slug>/<cs_id>.jsonl."""
    slug = cwd.replace("/", "-")
    pdir = claude_home / "projects" / slug
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{cs_id}.jsonl"
    path.write_text(
        '{"type":"user","sessionId":"%s","cwd":"%s",'
        '"message":{"role":"user","content":"hello"}}\n' % (cs_id, cwd)
    )
    return path


def test_info_resolves_by_archive_id(tmp_path: Path) -> None:
    """Regression: webui sends 12-char archive id to /info; backend must
    translate it to the matching claude_session_id and return that session.
    Before the fix, /info returned 404 because get_session only matched on
    Session.id (= claude_session_id)."""
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    fake_home = tmp_path / ".claude"
    (fake_home / "projects").mkdir(parents=True)
    cs_id = "deadbeef-dead-beef-dead-beefdeadbeef"
    _seed_transcript(fake_home, cs_id)
    arch.append_created(events, id="abc123ab", name="s", cwd="/tmp", claude_bin="claude")
    arch.append_url_acquired(events, id="abc123ab", name="s", claude_session_id=cs_id)

    api = svc.build_api(events_store=events, pane_store=panes, claude_home=fake_home)
    client = TestClient(api)
    r = client.get("/sessions/abc123ab/info")
    assert r.status_code == 200, r.text
    assert r.json()["claude_session_id"] == cs_id


def test_resume_404_when_archive_id_has_no_claude_session(tmp_path: Path) -> None:
    """An archive entry with no claude_session_id (URL never acquired)
    cannot be resumed — must 404, not 500."""
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    fake_home = tmp_path / ".claude"
    (fake_home / "projects").mkdir(parents=True)
    arch.append_created(events, id="abc123ab", name="s", cwd="/tmp", claude_bin="claude")

    api = svc.build_api(events_store=events, pane_store=panes, claude_home=fake_home)
    client = TestClient(api)
    r = client.post("/sessions/abc123ab/resume", json={})
    assert r.status_code == 404


def test_diagnose_surfaces_oom_signals_and_hint(tmp_path: Path) -> None:
    """End-to-end: pane log with 'Killed' + transcript with exit 137 →
    /diagnose returns oom_signals and an actionable hint."""
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    fake_home = tmp_path / ".claude"
    (fake_home / "projects").mkdir(parents=True)
    cs_id = "feedface-feed-face-feed-facefeedface"
    # Transcript with a tool_use + tool_result(Exit code 137).
    slug_dir = fake_home / "projects" / "-tmp"
    slug_dir.mkdir(parents=True, exist_ok=True)
    (slug_dir / f"{cs_id}.jsonl").write_text(
        '{"type":"assistant","sessionId":"%s","cwd":"/tmp",'
        '"message":{"role":"assistant","content":[{"type":"tool_use",'
        '"name":"Bash","input":{"command":"big"}}]}}\n'
        '{"type":"user","sessionId":"%s","cwd":"/tmp",'
        '"message":{"role":"user","content":[{"type":"tool_result",'
        '"content":"Exit code 137"}]}}\n' % (cs_id, cs_id)
    )
    arch.append_created(events, id="0becdead", name="s", cwd="/tmp", claude_bin="claude",
                        tmux_created_ts=1000)
    arch.append_url_acquired(events, id="0becdead", name="s", claude_session_id=cs_id)
    panes["0becdead"] = b"... output ...\nKilled\n"
    arch.reconcile(events, panes, live_sessions=[], claude_home=fake_home)

    api = svc.build_api(events_store=events, pane_store=panes, claude_home=fake_home)
    client = TestClient(api)
    # Both the archive id and the claude_session_id should resolve.
    for key in ("0becdead", cs_id):
        r = client.get(f"/sessions/{key}/diagnose")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("oom_signals") == ["Killed"], body
        assert body.get("gone_reason") == "oom_killed", body
        assert body.get("hint")
        # The hint should be actionable, not just descriptive.
        assert ("OOM" in body["hint"] or "swap" in body["hint"].lower())


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


# --------------------------------------------------------------------------- #
# session creation (async default / wait=true) + kill resolution + enable-RC
# --------------------------------------------------------------------------- #


def _fake_session(**overrides):
    from xa.sessions import Session

    base = dict(
        id="aaaa1111-2222-3333-4444-555555555555",
        claude_session_id="aaaa1111-2222-3333-4444-555555555555",
        bridge_session_id=None,
        host="local",
        cwd="/w",
        project_slug="-w",
        state="live",
        live_pid=None,
        tmux_name="txa",
        name=None,
        summary=None,
        first_user_message=None,
        turn_count=1,
        forked_from=None,
        created=None,
        modified=None,
        url=None,
        url_source=None,
        transcript_path=None,
    )
    base.update(overrides)
    return Session(**base)


def test_create_async_returns_starting_immediately(
    app_and_stores, monkeypatch, tmp_path: Path
) -> None:
    import threading

    client, *_ = app_and_stores
    seen = {}
    completed = threading.Event()

    class FakePending:
        warning = None

    def fake_prepare(name, **kw):
        seen["name"] = name
        seen["kw"] = kw
        return FakePending()

    monkeypatch.setattr(svc.ccli, "prepare_spawn", fake_prepare)
    monkeypatch.setattr(
        svc.ccli, "complete_spawn", lambda pending: completed.set()
    )
    r = client.post(
        "/sessions", json={"name": "s1", "cwd": str(tmp_path), "wait": False}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "starting"
    assert body["name"] == "s1" and body["url"] is None
    # url-or-warning invariant: no-url responses always carry a warning.
    assert body["warning"]
    # The slow half runs in a background thread.
    assert completed.wait(5)
    # The chosen name is forwarded as the claude display name too.
    assert seen["kw"]["claude_name"] == "s1"


def test_create_wait_true_keeps_sync_contract(
    app_and_stores, monkeypatch, tmp_path: Path
) -> None:
    from xa import claude_cli as ccli_mod

    client, *_ = app_and_stores
    fake_result = ccli_mod.SpawnResult(
        name="s2",
        cwd=str(tmp_path),
        claude_pid=1,
        claude_session_id="cs-id",
        bridge_session_id="session_x",
        url="https://claude.ai/code/session_x",
        url_source="session_file",
        warning=None,
    )
    monkeypatch.setattr(svc.ccli, "prepare_spawn", lambda name, **kw: object())
    monkeypatch.setattr(svc.ccli, "complete_spawn", lambda pending: fake_result)
    # No `wait` field: the default preserves the original synchronous
    # wire contract (async is opt-in via wait:false).
    r = client.post("/sessions", json={"name": "s2", "cwd": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete"
    assert body["url"] == "https://claude.ai/code/session_x"


def test_create_rejects_shell_hostile_model_value(
    app_and_stores, tmp_path: Path
) -> None:
    client, *_ = app_and_stores
    r = client.post(
        "/sessions",
        json={"name": "s3", "cwd": str(tmp_path), "model": "bad value; rm"},
    )
    assert r.status_code == 400
    assert "model" in r.json()["detail"]


def test_delete_resolves_claude_session_id(app_and_stores, monkeypatch) -> None:
    """DELETE with a claude session id kills the backing tmux session —
    but only when that tmux session is dedicated to this claude."""
    client, *_ = app_and_stores
    s = _fake_session(live_pid=1234)
    monkeypatch.setattr(
        svc.tm,
        "list_sessions",
        lambda binary=None: [
            svc.tm.TmuxSession(name="txa", created=0, activity=0, attached=False)
        ],
    )
    killed: list = []
    monkeypatch.setattr(
        svc.tm, "kill_session", lambda n, binary=None: killed.append(n)
    )
    monkeypatch.setattr(svc.sess, "get_session", lambda i, **kw: s)
    monkeypatch.setattr(
        svc.ccli, "tmux_session_dedicated_to", lambda name, pid, **kw: True
    )
    r = client.delete("/sessions/aaaa1111-2222-3333-4444-555555555555")
    assert r.status_code == 200
    assert killed == ["txa"]


def test_delete_shared_workspace_signals_only_the_pid(
    app_and_stores, monkeypatch
) -> None:
    """A claude in one window of a multi-pane tmux workspace must NOT take
    the whole workspace down — only its own (identity-verified) pid."""
    client, *_ = app_and_stores
    s = _fake_session(live_pid=1234)
    monkeypatch.setattr(
        svc.tm,
        "list_sessions",
        lambda binary=None: [
            svc.tm.TmuxSession(name="txa", created=0, activity=0, attached=False)
        ],
    )
    tmux_killed: list = []
    monkeypatch.setattr(
        svc.tm, "kill_session", lambda n, binary=None: tmux_killed.append(n)
    )
    monkeypatch.setattr(svc.sess, "get_session", lambda i, **kw: s)
    monkeypatch.setattr(
        svc.ccli, "tmux_session_dedicated_to", lambda name, pid, **kw: False
    )
    monkeypatch.setattr(svc.ccli, "pid_is_claude", lambda pid: True)
    sig_killed: list = []
    monkeypatch.setattr(svc.os, "kill", lambda pid, sig: sig_killed.append(pid))
    r = client.delete("/sessions/aaaa1111-2222-3333-4444-555555555555")
    assert r.status_code == 200
    assert tmux_killed == [] and sig_killed == [1234]


def test_delete_never_signals_an_unverified_pid(
    app_and_stores, monkeypatch
) -> None:
    """If the pid fails the claude-identity check (stale file, recycled
    pid), no signal is sent — 404 instead of killing a bystander."""
    client, *_ = app_and_stores
    s = _fake_session(live_pid=1234)
    monkeypatch.setattr(svc.tm, "list_sessions", lambda binary=None: [])
    monkeypatch.setattr(svc.sess, "get_session", lambda i, **kw: s)
    monkeypatch.setattr(svc.ccli, "pid_is_claude", lambda pid: False)
    sig_killed: list = []
    monkeypatch.setattr(svc.os, "kill", lambda pid, sig: sig_killed.append(pid))
    r = client.delete("/sessions/aaaa1111-2222-3333-4444-555555555555")
    assert r.status_code == 404
    assert sig_killed == []


def test_delete_dead_session_explains_itself(app_and_stores, monkeypatch) -> None:
    """A session that resolved but has no living backing → 404 with a
    human explanation, not a parroted id."""
    client, *_ = app_and_stores
    monkeypatch.setattr(svc.tm, "list_sessions", lambda binary=None: [])
    monkeypatch.setattr(svc.sess, "get_session", lambda i, **kw: None)
    r = client.delete("/sessions/aaaa1111-2222-3333-4444-555555555555")
    assert r.status_code == 404
    assert "refresh" in r.json()["detail"]


def test_enable_remote_control_returns_url(app_and_stores, monkeypatch) -> None:
    client, *_ = app_and_stores
    s = _fake_session()
    monkeypatch.setattr(svc.sess, "get_session", lambda i, **kw: s)
    monkeypatch.setattr(
        svc.ccli,
        "request_remote_control",
        lambda name, **kw: ("https://claude.ai/code/session_y", "session_file", None),
    )
    r = client.post(f"/sessions/{s.id}/remote-control")
    assert r.status_code == 200
    body = r.json()
    assert body["url"] == "https://claude.ai/code/session_y"
    assert body["attention"] is None


def test_enable_remote_control_surfaces_login_required(
    app_and_stores, monkeypatch
) -> None:
    client, *_ = app_and_stores
    s = _fake_session()
    monkeypatch.setattr(svc.sess, "get_session", lambda i, **kw: s)
    monkeypatch.setattr(
        svc.ccli,
        "request_remote_control",
        lambda name, **kw: (None, None, "login_required"),
    )
    r = client.post(f"/sessions/{s.id}/remote-control")
    assert r.status_code == 200
    body = r.json()
    assert body["attention"] == "login_required"
    assert "/login" in body["hint"]


def test_enable_remote_control_needs_tmux(app_and_stores, monkeypatch) -> None:
    client, *_ = app_and_stores
    s = _fake_session(tmux_name=None)
    monkeypatch.setattr(svc.sess, "get_session", lambda i, **kw: s)
    r = client.post(f"/sessions/{s.id}/remote-control")
    assert r.status_code == 409
