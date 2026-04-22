# xa

**Manage Claude Code sessions across projects and machines.**

Claude Code's built-in `/resume` picker is per-project, per-machine — sessions
on your laptop, your Mac, your server, and your phone all live in separate
silos. `xa` treats sessions as first-class records you can list, search,
spawn, resume, and kill across every machine you use, from one CLI or one
HTTPS endpoint.

Works great on a headless server (tmux-hosted `claude`, phone URL for
remote control via `claude.ai/code/...`). Works fine on a laptop too.

---

## Quick install

```bash
pip install xa                       # library + CLI + SSH transport
pip install 'xa[service]'            # also HTTP service (FastAPI + uvicorn)
```

Runtime requirements (install via your package manager):

| Tool | Why |
| --- | --- |
| `tmux` (≥3.0) | backs every live Claude Code session as a detached pane |
| `claude` | Anthropic's Claude Code CLI — `xa` spawns and resumes it |
| `rsync` + `ssh` | only for `SSHHost` (pull remote session trees) |

`xa` checks for these at runtime and reports a clear error if they're missing.

## Quickstart

Already have some Claude Code sessions on this machine? List them:

```bash
$ xa list
ID        HOST   STATE            MOD      TURNS  CWD                      URL / FIRST_MSG
--------  -----  ---------------  -------  -----  -----------------------  ----------------------------------------------------
7b875f44  local  live             2s ago   494    /root                    https://claude.ai/code/session_01ALoSc...
b3366c60  local  live             20h ago  725    /root/py/proj            https://claude.ai/code/session_012W14x...
4e738fb5  local  transcript_only  18h ago  15     /root                    my apps.thorwhalen.com has an admin level app…
```

Dig into one of them:

```bash
xa info 7b875f44
xa archive forensics 7b875f44     # what was it doing when it last ran a tool?
xa history --search "bridge URL"  # cross-project prompt search
```

Or walk through them interactively:

```bash
xa pick
```

## Core CLI commands

### Sessions

| Command | What it does |
| --- | --- |
| `xa list [--host H] [--project P] [--state S] [--limit N]` | Table of sessions across configured hosts |
| `xa info <id>` | Full record + transcript forensics for one session |
| `xa history [--search PHRASE]` | Grep `~/.claude/history.jsonl` |
| `xa pick` | Interactive picker (list → number → action) |
| `xa spawn <cwd>` | Start `claude` in tmux at `cwd`, print phone URL |
| `xa resume <id>` | `claude --resume <id>` in a new tmux pane |
| `xa kill <id-or-tmux-name>` | End a live session |

### Archive

| Command | What it does |
| --- | --- |
| `xa archive list` | Postmortem of every session `xa` spawned |
| `xa archive log <id>` | Raw pane log of an archived session |
| `xa archive forensics <id>` | Death reason + last tool use + transcript tail |

### Multi-host

| Command | What it does |
| --- | --- |
| `xa sync [--host H] [--force]` | Refresh SSH-host caches (rsync pull) |
| `xa serve [--host H] [--port P] [--username U --password P] [--captcha]` | Run the HTTP service (requires `xa[service]`) |

Every command supports `--help` for full flags; every listing supports
`--json-out` for scripting.

## Configuration

`xa` reads `~/.config/xa/config.toml` (respects `$XDG_CONFIG_HOME`, override
with `$XA_CONFIG`). Missing file → single `LocalHost` is used.

```toml
[settings]
cache_dir = "~/.cache/xa/remotes"
stale_threshold_sec = 3600
claude_bin = "claude"
tmux_bin = "tmux"

# Local is always there, but you can spell it out:
[hosts.local]
kind = "local"

# Another machine via SSH. Uses your ~/.ssh/config → agent/keys.
[hosts.devbox]
kind = "ssh"
host = "devbox"           # SSH alias OR raw hostname
user = "deploy"           # optional
remote_claude_home = "~/.claude"

# Another xa server over HTTPS.
[hosts.phone]
kind = "http"
base_url = "https://apps.example.com/api/xa"
auth = "basic"            # or "bearer" or omit
username = "me"
password_env = "XA_PHONE_PW"   # resolved from env — keeps secrets out of TOML
```

After editing, `xa sync` pulls SSH hosts into the local cache.

## Python API

Everything the CLI does is also available as a Python library:

```python
import xa

# List all sessions on this machine.
for s in xa.list_sessions(limit=10):
    print(s.id[:8], s.state, s.cwd, s.url)

# Multi-host.
hosts = xa.load_hosts()                 # reads ~/.config/xa/config.toml
for s in xa.list_sessions(hosts=list(hosts.values())):
    print(s.host, s.id[:8], s.url)

# Spawn / resume / kill.
local = xa.LocalHost()
result = local.spawn("my-session", cwd="/tmp")
print(result.url)                       # https://claude.ai/code/session_…

# Read the archive.
events = xa.default_events_store()
panes = xa.default_pane_store()
for rec in xa.records(events, panes):
    print(rec.id, rec.gone_reason, rec.url)
```

