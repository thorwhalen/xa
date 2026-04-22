"""Command-line interface for ``xa``.

Exposes subcommands via ``argh``: ``list``, ``info``, ``history``.
Later phases add ``spawn``, ``resume``, ``kill``, ``serve`` and others.

Entry point: ``xa`` (see ``[project.scripts]`` in ``pyproject.toml``).
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from xa import claude_fs as cfs
from xa import sessions as sess


# --------------------------------------------------------------------------- #
# formatting helpers
# --------------------------------------------------------------------------- #


def _fmt_duration(sec: Optional[float]) -> str:
    if sec is None:
        return "?"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"


def _fmt_mtime(ts: Optional[float]) -> str:
    if not ts:
        return "?"
    delta = time.time() - ts
    return _fmt_duration(delta) + " ago"


def _short_id(session_id: str) -> str:
    return session_id[:8]


def _truncate(text: Optional[str], width: int) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _render_table(
    rows: list[list[str]], headers: list[str], *, out=sys.stdout
) -> None:
    """Emit a plain two-space-separated column table.

    Widths are computed per-column; the last column soaks up any overflow
    so we never truncate silently.
    """
    if not rows:
        print("(no rows)", file=out)
        return
    all_rows = [headers, *rows]
    widths = [max(len(r[i]) for r in all_rows) for i in range(len(headers))]
    fmt = "  ".join(
        f"{{:<{w}}}" if i < len(headers) - 1 else "{}"
        for i, w in enumerate(widths)
    )
    print(fmt.format(*headers), file=out)
    print(fmt.format(*["-" * w for w in widths]), file=out)
    for r in rows:
        print(fmt.format(*r), file=out)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def _configured_hosts() -> "dict":
    """Load hosts from the user's config file, falling back to a LocalHost."""
    try:
        from xa import config as cfg
        return cfg.load_hosts()
    except Exception as e:
        print(f"warn: failed to load xa config: {e}", file=sys.stderr)
        from xa.hosts import default_hosts
        return default_hosts()


def list_cmd(
    project: "Optional[str]" = None,
    limit: int = 30,
    include_forks: bool = True,
    no_live: bool = False,
    state: "Optional[str]" = None,
    host: "Optional[str]" = None,
    json_out: bool = False,
) -> None:
    """List Claude Code sessions across configured hosts, newest first.

    :param project: Substring match against session cwd (case-insensitive).
    :param limit: Maximum rows to show (0 = unlimited).
    :param include_forks: Include sessions spawned with ``claude --resume``.
    :param no_live: Skip the tmux/ephemeral scan (archive-only view).
    :param state: Restrict to 'live', 'archived', or 'transcript_only'.
    :param host: Restrict to a single configured host by name.
    :param json_out: Emit one JSON object per line instead of a table.
    """
    from typing import cast
    all_hosts = _configured_hosts()
    if host is not None:
        if host not in all_hosts:
            print(
                f"error: host '{host}' not in config; available: {list(all_hosts)}",
                file=sys.stderr,
            )
            sys.exit(1)
        hosts = [all_hosts[host]]
    else:
        hosts = list(all_hosts.values())
    sessions = sess.list_sessions(
        hosts=hosts,
        project=project,
        include_forks=include_forks,
        include_live=not no_live,
        state=cast("Optional[sess.SessionState]", state),
        limit=limit or None,
    )
    if json_out:
        for s in sessions:
            d = asdict(s)
            d["transcript_path"] = (
                str(d["transcript_path"]) if d["transcript_path"] else None
            )
            print(json.dumps(d))
        return

    rows = [
        [
            _short_id(s.id),
            s.host,
            s.state,
            _fmt_mtime(s.modified or s.created),
            str(s.turn_count),
            _truncate(s.cwd or s.project_slug, 30),
            _truncate(s.url or s.first_user_message or s.summary or "", 60),
        ]
        for s in sessions
    ]
    _render_table(
        rows, ["ID", "HOST", "STATE", "MOD", "TURNS", "CWD", "URL / FIRST_MSG"]
    )


