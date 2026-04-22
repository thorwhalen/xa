"""End-to-end tests for ``xa.hosts.HTTPHost``.

We spin up a real :mod:`xa.service` FastAPI app in-process (serving a
fixture ``~/.claude/``) and point an ``HTTPHost`` at it. This gives us
genuine round-trip coverage of the HTTP protocol without needing an
external server.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

import uvicorn  # noqa: E402

from xa import service as svc  # noqa: E402
from xa import store as st  # noqa: E402
from xa.hosts import HTTPHost  # noqa: E402


SID = "ccccdddd-1111-2222-3333-444444444444"


def _find_free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed_fixture_home(home: Path) -> None:
    pdir = home / "projects" / "-foo-bar"
    pdir.mkdir(parents=True)
    with (pdir / f"{SID}.jsonl").open("w") as f:
        f.write(json.dumps({
            "type": "user", "sessionId": SID, "cwd": "/foo/bar",
            "message": {"role": "user", "content": [{"type": "text", "text": "hi from API"}]},
        }) + "\n")
        f.write(json.dumps({
            "type": "assistant", "sessionId": SID, "cwd": "/foo/bar",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "hi back"}]},
        }) + "\n")


@pytest.fixture()
def live_server(tmp_path: Path) -> Iterator[str]:
    """Spin up uvicorn on a random port, yield the base_url, tear down."""
    home = tmp_path / ".claude"
    _seed_fixture_home(home)
    events = st.JsonLinesStore(tmp_path / "events.jsonl")
    panes = st.FileStore(tmp_path / "panes", suffix=".log")
    api = svc.build_api(
        events_store=events, pane_store=panes, claude_home=home
    )
    port = _find_free_port()
    config = uvicorn.Config(api, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait up to ~3s for the server to start responding.
    import time as _time
    import urllib.request as _ur
    deadline = _time.time() + 3.0
    while _time.time() < deadline:
        try:
            _ur.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5)
            break
        except Exception:
            _time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=3.0)


def test_http_host_lists_remote_sessions(live_server: str) -> None:
    h = HTTPHost(name="remote", base_url=live_server)
    rows = list(h.iter_sessions(include_live=False))
    assert len(rows) == 1
    s = rows[0]
    assert s.id == SID
    # Host name should be the *local* HTTPHost label, not the server's label.
    assert s.host == "remote"
    assert s.cwd == "/foo/bar"


def test_http_host_transcript_path_is_stringified(live_server: str) -> None:
    """Remote transcript paths are meaningless locally — must be None."""
    h = HTTPHost(name="remote", base_url=live_server)
    rows = list(h.iter_sessions(include_live=False))
    assert rows[0].transcript_path is None


def test_http_host_auth_header_basic() -> None:
    h = HTTPHost(
        name="x", base_url="https://x.invalid",
        auth="basic", username="u", password="p",
    )
    header = h._auth_header()
    assert header["Authorization"].startswith("Basic ")
    # base64('u:p') = 'dTpw'
    assert header["Authorization"] == "Basic dTpw"


def test_http_host_auth_header_bearer() -> None:
    h = HTTPHost(name="x", base_url="https://x.invalid", auth="bearer", token="tok")
    assert h._auth_header() == {"Authorization": "Bearer tok"}


def test_http_host_no_auth_header_when_none() -> None:
    h = HTTPHost(name="x", base_url="https://x.invalid")
    assert h._auth_header() == {}


def test_http_host_raises_on_bad_endpoint(live_server: str) -> None:
    h = HTTPHost(name="remote", base_url=live_server)
    with pytest.raises(RuntimeError) as e:
        h._request("GET", "/does-not-exist")
    assert "HTTP 404" in str(e.value)
