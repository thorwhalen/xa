# Design — xa

Architectural plan for `xa`, a Python toolkit for discovering, listing,
resuming, and managing Claude Code sessions — local or remote, live or
archived. Grounded in the research in `research.md`. Roadmap lives in
`roadmap.md`.

---

## 0. Guiding principles

These map to the project-level principles in `~/.claude/CLAUDE.md` and shape
every decision below.

- **Progressive disclosure.** `xa.list_sessions()` with zero arguments should
  Just Work on the local machine. Cross-machine, custom stores, auth,
  postmortem — all opt-in via keyword-only arguments with smart defaults.
- **SSOT for the `Session` record.** One dataclass is the canonical view of a
  Claude Code session (live, archived, transcript-only, forked). All
  renderers (CLI table, TUI picker, HTTP API, web UI) are projections of
  that record.
- **Functional over OOP.** Small pure functions first. Classes only for the
  genuine state containers (transports with caches, the FastAPI service,
  the archive writer).
- **Dependency injection.** Storage, transport, auth, and the `claude` binary
  path are injectable. No module-level globals hiding assumptions.
- **Split read from write.** Discovery / metadata / forensics code must never
  touch tmux, processes, or the network. That separation is what lets us
  read remote caches, dead sessions, and live sessions through one API.

---

## 1. High-level architecture

Layers, bottom up. Each layer depends only on layers below it.

```
┌───────────────────────────────────────────────────────────────────┐
│ xa.service (FastAPI)     xa.cli (TUI/table)     xa.webui (assets) │  6. Interfaces
├───────────────────────────────────────────────────────────────────┤
│          xa.sessions (unified Session model + discovery)          │  5. Domain
├───────────────────────────────────────────────────────────────────┤
│   xa.archive (postmortem)         xa.hosts (Host + transports)    │  4. Coordination
├───────────────────────────────────────────────────────────────────┤
│ xa.claude_cli (spawn/resume/URL)          xa.tmux (pure tmux)     │  3. Process
├───────────────────────────────────────────────────────────────────┤
│        xa.claude_fs (~/.claude readers, transcript parsing)       │  2. Data
├───────────────────────────────────────────────────────────────────┤
│   xa.store (dol-style key-value storage)   xa.config (TOML)       │  1. Infrastructure
└───────────────────────────────────────────────────────────────────┘
```

A consumer of `xa` (e.g. a future simplified `edualc`) composes layers 5-6;
they do not touch 1-2 directly unless they really want to.

---

## 2. Module breakdown

### `xa.claude_fs` — pure filesystem view of `~/.claude/`

No process work, no network, no tmux. Deterministic, testable.

Candidate signatures (keyword-only from 2nd arg):

```python
def iter_transcript_files(claude_home: Path = ...) -> Iterator[Path]: ...
def parse_project_slug(slug: str) -> str: ...       # "-root-py-proj-x" → "/root/py/proj/x"
def encode_project_slug(cwd: str) -> str: ...       # inverse
def read_ephemeral_session(pid: int, *, claude_home: Path = ...) -> dict | None: ...
def iter_ephemeral_sessions(*, claude_home: Path = ...) -> Iterator[dict]: ...
def transcript_metadata(path: Path) -> TranscriptMeta: ...   # cwd, summary, name, fork_of, turn_count, first_user_message
def iter_transcript_events(path: Path) -> Iterator[TranscriptEvent]: ...
def transcript_forensics(path: Path) -> TranscriptForensics: ...  # last_tool_use, last_tool_result, exit_code, markers
def history_iter(*, claude_home: Path = ...) -> Iterator[HistoryEntry]: ...   # ~/.claude/history.jsonl
```

Key invariants worth encoding:
- Transcript files have UUID basenames. Reject non-UUID.
- `forkedFrom.sessionId` is the fork pointer.
- Slug ↔ cwd rule: `/` ↔ `-`, with a leading `-` kept. Round-trip must be
  exact or the mapping silently drops sessions.

### `xa.tmux` — pure tmux wrappers