def info_cmd(session_id: str, json_out: bool = False) -> None:
    """Show full metadata + forensics for one session.

    :param session_id: Full UUID or unique prefix (e.g. first 8 chars).
    :param json_out: Emit a single JSON object.
    """
    try:
        s = sess.get_session(session_id)
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    if s is None:
        print(f"error: no session matching '{session_id}'", file=sys.stderr)
        sys.exit(1)

    forensics = (
        cfs.transcript_forensics(s.transcript_path) if s.transcript_path else None
    )

    if json_out:
        out = asdict(s)
        out["transcript_path"] = (
            str(out["transcript_path"]) if out["transcript_path"] else None
        )
        if forensics is not None:
            f = asdict(forensics)
            f["transcript_path"] = (
                str(f["transcript_path"]) if f["transcript_path"] else None
            )
            out["forensics"] = f
        print(json.dumps(out, indent=2))
        return

    print(f"id:              {s.id}")
    print(f"state:           {s.state}")
    print(f"host:            {s.host}")
    print(f"cwd:             {s.cwd}")
    print(f"project_slug:    {s.project_slug}")
    print(f"turns:           {s.turn_count}")
    if s.name:
        print(f"name:            {s.name}")
    if s.summary:
        print(f"summary:         {s.summary}")
    if s.forked_from:
        print(f"forked_from:     {s.forked_from}")
    print(f"modified:        {_fmt_mtime(s.modified)}")
    print(f"created:         {_fmt_mtime(s.created)}")
    if s.first_user_message:
        print(f"first_user:      {_truncate(s.first_user_message, 200)}")
    print(f"transcript:      {s.transcript_path}")

    if forensics is not None and forensics.last_tool_name:
        print()
        print("--- forensics (last tool use in transcript) ---")
        print(f"tool:            {forensics.last_tool_name}")
        if forensics.last_tool_command:
            print(f"command:         {_truncate(forensics.last_tool_command, 200)}")
        if forensics.last_tool_exit_code is not None:
            print(f"exit_code:       {forensics.last_tool_exit_code}")
        if forensics.user_interrupted:
            print("user_interrupted: yes  (marker is ambiguous — also fires on bridge drops)")
        if forensics.final_assistant_text:
            print(f"final_msg:       {_truncate(forensics.final_assistant_text, 200)}")


def history_cmd(
    search: "Optional[str]" = None, limit: int = 20, json_out: bool = False
) -> None:
    """Grep over ``~/.claude/history.jsonl``.

    :param search: Case-insensitive substring to filter on (display field).
    :param limit: Maximum rows to emit (0 = unlimited).
    :param json_out: Emit one JSON object per line.
    """
    needle = search.lower() if search else None
    matched = []
    for entry in cfs.history_iter():
        if needle is not None:
            hay = (entry.display or "").lower()
            if needle not in hay:
                continue
        matched.append(entry)

    # Reverse so newest first (history.jsonl is append-only).
    matched.reverse()
    if limit:
        matched = matched[:limit]

    if json_out:
        for e in matched:
            print(json.dumps({
                "cwd": e.cwd,
                "project": e.project,
                "display": e.display,
            }))
        return

    rows = [
        [_truncate(e.cwd or e.project or "?", 30), _truncate(e.display or "", 80)]
        for e in matched
    ]
    _render_table(rows, ["CWD", "DISPLAY"])


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Phase 3: live session commands (spawn / resume / kill)
# --------------------------------------------------------------------------- #


def spawn_cmd(
    cwd: str,
    name: "Optional[str]" = None,
    timeout: float = 120.0,
    no_remote_control: bool = False,
) -> None:
    """Spawn a detached claude-in-tmux session and print its remote URL.

    :param cwd: Working directory to start claude in.
    :param name: tmux session name (auto if omitted).
    :param timeout: Seconds to wait for the bridge URL.
    :param no_remote_control: Skip auto-issuing /remote-control.
    """
    import secrets
    from xa import claude_cli as ccli
    from xa import tmux as tm

    if name is None:
        existing = {s.name for s in tm.list_sessions()}
        for _ in range(50):
            candidate = f"xa-{secrets.token_hex(3)}"
            if candidate not in existing:
                name = candidate
                break
        else:
            print("error: could not generate a unique session name", file=sys.stderr)
            sys.exit(1)

    try:
        result = ccli.spawn_session(
            name,
            cwd=cwd,
            url_timeout_sec=timeout,
            auto_remote_control=not no_remote_control,
        )
    except (FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"name:            {result.name}")
    print(f"cwd:             {result.cwd}")
    if result.claude_pid is not None:
        print(f"claude_pid:      {result.claude_pid}")
    if result.claude_session_id:
        print(f"session_id:      {result.claude_session_id}")
    if result.url:
        print(f"url:             {result.url}")
        print(f"url_source:      {result.url_source}")
    if result.warning:
        print(f"warning:         {result.warning}")


