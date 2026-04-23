"""FastAPI service for ``xa``.

``build_api(...)`` returns a mountable ``FastAPI`` app that reimplements
the ``edualc`` route surface on top of the ``xa`` primitives. The service
is deliberately a function (not a module that imports-and-runs) so it can
be embedded into larger hosts (enlace / tw_platform) or launched
standalone via ``xa serve``.

FastAPI is an optional dependency. Import ``xa.service`` only when you
have installed ``xa[service]`` (pulls in ``fastapi`` + ``uvicorn``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import random
import re
import secrets
import string
import time
from pathlib import Path
from typing import Any, Callable, Optional

from xa import archive as arch
from xa import claude_cli as ccli
from xa import claude_fs as cfs
from xa import sessions as sess
from xa import store as st
from xa import tmux as tm


try:  # pragma: no cover - optional dep
    from pydantic import BaseModel

    class CreateReq(BaseModel):
        name: Optional[str] = None
        cwd: Optional[str] = None

    class DeleteReq(BaseModel):
        captcha_token: Optional[str] = None
        captcha_answer: Optional[str] = None

    class ResumeReq(BaseModel):
        name: Optional[str] = None

    class LabelReq(BaseModel):
        label: Optional[str] = None  # null or "" clears

    class HideReq(BaseModel):
        hidden: bool = True
except ImportError:  # pragma: no cover
    pass


# --------------------------------------------------------------------------- #
# auth: reference HTTP Basic implementation (swappable)
# --------------------------------------------------------------------------- #


def make_basic_auth(username: str, password: str) -> Callable[..., Any]:
    """Build a FastAPI dependency that enforces HTTP Basic auth.

    Returns a callable suitable for ``Depends(...)``. Uses
    ``secrets.compare_digest`` to avoid timing leaks.
    """
    from fastapi import Depends, HTTPException, status
    from fastapi.security import HTTPBasic, HTTPBasicCredentials

    basic = HTTPBasic(realm="xa")

    def _require_auth(creds: HTTPBasicCredentials = Depends(basic)) -> str:
        user_ok = secrets.compare_digest(creds.username, username)
        pass_ok = secrets.compare_digest(creds.password, password)
        if not (user_ok and pass_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": 'Basic realm="xa"'},
            )
        return creds.username

    return _require_auth


def allow_all() -> str:
    """No-op auth dependency. Useful when the service is behind an external
    auth layer (enlace, reverse-proxy mTLS) that already gated the request.

    FastAPI inspects the signature to decide what to inject; keep this
    nullary so it isn't treated as query-parameter binding.
    """
    return "anonymous"


# --------------------------------------------------------------------------- #
# captcha: stateless HMAC-signed challenge (ported from edualc)
# --------------------------------------------------------------------------- #


class Captcha:
    """Stateless 4-letter captcha with an HMAC-signed token.

    The signing key never leaves the server. Tokens are
    ``b64(CHALLENGE.EXPIRY).SIG`` where SIG = HMAC-SHA256(key, payload).
    Multi-worker safe; no server-side state. Pass one instance of this
    class to :func:`build_api` to enable captcha-gated deletes.
    """

    def __init__(self, *, key: str, ttl_sec: int = 120) -> None:
        self._key = key.encode()
        self._ttl_sec = ttl_sec

    def _sign(self, payload: bytes) -> str:
        sig = hmac.new(self._key, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()

    def issue(self) -> tuple[str, str, int]:
        """Return ``(token, challenge, ttl_sec)``."""
        challenge = "".join(random.choices(string.ascii_uppercase, k=4))
        expiry = int(time.time()) + self._ttl_sec
        payload = f"{challenge}.{expiry}".encode()
        token = (
            base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
            + "."
            + self._sign(payload)
        )
        return token, challenge, self._ttl_sec

    def check(self, token: str, answer: str) -> bool:
        try:
            b64_payload, sig = token.rsplit(".", 1)
            padded = b64_payload + "=" * (-len(b64_payload) % 4)
            payload = base64.urlsafe_b64decode(padded.encode())
            challenge, expiry_s = payload.decode().split(".")
            expiry = int(expiry_s)
        except (ValueError, UnicodeDecodeError):
            return False
        if not hmac.compare_digest(sig, self._sign(payload)):
            return False
        if time.time() > expiry:
            return False
        return secrets.compare_digest(challenge, (answer or "").strip().upper())


# --------------------------------------------------------------------------- #
# build_api
# --------------------------------------------------------------------------- #


_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,48}$")


def build_api(
    *,
    auth: Callable[..., Any] = allow_all,
    events_store: Optional[st.JsonLinesStore] = None,
    pane_store: Optional[st.FileStore] = None,
    captcha: Optional[Captcha] = None,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    claude_bin: str = ccli.DEFAULT_CLAUDE_BIN,
    session_prefix: str = "xa-",
    title: str = "xa",
    version: str = "0.1",
    include_webui: bool = False,
):
    """Return a ``FastAPI`` app exposing ``xa``'s session + archive surface.

    The caller composes this app into whatever process they have: mount
    under ``/api/xa/`` inside enlace, or run standalone via ``uvicorn``.
    """
    from dataclasses import asdict
    from fastapi import Body, Depends, FastAPI, HTTPException
    from fastapi.responses import PlainTextResponse

    # Use ``is None`` — not ``or`` — because an empty ``JsonLinesStore``
    # is falsy (``__len__`` == 0) which would silently substitute the
    # default store and lose the caller's one.
    events = events_store if events_store is not None else st.default_events_store()
    panes = pane_store if pane_store is not None else st.default_pane_store()
    app = FastAPI(title=title, version=version)

    def _session_dict(s: sess.Session, overlay_map: Optional[dict] = None) -> dict:
        d = asdict(s)
        d["transcript_path"] = (
            str(d["transcript_path"]) if d["transcript_path"] else None
        )
        # Apply a label/hidden overlay if present. Lookup order goes from
        # most session-specific to least: archive id (= claude session id
        # for transcript-only sessions) → claude_session_id → tmux_name.
        # tmux names get reused, so they're last to avoid an old session's
        # label leaking onto a new one of the same name.
        ov = {}
        if overlay_map:
            for key in (s.id, s.claude_session_id, s.tmux_name):
                if key and key in overlay_map:
                    ov = overlay_map[key]
                    break
        d["label"] = ov.get("label")
        d["hidden"] = bool(ov.get("hidden", False))
        return d

    def _record_dict(r: arch.ArchiveRecord) -> dict:
        return asdict(r)

    _ARCHIVE_ID_RE = re.compile(r"^[0-9a-f]{6,64}$")

    def _resolve_session(id: str) -> Optional[sess.Session]:
        """Find a Session by tmux name, claude session id (full or prefix),
        or edualc archive id.

        ``sess.get_session`` only knows about claude session ids and tmux
        names; archive ids live in the events store. This helper bridges
        the two so /info, /label and /resume all accept the same id forms
        the webui already shows on its session cards.
        """
        s = sess.get_session(id, claude_home=claude_home)
        if s is not None:
            return s
        if not _ARCHIVE_ID_RE.match(id):
            return None
        # Translate archive id → claude_session_id via the event log.
        cs_id: Optional[str] = None
        for rec in arch.records(events, panes):
            if rec.id == id and rec.claude_session_id:
                cs_id = rec.claude_session_id
                break
        if cs_id is None:
            return None
        return sess.get_session(cs_id, claude_home=claude_home)

    def _generate_name() -> str:
        existing = {t.name for t in tm.list_sessions()}
        for _ in range(50):
            stub = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
            candidate = f"{session_prefix}{stub}"
            if candidate not in existing:
                return candidate
        raise HTTPException(500, "Could not generate a unique session name")

    # --------------------------------------------------------------------- #
    # sessions
    # --------------------------------------------------------------------- #

    @app.get("/sessions")
    def list_sessions(
        _: str = Depends(auth),
        project: Optional[str] = None,
        state: Optional[str] = None,
        include_forks: bool = True,
        limit: int = 100,
    ) -> dict:
        rows = sess.list_sessions(
            project=project,
            state=state,  # type: ignore[arg-type]
            include_forks=include_forks,
            limit=limit or None,
            claude_home=claude_home,
        )
        # Freshen the archive before reporting so dead sessions show up.
        try:
            arch.reconcile(events, panes, tm.list_sessions(), claude_home=claude_home)
        except Exception:
            pass
        overlay_map = arch.overlays(events)
        return {"sessions": [_session_dict(r, overlay_map) for r in rows]}

    @app.post("/sessions")
    def create_session(req: CreateReq, _: str = Depends(auth)) -> dict:
        name = req.name or _generate_name()
        if not _NAME_RE.match(name):
            raise HTTPException(
                400, "Invalid name (allowed: letters, digits, _.-, max 48)"
            )
        existing = {t.name for t in tm.list_sessions()}
        if name in existing:
            raise HTTPException(409, f"Session '{name}' already exists")
        cwd = req.cwd or str(Path.home())
        if not Path(cwd).is_dir():
            raise HTTPException(400, f"cwd does not exist: {cwd}")
        try:
            result = ccli.spawn_session(
                name,
                cwd=cwd,
                claude_bin=claude_bin,
                claude_home=claude_home,
                archive_store=events,
                pane_store=panes,
            )
        except (FileNotFoundError, RuntimeError) as e:
            raise HTTPException(500, str(e))
        return {
            "name": result.name,
            "cwd": result.cwd,
            "url": result.url,
            "url_source": result.url_source,
            "claude_session_id": result.claude_session_id,
            "warning": result.warning,
        }

    @app.delete("/sessions/{name}")
    def delete_session(
        name: str,
        req: DeleteReq = Body(default=DeleteReq()),
        _: str = Depends(auth),
    ) -> dict:
        if not _NAME_RE.match(name):
            raise HTTPException(400, "Invalid session name")
        if captcha is not None:
            if not captcha.check(req.captcha_token or "", req.captcha_answer or ""):
                raise HTTPException(
                    400, "Captcha failed — request a new one and try again"
                )
        existing = {t.name for t in tm.list_sessions()}
        if name not in existing:
            raise HTTPException(404, f"No such session: {name}")
        try:
            tm.kill_session(name)
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        return {"killed": name}

    @app.get("/sessions/{id}/info")
    def session_info(id: str, _: str = Depends(auth)) -> dict:
        try:
            s = _resolve_session(id)
        except LookupError as e:
            raise HTTPException(400, str(e))
        if s is None:
            raise HTTPException(404, f"No session matching '{id}'")
        out = _session_dict(s)
        if s.transcript_path:
            out["forensics"] = asdict(cfs.transcript_forensics(s.transcript_path))
            out["forensics"]["transcript_path"] = (
                str(out["forensics"]["transcript_path"])
                if out["forensics"]["transcript_path"]
                else None
            )
        if s.state == "live" and s.tmux_name:
            out["pane_tail"] = tm.capture_pane(s.tmux_name, lines=80)
        return out

    @app.get("/sessions/{id}/diagnose")
    def diagnose(id: str, _: str = Depends(auth), tail_kb: int = 16) -> dict:
        """Single-stop "what happened" for a session.

        Accepts the same id forms as /info: tmux name, claude session id
        (full or unique prefix), or archive id. Combines the existing
        forensics, pane tail, and a synthesized human-readable hint. The
        hint draws on the same signals ``archive.classify_death`` uses,
        so it stays consistent with the death reason shown elsewhere.
        """
        try:
            s = _resolve_session(id)
        except LookupError as e:
            raise HTTPException(400, str(e))
        if s is None:
            raise HTTPException(404, f"No session matching '{id}'")

        out: dict = {"session": _session_dict(s)}

        # Transcript forensics — works for live and archived sessions both.
        forensics_obj: Optional[cfs.TranscriptForensics] = None
        if s.transcript_path:
            try:
                forensics_obj = cfs.transcript_forensics(s.transcript_path)
            except OSError:
                forensics_obj = None
            if forensics_obj is not None:
                fdict = asdict(forensics_obj)
                fdict["transcript_path"] = (
                    str(fdict["transcript_path"]) if fdict["transcript_path"] else None
                )
                out["forensics"] = fdict

        # Locate the matching archive record (lookup by claude_session_id
        # via the records table — that's the join key the events store
        # already exposes).
        archive_rec: Optional[arch.ArchiveRecord] = None
        if s.claude_session_id:
            for rec in arch.records(events, panes):
                if rec.claude_session_id == s.claude_session_id:
                    archive_rec = rec
                    break

        # Pane tail. Live: capture from tmux. Archived: read from pane store.
        oom_markers: tuple[str, ...] = ()
        pane_tail: Optional[str] = None
        if s.state == "live" and s.tmux_name:
            pane_tail = tm.capture_pane(s.tmux_name, lines=80)
        elif archive_rec is not None and archive_rec.id in panes:
            cap = max(1024, tail_kb * 1024)
            try:
                pane_tail = panes[archive_rec.id][-cap:].decode(
                    "utf-8", errors="replace"
                )
            except KeyError:
                pane_tail = None
        if pane_tail:
            out["pane_tail"] = pane_tail
            oom_markers = tuple(m for m in arch._OOM_PANE_MARKERS if m in pane_tail)
            if oom_markers:
                out["oom_signals"] = list(oom_markers)

        if archive_rec is not None:
            out["archive_id"] = archive_rec.id
            out["gone_reason"] = archive_rec.gone_reason
            out["gone"] = archive_rec.gone

        # Synthesize the hint — consistent with classify_death's verdict.
        out["hint"] = arch.synthesize_diagnosis(
            state=s.state,
            reason=archive_rec.gone_reason if archive_rec else None,
            forensics=forensics_obj,
            oom_markers=oom_markers,
        )
        return out

    @app.post("/sessions/{id}/resume")
    def resume(id: str, req: ResumeReq, _: str = Depends(auth)) -> dict:
        try:
            s = _resolve_session(id)
        except LookupError as e:
            raise HTTPException(400, str(e))
        if s is None or not s.claude_session_id:
            raise HTTPException(404, f"No resumable session matching '{id}'")
        try:
            result = sess.resume(
                s,
                name=req.name,
                claude_bin=claude_bin,
                claude_home=claude_home,
            )
        except (ValueError, FileNotFoundError, RuntimeError) as e:
            raise HTTPException(500, str(e))
        return {
            "name": result.name,
            "cwd": result.cwd,
            "url": result.url,
            "url_source": result.url_source,
            "claude_session_id": result.claude_session_id,
            "warning": result.warning,
        }

    @app.patch("/sessions/{id}/label")
    def set_label(
        id: str,
        req: LabelReq = Body(default=LabelReq()),
        _: str = Depends(auth),
    ) -> dict:
        """Set (or clear) a display label for a session.

        Accepts any of: tmux session name, archive id, claude session id.
        For a live session, also renames the tmux session so lookups by
        the new name work natively. For archived / transcript-only
        sessions, the label is kept as an overlay only.
        """
        label = (req.label or "").strip()
        if label and not _NAME_RE.match(label):
            raise HTTPException(400, "Label must match [A-Za-z0-9_.-]{1,48}")

        # Try to find a matching Session (live or transcript-only). If
        # none, we still accept the request and record the overlay —
        # archive-only records are identified by their hex id.
        try:
            s = _resolve_session(id)
        except LookupError as e:
            raise HTTPException(400, str(e))

        keys_to_label: list[str] = [id]
        if s is not None:
            if s.state == "live" and s.tmux_name and label:
                try:
                    tm.rename_session(s.tmux_name, label)
                except RuntimeError as e:
                    raise HTTPException(500, f"tmux rename failed: {e}")
            if s.id not in keys_to_label:
                keys_to_label.append(s.id)
            if s.claude_session_id and s.claude_session_id not in keys_to_label:
                keys_to_label.append(s.claude_session_id)
            if s.tmux_name and s.tmux_name not in keys_to_label:
                keys_to_label.append(s.tmux_name)

        for key in keys_to_label:
            arch.append_label(events, id=key, label=label or None)
        return {"id": id, "label": label or None}

    @app.post("/archive/{archive_id}/hide")
    def hide(
        archive_id: str,
        req: HideReq = Body(default=HideReq()),
        _: str = Depends(auth),
    ) -> dict:
        if not re.fullmatch(r"[0-9a-f]{6,64}", archive_id):
            raise HTTPException(400, "Invalid archive id")
        arch.append_hidden(events, id=archive_id, hidden=req.hidden)
        return {"id": archive_id, "hidden": req.hidden}

    @app.delete("/archive/{archive_id}/hide")
    def unhide(archive_id: str, _: str = Depends(auth)) -> dict:
        if not re.fullmatch(r"[0-9a-f]{6,64}", archive_id):
            raise HTTPException(400, "Invalid archive id")
        arch.append_hidden(events, id=archive_id, hidden=False)
        return {"id": archive_id, "hidden": False}

    # --------------------------------------------------------------------- #
    # archive
    # --------------------------------------------------------------------- #

    @app.get("/archive")
    def archive_list(_: str = Depends(auth), limit: int = 100) -> dict:
        try:
            arch.reconcile(events, panes, tm.list_sessions(), claude_home=claude_home)
        except Exception:
            pass
        recs = arch.records(events, panes)
        if limit:
            recs = recs[:limit]
        return {"sessions": [_record_dict(r) for r in recs]}

    @app.get("/archive/{archive_id}/forensics")
    def archive_forensics(archive_id: str, _: str = Depends(auth)) -> dict:
        if not re.fullmatch(r"[0-9a-f]{6,64}", archive_id):
            raise HTTPException(400, "Invalid archive id")
        rec = next((r for r in arch.records(events, panes) if r.id == archive_id), None)
        if rec is None:
            raise HTTPException(404, "No such archived session")
        out = _record_dict(rec)
        if rec.cwd and rec.claude_session_id:
            path = cfs.transcript_path(
                rec.cwd, rec.claude_session_id, claude_home=claude_home
            )
            if path is not None:
                out["transcript_forensics"] = asdict(cfs.transcript_forensics(path))
                out["transcript_forensics"]["transcript_path"] = (
                    str(out["transcript_forensics"]["transcript_path"])
                    if out["transcript_forensics"]["transcript_path"]
                    else None
                )
        return out

    @app.get("/archive/{archive_id}/log")
    def archive_log(archive_id: str, _: str = Depends(auth), tail_kb: int = 64):
        if not re.fullmatch(r"[0-9a-f]{6,64}", archive_id):
            raise HTTPException(400, "Invalid archive id")
        if archive_id not in panes:
            raise HTTPException(404, "No pane log for that session")
        data = panes[archive_id]
        if tail_kb and len(data) > tail_kb * 1024:
            data = data[-tail_kb * 1024 :]
        return PlainTextResponse(
            data.decode("utf-8", errors="replace"),
            media_type="text/plain; charset=utf-8",
        )

    # --------------------------------------------------------------------- #
    # captcha + health
    # --------------------------------------------------------------------- #

    if captcha is not None:

        @app.get("/captcha")
        def _captcha(_: str = Depends(auth)) -> dict:
            token, challenge, ttl = captcha.issue()
            return {"token": token, "challenge": challenge, "ttl_sec": ttl}

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    # --------------------------------------------------------------------- #
    # optional webui
    # --------------------------------------------------------------------- #
    #
    # The bundled static UI lives at ``xa/webui/`` and is served at the
    # mount root when ``include_webui=True``. It talks to the API on the
    # same origin — works under ``xa serve`` standalone and under any
    # reverse-proxy mount that includes the same prefix for both.
    if include_webui:
        from fastapi.staticfiles import StaticFiles

        webui_root = Path(__file__).parent / "webui"
        if webui_root.is_dir():
            # `html=True` makes `/` serve `index.html` naturally.
            app.mount(
                "/", StaticFiles(directory=str(webui_root), html=True), name="webui"
            )

    return app