Host-agnostic. No Claude Code knowledge. Could be extracted to its own
package later if useful elsewhere.

```python
def tmux_list_sessions(*, binary: str = "tmux") -> list[TmuxSession]: ...
def tmux_new_session(name: str, *, command: str, binary: str = "tmux") -> None: ...
def tmux_kill_session(name: str, *, binary: str = "tmux") -> None: ...
def tmux_capture_pane(name: str, *, lines: int = 200, binary: str = "tmux") -> str: ...
def tmux_pipe_pane_to_file(name: str, *, path: Path, binary: str = "tmux") -> None: ...
def tmux_pane_pid(name: str, *, binary: str = "tmux") -> int | None: ...
def tmux_descendants(pid: int) -> list[int]: ...        # via /proc, no pstree dep
def tmux_session_target(name: str) -> str: ...          # the ":" trick — critical
```

Lessons to enshrine:
- **Always target sessions as `f"{name}:"`** — bare names silently resolve to
  windows/panes.
- tmux returns non-zero when the server isn't running — treat as empty
  list, don't raise.

### `xa.claude_cli` — the `claude` binary + bridge URL

Glue that ties `claude_fs` + `tmux` + the `claude` CLI.

```python
def spawn_session(
    name: str,
    *,
    cwd: str,
    claude_bin: str = "claude",
    url_timeout_sec: float = 120.0,
    auto_remote_control: bool = True,
    pane_log_path: Path | None = None,
) -> SpawnResult: ...   # name, pid, url, url_source, claude_session_id

def resume_session(
    claude_session_id: str,
    *,
    cwd: str,
    name: str | None = None,          # auto: "{base}-r{n}"
    claude_bin: str = "claude",
    url_timeout_sec: float = 120.0,
) -> SpawnResult: ...

def resolve_bridge_url(session_name: str) -> tuple[str | None, str | None]: ...
# returns (url, source) where source ∈ {"session_file", "pane_capture", None}

def send_remote_control(session_name: str) -> None: ...
```

Embedded knowledge (from `edualc/server.py`):
- Bridge URL format: `https://claude.ai/code/{bridgeSessionId}`. Do not
  prepend a prefix; `bridgeSessionId` already starts with `session_`.
- Primary lookup: find the claude descendant pid → read
  `~/.claude/sessions/<pid>.json` → take `bridgeSessionId`.
- Fallback: regex-scan the pane capture for
  `https://claude\.ai/code/session_[A-Za-z0-9_-]+`.
- Readiness handshake: detect "trust this folder" → send Enter; detect
  main prompt (`❯`) without "remote control active" → send `/remote-control`.

### `xa.hosts` — remote transport abstraction

`Host` is a small `Protocol`, not a class hierarchy.

```python
class Host(Protocol):
    name: str
    kind: Literal["local", "ssh", "http"]

    def transcripts_root(self) -> Path: ...          # local path (possibly cached)
    def ephemeral_root(self) -> Path | None: ...     # None if not directly readable
    def list_live_tmux(self) -> list[TmuxSession]: ...
    def spawn(self, name: str, cwd: str, **opts) -> SpawnResult: ...
    def resume(self, claude_session_id: str, cwd: str, **opts) -> SpawnResult: ...
    def kill(self, name: str) -> None: ...
    def capture_pane(self, name: str, lines: int = 200) -> str: ...
```

Three concrete implementations:

- `LocalHost` — in-process. `transcripts_root()` = `~/.claude/projects`.
- `SSHHost(host, user=None, projects_dir=None, cache_dir=...)` —
  cc-sessions-style. `transcripts_root()` triggers a periodic rsync
  (`rsync -a -z --delete -e ssh <user@host>:~/.claude/projects/
  <cache_dir>/<name>/`) when stale; returns the cache path. Live tmux /
  spawn / resume / kill dispatch to `ssh <host> tmux …` and `ssh <host>
  claude --resume …`. Auth = SSH keys.