## Running the HTTP service

Stand up an `xa` server on a headless box so your phone / laptop / another
script can reach its sessions over HTTPS:

```bash
pip install 'xa[service]'
xa serve --host 0.0.0.0 --port 8010 --username me --password "$PW" --captcha
```

Put it behind a reverse proxy (Caddy, nginx, Coolify/Traefik) with TLS. A
matching `HTTPHost` entry in another machine's `config.toml` makes its
sessions show up in your `xa list` alongside the local ones.

The service mounts:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/sessions` | List (query: `project`, `state`, `include_forks`, `limit`) |
| `POST` | `/sessions` | Create a tmux+claude session; wait up to 120s for the bridge URL |
| `DELETE` | `/sessions/{name}` | Kill (optionally captcha-gated) |
| `GET` | `/sessions/{id}/info` | Metadata + forensics + pane tail |
| `POST` | `/sessions/{id}/resume` | `claude --resume` in a new pane |
| `GET` | `/archive` | Postmortem list |
| `GET` | `/archive/{id}/forensics` | Rich forensics |
| `GET` | `/archive/{id}/log` | Pane log (supports `?tail_kb=`) |
| `GET` | `/captcha` | 4-letter challenge (only with `--captcha`) |
| `GET` | `/health` | Liveness |

For embedding in a larger FastAPI app (enlace, tw_platform, etc.), skip
`xa serve` and use the factory directly:

```python
from xa.service import build_api, make_basic_auth, Captcha

api = build_api(
    auth=make_basic_auth("me", os.environ["XA_PW"]),
    captcha=Captcha(key=os.environ["XA_CAPTCHA_KEY"]),
)
outer_app.mount("/api/xa", api)
```

## How session data flows

```
 ┌──────────── local machine ─────────────┐   ┌────────── remote machine ─────────┐
 │                                        │   │                                   │
 │  ~/.claude/projects/<slug>/*.jsonl     │   │  ~/.claude/projects/<slug>/*.jsonl│
 │  ~/.claude/sessions/<pid>.json         │   │  ~/.claude/sessions/<pid>.json    │
 │         │          │                   │   │            │                      │
 │         ▼          ▼                   │   │            ▼                      │
 │   xa.claude_fs   xa.tmux ─────┐        │   │      rsync (SSHHost) ─┐           │
 │         │         │           │        │   │      or HTTP (HTTPHost)│          │
 │         └────┬────┘           │        │   │                        │          │
 │              ▼                │        │   │                        ▼          │
 │       Session dataclass ◄─────┘        │   │                 cached ~/.claude/ │
 │              │                         │   │                        │          │
 │              ▼                         │   │                        ▼          │
 │       xa.list_sessions ◄───────────────┼───┤                  remote Sessions  │
 │              │                         │   │                                   │
 │              ▼                         │   └───────────────────────────────────┘
 │        CLI / TUI / API                 │
 └────────────────────────────────────────┘
```

## How it compares

| Feature | `xa` | [`cc-sessions`](https://github.com/chronologos/cc-sessions) | `claude --resume` | `edualc` |
| --- | :---: | :---: | :---: | :---: |
| Cross-project discovery | ✓ | ✓ | per-project | ✓ (single host) |
| Cross-machine discovery | ✓ (SSH + HTTP) | ✓ (SSH) | ✗ | ✗ |
| Live tmux / spawn / kill | ✓ | ✗ | ✗ | ✓ |
| Bridge URL resolution | ✓ | ✗ | N/A | ✓ |
| Postmortem / death reason | ✓ | ✗ | ✗ | ✓ |
| HTTP API surface | ✓ | ✗ | ✗ | ✓ |
| Language | Python | Rust | — | Python |

Short version: cc-sessions pioneered cross-machine *listing*; edualc pioneered
live CRUD + URL resolution + postmortem on one box; `xa` unifies them and
exposes both as a library so other apps (including edualc itself) can be
thin clients.

## Development

Layered architecture is documented in [`misc/docs/design.md`](misc/docs/design.md);
phased plan in [`misc/docs/roadmap.md`](misc/docs/roadmap.md); research notes
on Claude Code internals in [`misc/docs/research.md`](misc/docs/research.md).

```bash
git clone https://github.com/thorwhalen/xa.git
cd xa
pip install -e '.[dev,service]'
pytest -q                              # ~98 tests, ~8 seconds
XA_RUN_INTEGRATION=1 pytest -q         # also runs the real-claude spawn test
```

Test layout mirrors source layout: `tests/test_<module>.py` per module.
FastAPI tests stand up a real uvicorn server in-thread; tmux tests use real
tmux (auto-skipped if not installed). Unit tests for cross-machine transport
(SSH) monkeypatch `_run` so no actual SSH is required.

There's a dev-facing [SKILL.md](.claude/skills/xa-dev/SKILL.md) Claude Code
will pick up when you're working on this repo (project layout, testing
conventions, known pitfalls).

## License

MIT
