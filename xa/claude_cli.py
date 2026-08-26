"""Spawn, resume, and URL-resolve Claude Code sessions.

Ties ``xa.tmux`` (pane / process control), ``xa.claude_fs`` (read the
ephemeral ``~/.claude/sessions/<pid>.json`` file) and the ``claude`` CLI
binary together. Everything here targets the *local* machine; remote-host
dispatch lives in ``xa.hosts`` (Phase 6+).

Bridge URL format: ``https://claude.ai/code/<bridgeSessionId>``. The
``bridgeSessionId`` already starts with ``session_`` — do not prepend
anything.
"""

from __future__ import annotations

import re
import secrets
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from xa import claude_fs as cfs
from xa import tmux as tm


DEFAULT_CLAUDE_BIN = "claude"
CLAUDE_WEB_BASE = "https://claude.ai/code"

_URL_RE = re.compile(r"https://claude\.ai/code/session_[A-Za-z0-9_-]+")

UrlSource = Literal["session_file", "pane_capture"]


@dataclass(frozen=True)
class SpawnResult:
    """What ``spawn_session`` / ``resume_session`` return to the caller."""

    name: str
    cwd: str
    claude_pid: Optional[int]
    claude_session_id: Optional[str]
    bridge_session_id: Optional[str]
    url: Optional[str]
    url_source: Optional[UrlSource]
    warning: Optional[str]
    # Adverse TUI state detected during the handshake (see
    # ``classify_pane_attention``); ``None`` when nothing was wrong.
    attention: Optional[str] = None


# --------------------------------------------------------------------------- #
# claude CLI capability detection
# --------------------------------------------------------------------------- #


# Successful --help probes are cached per binary with a TTL: claude
# self-updates while long-lived servers run, so "forever" is wrong, and
# transient probe failures (binary mid-update, box under load) must NOT
# be latched — a failure is simply retried on the next call.
_FLAGS_TTL_SEC = 900.0
_flags_cache: dict[str, tuple[float, frozenset[str]]] = {}