def resume_cmd(
    session_id: str,
    name: "Optional[str]" = None,
    cwd: "Optional[str]" = None,
    timeout: float = 120.0,
) -> None:
    """Resume a past session (``claude --resume``) in a new tmux pane.

    :param session_id: Full UUID or unique prefix.
    :param name: tmux session name (auto if omitted).
    :param cwd: Override cwd (defaults to the original session's cwd).
    :param timeout: Seconds to wait for the bridge URL.
    """
    try:
        s = sess.get_session(session_id)
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    if s is None:
        print(f"error: no session matching '{session_id}'", file=sys.stderr)
        sys.exit(1)
    try:
        result = sess.resume(s, cwd=cwd, name=name, url_timeout_sec=timeout)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"name:            {result.name}")
    print(f"cwd:             {result.cwd}")
    if result.claude_session_id:
        print(f"session_id:      {result.claude_session_id}")
    if result.url:
        print(f"url:             {result.url}")
    if result.warning:
        print(f"warning:         {result.warning}")


def kill_cmd(session_id: str) -> None:
    """Kill the tmux session backing a live Claude Code session.

    :param session_id: Full UUID or unique prefix, or a tmux session name.
    """
    from xa import tmux as tm

    # Try as a tmux session name first (direct short-circuit).
    live_names = {t.name for t in tm.list_sessions()}
    if session_id in live_names:
        tm.kill_session(session_id)
        print(f"killed: {session_id}")
        return

    try:
        s = sess.get_session(session_id)
    except LookupError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    if s is None or s.state != "live":
        print(
            f"error: no live session matching '{session_id}'",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        sess.kill_session(s)
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"killed: {s.tmux_name} (session {s.id[:8]})")


# --------------------------------------------------------------------------- #
# Phase 4: archive (postmortem)
# --------------------------------------------------------------------------- #


def archive_list_cmd(limit: int = 30, json_out: bool = False) -> None:
    """List archived sessions (``created`` events, newest first).

    :param limit: Maximum rows (0 = unlimited).
    :param json_out: One JSON object per line.
    """
    from xa import archive as arch
    from xa import store as st
    from xa import tmux as tm

    events = st.default_events_store()
    panes = st.default_pane_store()
    # Freshen the archive before reading so dead sessions show up.
    try:
        arch.reconcile(events, panes, tm.list_sessions())
    except Exception:
        pass
    recs = arch.records(events, panes)
    if limit:
        recs = recs[:limit]

    if json_out:
        for r in recs:
            print(json.dumps({
                "id": r.id,
                "name": r.name,
                "cwd": r.cwd,
                "created": r.created,
                "gone": r.gone,
                "gone_reason": r.gone_reason,
                "url": r.url,
                "pane_log_bytes": r.pane_log_bytes,
                "claude_session_id": r.claude_session_id,
                "forensics": r.forensics,
            }))
        return

    rows = [
        [
            r.id[:12],
            _truncate(r.name or "", 20),
            r.gone_reason or "alive",
            _fmt_mtime(r.created),
            _fmt_mtime(r.gone) if r.gone else "",
            str(r.pane_log_bytes),
            _truncate(r.cwd or "", 40),
        ]
        for r in recs
    ]
    _render_table(
        rows, ["ID", "NAME", "REASON", "CREATED", "GONE", "LOG_B", "CWD"]
    )


