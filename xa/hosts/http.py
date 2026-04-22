"""HTTP host — a remote ``xa serve`` instance.

The remote must be running ``xa serve`` (or an API with the same
``/sessions`` + ``/archive`` surface) and be reachable over HTTPS.
All discovery and actions are performed through the API; no SSH keys,
no rsync. Auth is pluggable:

- ``auth="basic"`` → HTTP Basic with ``username`` / ``password``
- ``auth="bearer"`` → ``Authorization: Bearer <token>``
- ``auth=None``   → no auth (host is already gated upstream)

``urllib`` is used for transport so there's no hard dep on ``httpx``
or ``requests``; ``xa.hosts.http`` works in any Python 3.10+ install.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Iterator, Literal, Optional
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from xa.claude_cli import SpawnResult


AuthKind = Literal["basic", "bearer", None]


class HTTPHost:
    """Client for a remote ``xa serve`` instance."""

    kind = "http"

    def __init__(
        self,
        name: str,
        *,
        base_url: str,
        auth: AuthKind = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.username = username
        self.password = password
        self.token = token
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # transport
    # ------------------------------------------------------------------ #

    def _auth_header(self) -> dict[str, str]:
        if self.auth == "basic" and self.username and self.password:
            pair = f"{self.username}:{self.password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(pair).decode()}
        if self.auth == "bearer" and self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _request(self, method: str, path: str, *, body: Optional[dict] = None) -> Any:
        url = self.base_url + path
        data: Optional[bytes] = None
        headers = {"Accept": "application/json", **self._auth_header()}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urlrequest.Request(url, data=data, method=method, headers=headers)
        try:
            with urlrequest.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if "application/json" in ctype:
                    return json.loads(raw) if raw else None
                return raw.decode("utf-8", errors="replace")
        except HTTPError as e:
            raise RuntimeError(
                f"{method} {path} → HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
            )
        except URLError as e:
            raise RuntimeError(f"{method} {path} → network error: {e.reason}")

    # ------------------------------------------------------------------ #
    # discovery
    # ------------------------------------------------------------------ #

    def iter_sessions(
        self,
        *,
        project_slug: Optional[str] = None,
        include_live: bool = True,
    ) -> Iterator["Session"]:  # noqa: F821
        from xa.sessions import Session

        # Host-side project filtering matches project_slug against cwd
        # substring; the server's list supports a ``project=`` query.
        qs = []
        if project_slug:
            # The remote API filters by cwd substring, so a slug works as a
            # substring too after the slug → cwd transform.
            from xa.claude_fs import parse_project_slug

            qs.append(f"project={parse_project_slug(project_slug)}")
        qs.append(f"limit=0")  # all rows
        query = ("?" + "&".join(qs)) if qs else ""
        body = self._request("GET", "/sessions" + query)
        for row in body.get("sessions", []):
            # Re-hydrate a Session. The remote includes every field the
            # dataclass expects; we pass through unchanged. ``transcript_path``
            # is string-encoded by the server — we leave it as a string for
            # the HTTP-remote case since the caller shouldn't open it.
            try:
                # Drop the server-side ``transcript_path`` string so we don't
                # confuse downstream code that expects a Path or None.
                row = dict(row)
                row.pop("transcript_path", None)
                yield Session(
                    id=row.get("id"),
                    claude_session_id=row.get("claude_session_id"),
                    bridge_session_id=row.get("bridge_session_id"),
                    host=self.name,  # override server's host name with our local name
                    cwd=row.get("cwd"),
                    project_slug=row.get("project_slug", ""),
                    state=row.get("state", "transcript_only"),
                    live_pid=row.get("live_pid"),
                    tmux_name=row.get("tmux_name"),
                    name=row.get("name"),
                    summary=row.get("summary"),
                    first_user_message=row.get("first_user_message"),
                    turn_count=row.get("turn_count", 0),
                    forked_from=row.get("forked_from"),
                    created=row.get("created"),
                    modified=row.get("modified"),
                    url=row.get("url"),
                    url_source=row.get("url_source"),
                    transcript_path=None,
                )
            except TypeError:
                # Server's Session shape drifted — skip rather than raise.
                continue

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #

    def spawn(self, name: str, *, cwd: str, **opts) -> SpawnResult:
        body = self._request("POST", "/sessions", body={"name": name, "cwd": cwd})
        return SpawnResult(
            name=body.get("name", name),
            cwd=body.get("cwd", cwd),
            claude_pid=None,
            claude_session_id=body.get("claude_session_id"),
            bridge_session_id=None,
            url=body.get("url"),
            url_source=body.get("url_source"),
            warning=body.get("warning"),
        )

    def resume(self, claude_session_id: str, *, cwd: str, **opts) -> SpawnResult:
        # The remote API resumes by the ``id`` field exposed on
        # /sessions. Pass the Claude session UUID directly.
        body = self._request(
            "POST",
            f"/sessions/{claude_session_id}/resume",
            body={"name": opts.get("name")},
        )
        return SpawnResult(
            name=body.get("name", ""),
            cwd=body.get("cwd", cwd),
            claude_pid=None,
            claude_session_id=body.get("claude_session_id"),
            bridge_session_id=None,
            url=body.get("url"),
            url_source=body.get("url_source"),
            warning=body.get("warning"),
        )

    def kill(self, name: str) -> None:
        self._request("DELETE", f"/sessions/{name}", body={})

    def capture_pane(self, name: str, *, lines: int = 200) -> str:
        info = self._request("GET", f"/sessions/{name}/info")
        if isinstance(info, dict):
            return info.get("pane_tail", "")
        return ""

    def sync(self, *, force: bool = False) -> None:
        """HTTP sessions are fetched fresh on each listing — no separate sync."""