def supported_cli_flags(
    claude_bin: str = DEFAULT_CLAUDE_BIN, *, ttl_sec: float = _FLAGS_TTL_SEC
) -> frozenset[str]:
    """Long-form flags advertised by ``<claude_bin> --help``, cached per binary.

    Lets spawn compose modern per-session flags (``--remote-control``,
    ``--name``, ``--model``, ``--effort``) while degrading gracefully on
    older installs. Empty set when the binary can't be executed — callers
    then omit every optional flag, matching pre-flag behavior. Only
    successful probes are cached (for ``ttl_sec``); failures are retried.
    """
    cached = _flags_cache.get(claude_bin)
    if cached is not None and time.time() - cached[0] < ttl_sec:
        return cached[1]
    try:
        out = subprocess.run(
            [claude_bin, "--help"],
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return frozenset()
    flags = frozenset(re.findall(r"--[a-z][a-z0-9-]*", out.stdout + out.stderr))
    _flags_cache[claude_bin] = (time.time(), flags)
    return flags


def _claude_argv(
    claude_bin: str,
    *,
    supported: frozenset[str],
    resume_id: Optional[str] = None,
    claude_name: Optional[str] = None,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    remote_control: bool = False,
) -> tuple[list[str], list[str]]:
    """Compose the claude invocation from what the binary supports.

    Returns ``(argv, skipped)`` where ``skipped`` lists requested flags the
    binary does not advertise. ``--remote-control`` goes last and bare so
    its optional value can never swallow a following argument.
    """
    argv = [claude_bin]
    skipped: list[str] = []
    if resume_id:
        argv += ["--resume", resume_id]
    for flag, value in (
        ("--name", claude_name),
        ("--model", model),
        ("--effort", effort),
    ):
        if value is None:
            continue
        if flag in supported:
            argv += [flag, value]
        else:
            skipped.append(flag)
    if remote_control:
        if "--remote-control" in supported:
            argv.append("--remote-control")
        else:
            skipped.append("--remote-control")
    return argv, skipped


# --------------------------------------------------------------------------- #
# pane-state classification (attention states)
# --------------------------------------------------------------------------- #


ATTENTION_LOGIN_REQUIRED = "login_required"
ATTENTION_TRUST_PROMPT = "trust_prompt"

# Case-insensitive substrings. "unknown command: /remote-control" is what
# the TUI answers when the command isn't registered — overwhelmingly an
# expired login (the command only exists once authenticated); on very old
# claude versions it can also mean remote control doesn't exist at all.
_LOGIN_PANE_MARKERS = (
    "run /login",
    "please log in",
    "select login method",
    "oauth token has expired",
    "invalid api key",
    "unknown command: /remote-control",
)
_TRUST_PANE_MARKERS = ("trust this folder", "do you trust the files")


def classify_pane_attention(pane_text: str) -> Optional[str]:
    """Classify adverse TUI states from a pane capture.

    Only meaningful for sessions with no established bridge URL — a
    healthy remote-controlled session's pane renders *conversation* text,
    which may legitimately mention ``/login``. Callers must gate on
    "bridgeless" before trusting a verdict.
    """
    low = pane_text.lower()
    if any(m in low for m in _LOGIN_PANE_MARKERS):
        return ATTENTION_LOGIN_REQUIRED
    if any(m in low for m in _TRUST_PANE_MARKERS):
        return ATTENTION_TRUST_PROMPT
    return None


def attention_hint(
    attention: Optional[str], *, tmux_name: Optional[str] = None
) -> Optional[str]:
    """Human fix-it hint for an attention state (SSOT for CLI + web UI)."""
    if attention is None:
        return None
    attach = (
        f"tmux attach -t {tmux_name}"
        if tmux_name
        else "attach the terminal running claude"
    )
    if attention == ATTENTION_LOGIN_REQUIRED:
        return (
            f"Claude on this machine needs to log in again. Open its terminal "
            f"({attach}), run /login and complete the flow, then /remote-control "
            f"if no URL appears. Login state is per-machine, so one login fixes "
            f"every session on this host. (If /remote-control reports 'Unknown "
            f"command' even after login, the installed claude is too old — "
            f"update it.)"
        )
    if attention == ATTENTION_TRUST_PROMPT:
        return (
            f"Claude is waiting on its workspace-trust prompt. Open its "
            f"terminal ({attach}) and answer it."
        )
    return None


# --------------------------------------------------------------------------- #
# URL resolution
# --------------------------------------------------------------------------- #


def find_claude_pid(
    session_name: str,
    *,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
) -> Optional[int]:
    """Return the PID of the claude process living in the tmux session.

    Walks **every** pane's process tree (a claude can live in any window
    of a multi-window session, not just the first pane). Recognizes both
    the native binary (``comm == "claude"``) and npm/bun installs.
    """
    for root in tm.pane_pids(session_name, binary=tmux_bin):
        for pid in (root, *tm.descendants(root)):
            if cfs._looks_like_claude(pid):
                return pid
    return None


def pid_is_claude(pid: int) -> bool:
    """Portable positive identity check — used before ever signaling a pid.

    Liveness (:func:`xa.claude_fs.ephemeral_session_alive`) proves a
    process exists; this proves it is actually a claude, so a stale
    ephemeral file whose pid got recycled can never direct a kill at an
    unrelated process. Uses ``/proc`` where available, ``ps`` otherwise.
    """
    if cfs._PROC_ROOT.is_dir():
        return cfs._looks_like_claude(pid)
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm=,command="],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    return any(t.rsplit("/", 1)[-1] == "claude" for t in out.stdout.split())


def tmux_session_dedicated_to(
    session_name: str,
    claude_pid: Optional[int],
    *,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
) -> bool:
    """Is killing this whole tmux session equivalent to killing this claude?

    True iff the session consists of a single pane whose claude is
    ``claude_pid``. xa-spawned sessions always qualify; a claude living in
    one window of a user's multi-window workspace never does — killing
    the session there would destroy unrelated panes, so callers must fall
    back to signaling the pid instead.
    """
    if claude_pid is None:
        return False
    if len(tm.pane_pids(session_name, binary=tmux_bin)) != 1:
        return False
    return find_claude_pid(session_name, tmux_bin=tmux_bin) == claude_pid


def _extract_url_from_text(text: str) -> Optional[str]:
    m = _URL_RE.search(text)
    return m.group(0) if m else None


def resolve_bridge_url(
    session_name: str,
    *,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
    scrape_lines: int = 400,
) -> tuple[Optional[str], Optional[UrlSource]]:
    """Return ``(url, source)`` for a live tmux-hosted claude session.

    Primary path: find the claude descendant pid → read
    ``~/.claude/sessions/<pid>.json`` → take ``bridgeSessionId``.
    Fallback: regex-scrape ``capture-pane`` output for the full URL.
    """
    pid = find_claude_pid(session_name, tmux_bin=tmux_bin)
    if pid is not None:
        data = cfs.read_ephemeral_session(pid, claude_home=claude_home)
        if data and (bridge := data.get("bridgeSessionId")):
            return f"{CLAUDE_WEB_BASE}/{bridge}", "session_file"
    pane = tm.capture_pane(session_name, lines=scrape_lines, binary=tmux_bin)
    url = _extract_url_from_text(pane)
    if url:
        return url, "pane_capture"
    return None, None


# --------------------------------------------------------------------------- #
# readiness handshake
# --------------------------------------------------------------------------- #


def _wait_for_url(
    session_name: str,
    *,
    claude_home: Path,
    tmux_bin: str,
    deadline: float,
    auto_remote_control: bool,
    rc_via_flag: bool = False,
) -> tuple[Optional[str], Optional[UrlSource], Optional[str]]:
    """Poll for the bridge URL, dismissing startup prompts along the way.

    Handshake state (trust dismissed, ``/remote-control`` sent) lives here,
    across poll iterations, so each prompt is answered **at most once** —
    re-sending every cycle used to fill the pane with dozens of
    ``Unknown command: /remote-control`` lines whenever the command didn't
    take. ``rc_via_flag=True`` means the session was spawned with
    ``--remote-control``, so the TUI send is skipped entirely.

    Returns ``(url, source, attention)``. Aborts early with
    ``attention="login_required"`` — that state cannot resolve without a
    human, so burning the rest of the timeout would only delay the answer.
    """
    trust_dismissed = False
    rc_sent = rc_via_flag
    attention: Optional[str] = None
    while time.time() < deadline:
        url, src = resolve_bridge_url(
            session_name, claude_home=claude_home, tmux_bin=tmux_bin
        )
        if url:
            return url, src, None
        pane = tm.capture_pane(session_name, lines=60, binary=tmux_bin)
        attention = classify_pane_attention(pane)
        if attention == ATTENTION_LOGIN_REQUIRED:
            return None, None, attention
        if not trust_dismissed and attention == ATTENTION_TRUST_PROMPT:
            tm.send_keys(session_name, "Enter", binary=tmux_bin)
            trust_dismissed = True
            time.sleep(1.5)
            continue
        if (
            auto_remote_control
            and not rc_sent
            and "remote control active" not in pane.lower()
            and "❯" in pane
        ):
            tm.send_keys(session_name, "/remote-control", "Enter", binary=tmux_bin)
            rc_sent = True
            time.sleep(1.5)
            continue
        time.sleep(0.5)
    return None, None, attention


def request_remote_control(
    session_name: str,
    *,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
    timeout_sec: float = 15.0,
) -> tuple[Optional[str], Optional[UrlSource], Optional[str]]:
    """Drive an existing tmux-hosted claude toward remote control.

    Sends ``/remote-control`` (once, prompts permitting) and waits briefly
    for the bridge URL. Returns ``(url, source, attention)`` — callers
    should check ``attention`` (e.g. ``login_required``) when no URL comes
    back, and surface :func:`attention_hint` to the user.
    """
    return _wait_for_url(
        session_name,
        claude_home=claude_home,
        tmux_bin=tmux_bin,
        deadline=time.time() + timeout_sec,
        auto_remote_control=True,
    )


# --------------------------------------------------------------------------- #
# spawn / resume
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PendingSpawn:
    """A launched tmux session whose bridge-URL handshake hasn't finished.

    Returned by :func:`prepare_spawn` — the fast, synchronous part (tmux
    session exists, pane log piping, ``created`` archive event emitted).
    Pass it to :func:`complete_spawn` to run the slow part (URL wait,
    prompt dismissal); services may do that from a background thread so
    the caller gets an immediate acknowledgment.
    """

    name: str
    cwd: str
    claude_home: Path
    tmux_bin: str
    deadline: float
    auto_remote_control: bool
    rc_via_flag: bool
    warning: Optional[str]
    archive_ctx: Optional["_ArchiveCtx"]


def prepare_spawn(
    name: str,
    *,
    cwd: str,
    resume_id: Optional[str] = None,
    claude_bin: str = DEFAULT_CLAUDE_BIN,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
    url_timeout_sec: float = 120.0,
    auto_remote_control: bool = True,
    claude_name: Optional[str] = None,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    pane_log_path: Optional[Path] = None,
    archive_store=None,
    pane_store=None,
    archive_id: Optional[str] = None,
) -> PendingSpawn:
    """Launch the tmux session and return without waiting for the URL.

    Composes per-session claude flags (``--name`` / ``--model`` /
    ``--effort`` / ``--remote-control``) when the installed binary
    supports them; requested-but-unsupported ``--model`` / ``--effort``
    are reported in ``PendingSpawn.warning`` rather than failing the
    spawn. ``--remote-control`` silently falls back to the TUI handshake
    in :func:`complete_spawn` on older binaries.
    """
    if not Path(cwd).is_dir():
        raise FileNotFoundError(f"cwd does not exist: {cwd}")
    supported = supported_cli_flags(claude_bin)
    argv, skipped = _claude_argv(
        claude_bin,
        supported=supported,
        resume_id=resume_id,
        claude_name=claude_name,
        model=model,
        effort=effort,
        remote_control=auto_remote_control,
    )
    rc_via_flag = auto_remote_control and "--remote-control" not in skipped
    shell_cmd = f"cd {shlex.quote(cwd)} && exec " + " ".join(
        shlex.quote(a) for a in argv
    )

    ctx, ctx_pane_log, _sid = _build_archive_ctx(
        archive_store=archive_store, pane_store=pane_store, archive_id=archive_id
    )
    tm.new_session(name, command=shell_cmd, binary=tmux_bin)
    pane_log = pane_log_path or ctx_pane_log
    if pane_log is not None:
        pane_log.parent.mkdir(parents=True, exist_ok=True)
        tm.pipe_pane_to_file(name, path=pane_log, binary=tmux_bin)
    if ctx is not None:
        ctx.emit_created(name=name, cwd=cwd, pane_log=pane_log, tmux_bin=tmux_bin)

    hard_skips = [f for f in skipped if f in ("--model", "--effort")]
    warning = (
        "Installed claude does not support "
        + ", ".join(hard_skips)
        + " — spawned without."
        if hard_skips
        else None
    )
    return PendingSpawn(
        name=name,
        cwd=cwd,
        claude_home=claude_home,
        tmux_bin=tmux_bin,
        deadline=time.time() + url_timeout_sec,
        auto_remote_control=auto_remote_control,
        rc_via_flag=rc_via_flag,
        warning=warning,
        archive_ctx=ctx,
    )


def complete_spawn(pending: PendingSpawn) -> SpawnResult:
    """Blocking half of the spawn: wait for the URL, then report.

    Safe to call from a background thread — all state it touches (tmux,
    ``~/.claude/sessions/``, the append-only events log) is shared
    machine-wide, not per-process.
    """
    url, src, attention = _wait_for_url(
        pending.name,
        claude_home=pending.claude_home,
        tmux_bin=pending.tmux_bin,
        deadline=pending.deadline,
        auto_remote_control=pending.auto_remote_control,
        rc_via_flag=pending.rc_via_flag,
    )
    claude_pid = find_claude_pid(pending.name, tmux_bin=pending.tmux_bin)
    data = (
        cfs.read_ephemeral_session(claude_pid, claude_home=pending.claude_home)
        if claude_pid is not None
        else None
    )
    warnings = [pending.warning] if pending.warning else []
    if not url:
        hint = attention_hint(attention, tmux_name=pending.name)
        warnings.append(
            hint
            or "Session created but no remote-control URL detected yet — try refreshing."
        )
    result = SpawnResult(
        name=pending.name,
        cwd=pending.cwd,
        claude_pid=claude_pid,
        claude_session_id=(data or {}).get("sessionId"),
        bridge_session_id=(data or {}).get("bridgeSessionId"),
        url=url,
        url_source=src,
        warning="; ".join(warnings) if warnings else None,
        attention=attention,
    )
    if pending.archive_ctx is not None and url:
        pending.archive_ctx.emit_url_acquired(
            name=pending.name,
            url=url,
            claude_session_id=result.claude_session_id,
            claude_pid=claude_pid,
        )
    return result


@dataclass
class _ArchiveCtx:
    """Internal adapter passed to ``_run_spawn`` for event emission.

    Kept private so ``xa.archive`` owns the schema end-to-end. Callers
    use :func:`spawn_session` / :func:`resume_session` with the public
    ``archive_store`` / ``pane_store`` / ``archive_id`` kwargs instead.
    """

    events_store: object
    archive_id: str

    def emit_created(
        self, *, name: str, cwd: str, pane_log: Optional[Path], tmux_bin: str
    ) -> None:
        from xa import archive as arch  # local import to avoid cycle

        tmux_ts: Optional[int] = None
        for t in tm.list_sessions(binary=tmux_bin):
            if t.name == name:
                tmux_ts = t.created
                break
        arch.append_created(
            self.events_store,
            id=self.archive_id,
            name=name,
            cwd=cwd,
            claude_bin=DEFAULT_CLAUDE_BIN,
            tmux_created_ts=tmux_ts,
            pane_log=str(pane_log) if pane_log else None,
        )

    def emit_url_acquired(
        self, *, name: str, url: str, claude_session_id, claude_pid
    ) -> None:
        from xa import archive as arch

        arch.append_url_acquired(
            self.events_store,
            id=self.archive_id,
            name=name,
            url=url,
            claude_session_id=claude_session_id,
            claude_pid=claude_pid,
        )


def new_archive_id() -> str:
    """Fresh 12-char hex id used as the key for pane logs + archive events."""
    return secrets.token_hex(6)


def _build_archive_ctx(
    *, archive_store, pane_store, archive_id: Optional[str]
) -> tuple[Optional[_ArchiveCtx], Optional[Path], Optional[str]]:
    if archive_store is None:
        return None, None, None
    sid = archive_id or new_archive_id()
    pane_log = pane_store.path_for(sid) if pane_store is not None else None
    return _ArchiveCtx(events_store=archive_store, archive_id=sid), pane_log, sid


def spawn_session(
    name: str,
    *,
    cwd: str,
    claude_bin: str = DEFAULT_CLAUDE_BIN,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
    url_timeout_sec: float = 120.0,
    auto_remote_control: bool = True,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    pane_log_path: Optional[Path] = None,
    archive_store=None,
    pane_store=None,
    archive_id: Optional[str] = None,
) -> SpawnResult:
    """Create a detached tmux session running ``claude`` in ``cwd``.

    Waits up to ``url_timeout_sec`` for the bridge URL to appear, dismissing
    the "trust this folder" prompt and enabling remote control (via the
    ``--remote-control`` flag on modern claude binaries, or the TUI
    handshake on older ones). The session name is also passed to
    ``claude --name`` so the Claude-side display name matches; ``model``
    and ``effort`` become per-session ``--model`` / ``--effort`` flags.

    If ``archive_store`` (and optionally ``pane_store``) are given, emits
    ``created`` + ``url_acquired`` events to the archive and pipes the pane
    output to a file under ``pane_store``.

    Services that want an immediate return should call
    :func:`prepare_spawn` and run :func:`complete_spawn` in the background
    instead — this function is simply the composition of the two.
    """
    pending = prepare_spawn(
        name,
        cwd=cwd,
        claude_bin=claude_bin,
        claude_home=claude_home,
        tmux_bin=tmux_bin,
        url_timeout_sec=url_timeout_sec,
        auto_remote_control=auto_remote_control,
        claude_name=name,
        model=model,
        effort=effort,
        pane_log_path=pane_log_path,
        archive_store=archive_store,
        pane_store=pane_store,
        archive_id=archive_id,
    )
    return complete_spawn(pending)


def resume_session(
    claude_session_id: str,
    *,
    cwd: str,
    name: Optional[str] = None,
    claude_bin: str = DEFAULT_CLAUDE_BIN,
    claude_home: Path = cfs.DEFAULT_CLAUDE_HOME,
    tmux_bin: str = tm.DEFAULT_TMUX_BIN,
    url_timeout_sec: float = 120.0,
    auto_remote_control: bool = True,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    pane_log_path: Optional[Path] = None,
    archive_store=None,
    pane_store=None,
    archive_id: Optional[str] = None,
) -> SpawnResult:
    """Launch ``claude --resume <id>`` in a new detached tmux session.

    A user-supplied ``name`` is also passed to ``claude --name`` (renaming
    the resumed session's display name); the auto-generated fallback name
    is tmux-only so a resume never clobbers the session's existing name.
    """
    # Only an explicit caller-chosen name becomes the claude display name.
    claude_name = name
    if name is None:
        # Auto-name: <claude_session_id-short>-r{n}.
        live = {s.name for s in tm.list_sessions(binary=tmux_bin)}
        base = f"resumed-{claude_session_id[:8]}"
        name = next(
            (f"{base}-r{i}" for i in range(1, 100) if f"{base}-r{i}" not in live),
            base,
        )
    pending = prepare_spawn(
        name,
        cwd=cwd,
        resume_id=claude_session_id,
        claude_bin=claude_bin,
        claude_home=claude_home,
        tmux_bin=tmux_bin,
        url_timeout_sec=url_timeout_sec,
        auto_remote_control=auto_remote_control,
        claude_name=claude_name,
        model=model,
        effort=effort,
        pane_log_path=pane_log_path,
        archive_store=archive_store,
        pane_store=pane_store,
        archive_id=archive_id,
    )
    return complete_spawn(pending)