def archive_log_cmd(archive_id: str, tail_kb: int = 64) -> None:
    """Print the pane log for an archived session.

    :param archive_id: The archive id (first column of ``xa archive list``).
    :param tail_kb: Only print the last N KB (0 = full log).
    """
    from xa import store as st

    panes = st.default_pane_store()
    if archive_id not in panes:
        print(f"error: no pane log for id '{archive_id}'", file=sys.stderr)
        sys.exit(1)
    data = panes[archive_id]
    if tail_kb and len(data) > tail_kb * 1024:
        data = data[-tail_kb * 1024:]
    sys.stdout.buffer.write(data)


def sync_cmd(host: "Optional[str]" = None, force: bool = False) -> None:
    """Refresh remote-host caches (SSH hosts; HTTP is always fresh).

    :param host: Only sync this one host (default: all configured).
    :param force: Sync even if the cache isn't stale yet.
    """
    hosts = _configured_hosts()
    targets = (
        [hosts[host]] if host else list(hosts.values())
    )
    if host and host not in hosts:
        print(
            f"error: host '{host}' not in config; available: {list(hosts)}",
            file=sys.stderr,
        )
        sys.exit(1)
    for h in targets:
        if getattr(h, "kind", None) == "local":
            continue
        print(f"syncing {h.name} ({h.kind})...")
        try:
            h.sync(force=force)
        except Exception as e:
            print(f"  error: {e}", file=sys.stderr)
            continue
        err = getattr(h, "_last_sync_error", None)
        if err:
            print(f"  warn: {err}", file=sys.stderr)


