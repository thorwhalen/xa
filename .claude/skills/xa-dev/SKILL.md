---
name: xa-dev
description: Use when developing on or diagnosing the `xa` package (Claude Code session manager). Triggers on work under /root/py/proj/t/xa, edits to xa/xa/*.py, failing tests, or questions about xa's architecture, layering, or extension points. Not for using xa as an end user — that's the shipped xa-install skill.
---

# xa — developer skill

Guide for working inside the `xa` codebase — a Python package that discovers, spawns, resumes, and archives Claude Code sessions across local + SSH + HTTP hosts. This skill loads when you're editing code in the repo.

## Architecture at a glance

Six layers, bottom up. Each only imports from layers below (with one documented exception for the `hosts ↔ sessions` cycle — see Pitfalls).

| Layer | Module | What it does |
| --- | --- | --- |
| 6b | `xa.cli` | argh subcommands (`list`, `info`, `history`, `spawn`, `resume`, `kill`, `serve`, `sync`, `pick`, `archive …`) |
| 6a | `xa.service` | `build_api(...)` → FastAPI app (optional, requires `xa[service]`) |
| 5  | `xa.sessions` | `Session` dataclass + `list_sessions(hosts=…)` + `get_session` / `kill_session` / `resume` |
| 4  | `xa.hosts.*` | `Host` Protocol + `LocalHost` / `SSHHost` / `HTTPHost` |
| 4  | `xa.archive` | event log + `reconcile` + `classify_death` + `records` |
| 3b | `xa.claude_cli` | `spawn_session` / `resume_session` / `resolve_bridge_url` / readiness handshake |
| 3a | `xa.tmux` | pure tmux wrappers (nothing Claude-specific) |
| 2  | `xa.claude_fs` | read-only view of `~/.claude/` — transcripts, ephemeral files, history |
| 1  | `xa.store`, `xa.config` | JsonLinesStore, FileStore, TOML loader |

Full design doc: `misc/docs/design.md`. Research on Claude Code internals: `misc/docs/research.md`. Phased plan: `misc/docs/roadmap.md`.

## SSOT: the `Session` dataclass

Every renderer (CLI, TUI, HTTP API) is a projection of `xa.sessions.Session`. When extending, **add to the dataclass first**; don't create per-renderer types. Fields not applicable to a given state are `None`. Three states: `"live"`, `"archived"`, `"transcript_only"`.

## Tests

```bash
pytest -q                       # ~98 tests, ~8 seconds
pytest tests/test_<x>.py -v     # one module
XA_RUN_INTEGRATION=1 pytest -q  # also runs the gated real-claude spawn test
```

Test layout mirrors source: `tests/test_<module>.py`. Conventions:

- **Pure modules** (`claude_fs`, `archive`, `store`, `config`): fixture-based, fully hermetic.
- **tmux** (`test_tmux.py`): requires real tmux (auto-skip if absent). Each test uses a `xa-test-<hex>` session name and cleans up in `finally`.
- **FastAPI** (`test_service.py`, `test_hosts_http.py`): the HTTP host test stands up a real uvicorn server on a random port in a daemon thread.
- **CLI** (`test_cli.py`, `test_pick.py`): drive via `subprocess` + `XA_CLAUDE_HOME` env var pointing at a fixture.
- **SSH** (`test_hosts_ssh.py`): monkeypatches `SSHHost._run` — never actually SSH.

Don't leave side effects in `~/.claude/` or `~/.xa/`. Use `tmp_path` fixtures and `XA_CLAUDE_HOME` / `XA_STATE_DIR` env vars for any code that reads/writes per-user dirs.

## Known pitfalls (already burned us)

1. **`JsonLinesStore.__len__` makes empty stores falsy.** `events_store or default_events_store()` silently substituted the default. In `build_api` we use `is not None`; apply the same pattern in any new factory.
2. **FastAPI treats pydantic bodies on DELETE as query params.** Must annotate with `Body(default=…)`. Request models live at *module scope* in `xa/service.py` to avoid `TypeAdapter` forward-ref errors.
3. **`/proc` walks race.** `tmux.descendants` / `tmux.proc_comm` catch `ProcessLookupError` and generic `OSError` — add these to any new `/proc` reader.
4. **`tmux` needs the trailing colon.** Always target as `f"{name}:"` via `tmux.session_target(name)`; bare names resolve to windows/panes.
5. **Bridge URL format.** `https://claude.ai/code/{bridgeSessionId}` — the `bridgeSessionId` already starts with `session_`. Don't prepend.
6. **"Request interrupted by user" is ambiguous.** Also fires on phone-standby bridge resets. Don't infer user intent from the marker alone.
7. **Slug encoding is lossy.** `/` ↔ `-` with leading `-` kept. A cwd containing literal `-` round-trips to the wrong slug — Claude Code itself has this bug, we inherit it.
8. **`sessions ↔ hosts` import cycle.** `LocalHost.iter_sessions` needs `Session` from `xa.sessions`. Resolved with a function-level import. Don't try to move the dataclass around to "fix" it; keep the deferred import.

## Common jobs

**Add a new CLI subcommand.**
1. Write a `*_cmd(...)` function in `xa/cli.py`.
2. Rename it via `foo_cmd.__name__ = "foo"`.
3. Append to `_top_funcs` (or `_archive_funcs` for nested).
4. Add a test in `tests/test_cli.py` (subprocess + `XA_CLAUDE_HOME` if it touches discovery).

**Add a new Host backend.**
1. Create `xa/hosts/<kind>.py` implementing the duck-typed `Host` protocol (name, kind, `iter_sessions`, `spawn`, `resume`, `kill`, `capture_pane`, `sync`).
2. Register in `xa.hosts.__init__`'s re-exports.
3. Extend `xa.config._build_host` to handle the new `kind`.
4. Add tests mirroring `test_hosts_<existing>.py`.

**Add a new route.**
1. Define inside `build_api(...)` in `xa/service.py`, beside the existing ones.
2. For DELETE or PATCH bodies, use `req: FooReq = Body(default=FooReq())` (with `FooReq` at module scope).
3. Test via `FastAPI`'s `TestClient` in `tests/test_service.py`.

**Bump the version.** Change both `pyproject.toml::version` and `xa/__init__.py::__version__`.

## Don't

- Don't add hard deps to the base package. Heavy libs go in `[project.optional-dependencies]` (e.g. fastapi/uvicorn in `[service]`).
- Don't access `~/.claude/` or `~/.xa/` without honoring `XA_CLAUDE_HOME` / `XA_STATE_DIR`.
- Don't gate behavior on the user's `claude` binary being present at import time. Fail at call time with a clear message.
- Don't let a failing `reconcile` break a listing. It runs in `try/except Exception: pass` inside request handlers on purpose — live data beats postmortem freshness.