- `HTTPHost(base_url, auth)` — edualc-style. Calls the `/api/<mount>/`
  endpoints. `transcripts_root()` is either an rsync cache (if the host
  exposes SSH too) or a local materialization from API responses. Auth =
  whatever the server wants (Basic, enlace session, bearer token).

Staleness policy + cache dir come from config; same shape as cc-sessions.

### `xa.store` — pluggable key-value storage

Ultra-thin wrapper over [`dol`](https://github.com/i2mint/dol) `KV` stores:

- Default: `dol.JsonLinesStore` (append-only JSONL, matches edualc today).
- Swap targets: `dol.Files`, `dol.DirReader`, SQLite via `dol.sqldol`, etc.

Two stores are enough for v1:

```python
events_store: MutableMapping[str, dict]     # one dict per event; iterable in order
pane_log_store: MutableMapping[str, bytes]  # session_id → pane log bytes
```

A single protocol boundary between "archive" and "storage" means the archive
engine can be tested against an in-memory dict, and users can later swap
the on-disk layout without touching the core.

### `xa.archive` — postmortem engine

Factored-out version of the edualc archive, unchanged in behavior but
independent of FastAPI / HTTP.

```python
def append_event(store, event: dict) -> None: ...
def reconcile(store, live_sessions: list[TmuxSession], *, now: float = ...) -> list[dict]: ...
def classify_death(pane_log_bytes: bytes, forensics: TranscriptForensics | None) -> DeathReason: ...
def session_records(store) -> list[SessionRecord]: ...

DeathReason = Literal["clean_exit", "abrupt", "interrupted", "tool_crash", "replaced", "missing"]
```

Death-time is always **pane-log mtime**, never reconcile-run time. The
"interrupted" label is suppressed when only the ambiguous transcript marker
fires and no corroborating evidence exists — see research §1 (phone-standby
bridge drops emit the same marker).

### `xa.sessions` — the unified domain

Single dataclass. Everything above collapses into this.

```python
@dataclass(frozen=True)
class Session:
    # identity
    id: str                              # tmux-session-ish, or synthetic for pure-transcript
    claude_session_id: str | None        # UUID from claude; identifies the transcript
    bridge_session_id: str | None        # the phone URL's tail
    # location
    host: str                            # Host.name
    cwd: str
    project_slug: str
    # status
    state: Literal["live", "archived", "transcript_only"]
    live_pid: int | None
    death_reason: DeathReason | None
    # content
    name: str | None                     # custom title via /rename
    summary: str | None
    first_user_message: str | None
    turn_count: int
    forked_from: str | None
    # times
    created: float | None
    modified: float | None
    gone: float | None
    # derived
    url: str | None                      # https://claude.ai/code/...
    url_source: Literal["session_file", "pane_capture"] | None
```

Discovery API:

```python
def list_sessions(
    hosts: Iterable[Host] = (LocalHost(),),
    *,
    state: Collection[str] | None = None,        # filter
    project: str | None = None,
    include_forks: bool = False,
    search: str | None = None,                    # full-text over transcripts
    limit: int | None = None,
) -> list[Session]: ...

def resume(session: Session, *, host: Host | None = None, **opts) -> SpawnResult: ...
def kill(session: Session, *, host: Host | None = None) -> None: ...
def fork(session: Session, *, host: Host | None = None, **opts) -> SpawnResult: ...
```

`Session` is the cc-sessions session record + the edualc live/archive
fields, unified. Fields that don't apply to a given state are `None`.

### `xa.service` — FastAPI app builder

A function, not a module that imports-and-runs. Returns a ready-to-mount
`FastAPI` instance so it composes cleanly into enlace/tw_platform or a
standalone `uvicorn` launch.

```python
def build_api(
    *,
    hosts: dict[str, Host],                      # named hosts (incl. "local")
    archive_store,
    auth: Callable[..., Any],                    # FastAPI dependency
    mount_prefix: str = "/api/xa",
    include_webui: bool = False,
) -> FastAPI: ...
```

Routes (edualc shape, generalised):

- `GET    /sessions`                          (list; query: host, state, project, search, limit)
- `POST   /sessions`                          (create on a named host)
- `DELETE /sessions/{id}`                     (kill; captcha injected via auth dep, optional)
- `GET    /sessions/{id}/info`                (pane tail + forensics)
- `POST   /sessions/{id}/resume`              (fork-resume; new session)
- `GET    /archive`                           (postmortem list, per-host)
- `GET    /archive/{id}/forensics`            (rich view)
- `GET    /archive/{id}/log`                  (raw pane log, tail_kb)
- `GET    /captcha`                           (optional, delegated to auth)
- `GET    /health`

This is the direct replacement target for `/api/edualc/` — same UX, wider
host coverage, injectable auth.

### `xa.cli` — CLI interface

Small `argh` or `typer` surface. cc-sessions-compatible flags where
sensible, so users can migrate intuition:

```
xa                          # interactive TUI picker (local + configured hosts)
xa list                     # table output
xa list --project xa --host devbox
xa resume <id>              # resume by session ID
xa kill <id>
xa sync                     # force-sync all remote hosts
xa archive                  # postmortem listing
xa archive log <id>
xa serve [--host 0.0.0.0] [--port 8010]   # run xa.service as a FastAPI app
```

TUI: a thin `prompt_toolkit` or `textual` layer. Optional extra.

---

## 3. Configuration

Single TOML file at `~/.config/xa/config.toml` (respect `$XDG_CONFIG_HOME`).
Merges cc-sessions's `remotes.toml` shape with xa-specific settings.

```toml
[settings]
cache_dir = "~/.cache/xa/remotes"
stale_threshold_sec = 3600
claude_bin = "claude"
tmux_bin = "tmux"

[hosts.local]
kind = "local"

[hosts.devbox]
kind = "ssh"
host = "devbox"

[hosts.workstation]
kind = "ssh"
host = "192.168.1.100"
user = "ec2-user"
projects_dir = "/home/ec2-user/.claude/projects"

[hosts.thorwhalen]
kind = "http"
base_url = "https://apps.thorwhalen.com/api/xa"
auth = "basic"          # or "enlace_session"
```

Env var override pattern (matches edualc's style):
`XA_CONFIG`, `XA_CACHE_DIR`, `XA_CLAUDE_BIN`, `XA_TMUX_BIN`.

---

## 4. Dependency strategy: cc-sessions

cc-sessions is Rust; `xa` is Python. Three options, ranked by effort:

1. **Reimplement in Python (chosen for v1).** Discovery + fork tree + basic
   search in pure Python. Python is fast enough at typical session-tree
   sizes (hundreds, low thousands of sessions). Gives us tight coupling
   with `xa.sessions`'s domain model, no subprocess boundary. The parts
   worth reimplementing are genuinely small — the session scanner is a
   few dozen lines.
2. **Optional shell-out.** If cc-sessions is installed, expose an `xa cc`
   passthrough for its TUI; use `--list --include-forks` as a fallback
   before we implement a full TUI. Useful transition path during early
   `xa` development.
3. **pyo3 wrapping.** Only if profiling shows Python discovery is a
   bottleneck on very large trees. Not for v1.

The rsync-over-SSH transport pattern is the one idea from cc-sessions we
copy verbatim — commands, flags, trailing slash, cache dir — because it is
already battle-tested and there's no reason to reinvent it.

---

## 5. Migration path from `edualc`

`edualc` continues to work unchanged while `xa` grows. Once `xa` covers the
API surface:

1. **Phase A — library extraction.** Pull the pure logic (transcript
   forensics, death classifier, bridge URL resolver, `/proc` descendants)
   out of `edualc/server.py` into `xa.claude_fs` + `xa.claude_cli` +
   `xa.archive`. `edualc/server.py` imports from `xa`, shrinking to a thin
   auth + routes layer.
2. **Phase B — route swap.** Replace edualc's handwritten routes with
   `xa.service.build_api(hosts={"local": LocalHost()}, …)`. edualc becomes
   a 30-line mounter.
3. **Phase C — multi-host.** Add `SSHHost` / `HTTPHost` entries to the
   edualc config; edualc (now a trivial xa consumer) surfaces sessions
   from other machines in the same UI.
4. **Phase D — retire `edualc`?** Possibly; or keep it as the user-facing
   web app while `xa` remains the library. The web UI in
   `edualc/frontend/index.html` is small enough to stay where it is, or
   move into `xa.webui` as an optional resource folder.

---

## 6. What xa deliberately does NOT do (v1)

- **Bridge / WebSocket protocol implementation.** `xa` reads the
  `bridgeSessionId`, produces the URL, lets the user click. It does not
  open or manage the bridge itself; that's Claude Code internals.
- **mTLS / per-device client cert issuance.** The edualc README sketches
  this; xa leaves it to the reverse proxy (Caddy).
- **Account quota / billing display.** No public API for it.
- **Non-tmux process hosts.** `xa` assumes tmux for detached live sessions.
  Supporting systemd / nohup / screen is possible but not for v1.
- **Session editing / transcript rewriting.** Read-only over transcripts.
  The only write paths are: spawn, resume, kill, append archive events,
  write pane logs.

---

## 7. Package layout (concrete)

```
xa/
  __init__.py            # re-exports list_sessions, Session, LocalHost, build_api
  claude_fs.py           # layer 2
  tmux.py                # layer 3a (pure tmux)
  claude_cli.py          # layer 3b (tmux + claude binary + bridge URL)
  hosts/
    __init__.py          # Host protocol + registry loader
    local.py
    ssh.py
    http.py
  store.py               # layer 1 (dol facades)
  archive.py             # layer 4a
  sessions.py            # layer 5 (Session + list_sessions + actions)
  service.py             # layer 6a (build_api)
  cli.py                 # layer 6b (entry point)
  config.py              # TOML loader, env var overrides
  webui/                 # layer 6c (static assets; optional package-data)
    index.html
tests/
  claude_fs/
  tmux/
  ...
misc/
  docs/
    research.md
    design.md      (this file)
    roadmap.md
```

Console scripts in `pyproject.toml`:

```toml
[project.scripts]
xa = "xa.cli:main"
```

---

## 8. Testing approach

The read-only discovery layer (`claude_fs`, transcript parsing, slug
conversion) is deterministic → unit-testable with a fixture tree of fake
`~/.claude/projects/...` dirs. Ship a small fixture generator.

tmux / claude-binary interaction needs integration tests; gate behind a
`XA_RUN_INTEGRATION=1` env var so CI without tmux can still run the pure
unit tests.

FastAPI service: `TestClient` + an in-memory `dict` archive store + a
`FakeHost` returning a handcrafted `Session` list.

Property tests worth having:
- Slug round-trip: `encode_project_slug(parse_project_slug(s)) == s`.
- Archive reconcile idempotence: running it twice produces no new events
  if nothing changed.
- Death-reason classifier: given (pane log, forensics) fixtures, produces
  expected labels.

---

## 9. Deferred / open for discussion

- Whether to depend on `dol` from day 1 (pulls in a nontrivial dep tree) or
  start with a tiny in-repo `JsonLinesStore` and swap later.
- Naming: `xa` is short but opaque. The PyPI slot is already Thor's (under
  `t-c-w/xa` for an unrelated project). Reuse or pick a new name?
- Whether `SSHHost` should be implemented via `paramiko` (Python) or by
  shelling out to `ssh`/`rsync` (match cc-sessions). Shell-out is simpler
  and picks up the user's `~/.ssh/config` for free; paramiko is nicer to
  test. Leaning shell-out.
- TUI framework: `textual` (nice but heavy) vs `prompt_toolkit` (lighter,
  more DIY) vs a thin `rich` table + inline prompts (no fullscreen TUI at
  all, like `cc-sessions --list`). A non-TUI v1 might be the right call.
- Whether to auto-register the user's current `tw_platform` server as a
  default `HTTPHost` so `xa` out-of-box sees the Hetzner sessions once
  edualc is migrated.