def pick_cmd(
    project: "Optional[str]" = None,
    limit: int = 30,
    host: "Optional[str]" = None,
) -> None:
    """Interactive session picker — pick a row by number, then choose an action.

    A pragmatic v1 picker without a TUI framework. Use :command:`xa list`
    for scripting; use :command:`xa pick` when a human is at the keyboard.
    """
    from typing import cast
    all_hosts = _configured_hosts()
    if host is not None and host not in all_hosts:
        print(
            f"error: host '{host}' not in config; available: {list(all_hosts)}",
            file=sys.stderr,
        )
        sys.exit(1)
    hosts = [all_hosts[host]] if host else list(all_hosts.values())
    rows = sess.list_sessions(
        hosts=hosts, project=project, limit=limit or None
    )
    if not rows:
        print("no sessions.")
        return

    for i, s in enumerate(rows, 1):
        live = "●" if s.state == "live" else "·"
        print(
            f"{i:>3}  {live} {_short_id(s.id)}  {s.host:<10}  "
            f"{_fmt_mtime(s.modified or s.created):>8}  "
            f"turns={s.turn_count:<4}  {_truncate(s.cwd or '', 35):<35}  "
            f"{_truncate(s.first_user_message or s.summary or s.url or '', 40)}"
        )
    print()
    try:
        pick = input("pick # (or q to quit): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not pick or pick.lower() == "q":
        return
    try:
        idx = int(pick)
    except ValueError:
        print("not a number.", file=sys.stderr)
        sys.exit(1)
    if not (1 <= idx <= len(rows)):
        print("out of range.", file=sys.stderr)
        sys.exit(1)

    chosen = rows[idx - 1]
    print(f"\nselected: {chosen.id}  ({chosen.state})  cwd={chosen.cwd}")
    if chosen.url:
        print(f"url: {chosen.url}")
    print()
    actions = (
        ["info", "resume", "kill"] if chosen.state == "live"
        else ["info", "resume"]
    )
    print(f"actions: {', '.join(actions)}  (or q)")
    try:
        action = input("action: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if not action or action == "q":
        return
    if action == "info":
        info_cmd(chosen.id)
    elif action == "resume":
        resume_cmd(chosen.id)
    elif action == "kill" and chosen.state == "live":
        kill_cmd(chosen.id)
    else:
        print(f"unknown action: {action!r}", file=sys.stderr)
        sys.exit(1)


def serve_cmd(
    host: str = "127.0.0.1",
    port: int = 8010,
    mount: str = "",
    username: "Optional[str]" = None,
    password: "Optional[str]" = None,
    captcha: bool = False,
) -> None:
    """Run the xa HTTP service (requires the ``xa[service]`` extra).

    :param host: Bind host.
    :param port: Bind port.
    :param mount: Optional mount prefix (e.g. ``/api/xa``).
    :param username: HTTP Basic username; if omitted, no auth is enforced.
    :param password: HTTP Basic password (required if username is given).
    :param captcha: Enable captcha-gated deletes.
    """
    try:
        import uvicorn
        from fastapi import FastAPI
    except ImportError:
        print(
            "error: xa[service] extra not installed. "
            "Run: pip install 'xa[service]'",
            file=sys.stderr,
        )
        sys.exit(1)

    from xa import service as svc

    auth_dep = svc.allow_all
    if username:
        if not password:
            print("error: --password required when --username is given", file=sys.stderr)
            sys.exit(1)
        auth_dep = svc.make_basic_auth(username, password)

    captcha_obj = None
    if captcha:
        key = os.environ.get("XA_CAPTCHA_KEY")
        if not key:
            import secrets
            key = secrets.token_hex(32)
            print("warn: XA_CAPTCHA_KEY not set; using ephemeral key", file=sys.stderr)
        captcha_obj = svc.Captcha(key=key)

    api = svc.build_api(auth=auth_dep, captcha=captcha_obj)

    if mount:
        # Wrap in an outer app so the API lives under /mount/...
        outer = FastAPI()
        outer.mount(mount, api)
        app = outer
    else:
        app = api

    uvicorn.run(app, host=host, port=port)


def archive_forensics_cmd(archive_id: str) -> None:
    """Print rich forensics for an archived session as JSON.

    :param archive_id: Archive id from ``xa archive list``.
    """
    from dataclasses import asdict as _asdict
    from xa import archive as arch
    from xa import claude_fs as cfs
    from xa import store as st

    events = st.default_events_store()
    panes = st.default_pane_store()
    rec = next((r for r in arch.records(events, panes) if r.id == archive_id), None)
    if rec is None:
        print(f"error: no archive record for id '{archive_id}'", file=sys.stderr)
        sys.exit(1)

    out = {
        "id": rec.id,
        "name": rec.name,
        "cwd": rec.cwd,
        "created": rec.created,
        "gone": rec.gone,
        "gone_reason": rec.gone_reason,
        "url": rec.url,
        "claude_session_id": rec.claude_session_id,
        "forensics": rec.forensics,
    }
    if rec.cwd and rec.claude_session_id:
        path = cfs.transcript_path(rec.cwd, rec.claude_session_id)
        if path is not None:
            out["transcript_forensics"] = _asdict(cfs.transcript_forensics(path))
            out["transcript_forensics"]["transcript_path"] = str(
                out["transcript_forensics"].get("transcript_path") or ""
            )
    print(json.dumps(out, indent=2, default=str))


# argh maps a function's __name__ to the subcommand name. Rename the
# handler functions (stripping the _cmd suffix) so the user types
# ``xa list``, not ``xa list_cmd``.
list_cmd.__name__ = "list"
info_cmd.__name__ = "info"
history_cmd.__name__ = "history"
spawn_cmd.__name__ = "spawn"
resume_cmd.__name__ = "resume"
kill_cmd.__name__ = "kill"
serve_cmd.__name__ = "serve"
sync_cmd.__name__ = "sync"
pick_cmd.__name__ = "pick"

archive_list_cmd.__name__ = "list"
archive_log_cmd.__name__ = "log"
archive_forensics_cmd.__name__ = "forensics"

_top_funcs = [
    list_cmd,
    info_cmd,
    history_cmd,
    spawn_cmd,
    resume_cmd,
    kill_cmd,
    serve_cmd,
    sync_cmd,
    pick_cmd,
]
_archive_funcs = [archive_list_cmd, archive_log_cmd, archive_forensics_cmd]


def main() -> None:
    """Entry point referenced by ``pyproject.toml``'s ``[project.scripts]``."""
    import argh

    parser = argh.ArghParser(prog="xa")
    argh.add_commands(parser, _top_funcs)
    argh.add_commands(
        parser,
        _archive_funcs,
        group_name="archive",
        group_kwargs={"help": "Postmortem archive (list, log, forensics)."},
    )
    argh.dispatch(parser)


if __name__ == "__main__":
    main()
