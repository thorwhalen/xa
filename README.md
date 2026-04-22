# xa

**Manage Claude Code sessions across projects and machines.**

Claude Code's built-in `/resume` picker is per-project, per-machine — sessions
on your laptop, your Mac, your server, and your phone live in separate silos.
`xa` treats sessions as first-class records you can list, search, spawn,
resume, and kill across every machine you use, from one CLI or one HTTPS
endpoint.

Works great on a headless server (tmux-hosted `claude`, phone URL via
`claude.ai/code/…` remote control). Works fine on a laptop too.

---

## Easiest path — let Claude Code set it up

`xa` ships with an **agent skill** that teaches any Claude Code instance how to
install, configure, and secure-by-default-set-up the package on your machine.
After `pip install xa`, a Claude Code session running on that machine can
guide you through the rest with zero docs-reading.

```bash
pip install 'xa[service]'          # service extra bundles the HTTP server
```

Then, in a Claude Code session on the target machine:

> *"Set up xa on this server so I can manage Claude Code sessions from my phone."*

The [`xa-install`](xa/data/skills/xa-install/SKILL.md) skill will:

- verify system prerequisites (`tmux`, `claude`, `rsync`/`ssh` if needed);
- generate a strong password + captcha key (via `xa gen-secret`);
- write a secure `~/.config/xa/config.toml` and a systemd unit;
- **refuse to expose the HTTP service publicly without auth + TLS guidance**;
- run a health-check and confirm everything is wired up.

Two companion skills live in the repo:

- [`xa-install`](xa/data/skills/xa-install/SKILL.md) — the consumer-facing
  setup skill, shipped inside the wheel so every `pip install xa` gets it.
- [`xa-dev`](.claude/skills/xa-dev/SKILL.md) — for collaborators hacking on
  the codebase (architecture, test conventions, known pitfalls).

## Running `xa` as a phone-accessible server

**This is the highest-leverage use case.** A headless server runs
`xa serve`, you open `https://your-server/api/xa/sessions/{id}` (or a small
web UI) on your phone, and you can list / spawn / resume / kill Claude Code
sessions from anywhere. Each session exposes a `claude.ai/code/…` URL that
resumes the agent right in your phone's browser.

### ⚠️ Security first — this is a code-execution endpoint

A live `xa` session runs `claude`, which can invoke arbitrary shell
commands via its `Bash` tool. Anyone who reaches your `xa` server with
valid credentials can execute code as the server user. Treat the server
like you would an SSH endpoint — **only more carefully**, because the
attack surface is a web request.

`xa` is **secure by default**. When running on a non-loopback interface,
it refuses to start without credentials. You have to pass
`--i-know-its-insecure` to bypass the check.

### The recommended recipe

```bash
# 1. Install (on the server).
pip install 'xa[service]'

# 2. Generate secrets. Don't copy passwords from a tutorial — use these.
export XA_PASSWORD=$(xa gen-secret)
export XA_CAPTCHA_KEY=$(xa gen-secret)

# 3. Start xa, bound locally, captcha-gated.
xa serve \
  --host 127.0.0.1 --port 8010 \
  --username $(whoami) --password-env XA_PASSWORD \
  --captcha
```

Then put a **TLS-terminating reverse proxy in front of port 8010**. Caddy,
nginx, Traefik, Coolify — any of them. Without TLS, HTTP Basic sends your
password in plaintext on every request.

A minimal Caddy block:

```caddy
xa.example.com {
    reverse_proxy 127.0.0.1:8010
}
```

### Defense in depth — things to add

1. **mTLS client certs.** Issue one per device (phone, laptop). Caddy's
   `client_auth { mode require_and_verify }` rejects any connection without
   a valid cert. Removes the "shared password" problem entirely. Sketch:
   ```caddy
   xa.example.com {
       tls {
           client_auth {
               mode require_and_verify
               trusted_ca_cert_file /etc/caddy/xa_ca.pem
           }
       }
       reverse_proxy 127.0.0.1:8010
   }
   ```
   Generate a CA with `step-ca` or plain `openssl`; export per-device
   `.p12` bundles to install on each device. Once you have this, you can
   run `xa serve --i-know-its-insecure` (Basic auth becomes redundant —
   the proxy already authenticated the client) or keep Basic as a second
   factor.

2. **Obscure the hostname.** Don't call it `xa.example.com`. Random
   subdomain + private DNS is cheap friction for bots.

3. **Systemd hardening.** Run as a non-privileged user with
   `ProtectSystem=strict`, `NoNewPrivileges=true`, and a tight
   `ReadWritePaths=` list.

4. **Firewall.** Bind only to loopback; let the reverse proxy be the only
   thing on a public interface.

5. **Rotate credentials.** `xa gen-secret` is a one-liner; rotate monthly
   and restart the service.

### Systemd unit

```ini
# /etc/systemd/system/xa.service
[Unit]
Description=xa Claude Code session server
After=network.target

[Service]
Type=simple
User=xa
WorkingDirectory=/home/xa
Environment="XA_PASSWORD=<paste the output of `xa gen-secret`>"
Environment="XA_CAPTCHA_KEY=<paste a second `xa gen-secret`>"
ExecStart=/path/to/venv/bin/xa serve \
  --host 127.0.0.1 --port 8010 \
  --username xa --password-env XA_PASSWORD \
  --captcha
Restart=on-failure
# Hardening — uncomment and tune if your setup allows:
# ProtectSystem=strict
# ProtectHome=true
# NoNewPrivileges=true
# PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now xa
curl -u xa:$XA_PASSWORD http://127.0.0.1:8010/health
```

### What happens if you try to skip the guardrails

```console
$ xa serve --host 0.0.0.0 --port 8010
error: refusing to bind to a non-loopback interface ('0.0.0.0') without
       --username/--password.
       Anyone who can reach this port could spawn arbitrary shell
       commands via Claude Code sessions on this host.

       Fix (recommended): add credentials + captcha.
         xa gen-secret                  # use for password + XA_CAPTCHA_KEY
         …

       Bypass (only when an outer layer already gates access,
       e.g. mTLS in the reverse proxy): --i-know-its-insecure
```

The flag exists, but it's named so the error is honest.

---

## The rest of the CLI

Already have some Claude Code sessions on this machine? List them:

```console
$ xa list
ID        HOST   STATE            MOD      TURNS  CWD                      URL / FIRST_MSG
--------  -----  ---------------  -------  -----  -----------------------  ---------------------------------------------------
7b875f44  local  live             2s ago   494    /root                    https://claude.ai/code/session_01ALoSc...
b3366c60  local  live             20h ago  725    /root/py/proj            https://claude.ai/code/session_012W14x...
4e738fb5  local  transcript_only  18h ago  15     /root                    my apps.thorwhalen.com has an admin level app…
```

Dig into one:

```bash
xa info 7b875f44                # full record + forensics
xa archive forensics 7b875f44   # what was it doing when it last ran a tool?
xa history --search "bridge URL"   # cross-project prompt grep
```

Or walk through them interactively:

```bash
xa pick
```

### Commands

| Command | Purpose |
| --- | --- |
| `xa list` | Table of sessions across configured hosts |
| `xa info <id>` | One session — metadata + forensics |
| `xa history [--search X]` | Grep `~/.claude/history.jsonl` |
| `xa pick` | Interactive list → number → action |
| `xa spawn <cwd>` | Start `claude` in tmux, print phone URL |
| `xa resume <id>` | `claude --resume` in a new tmux pane |
| `xa kill <id>` | End a live session |
| `xa archive list` | Postmortem of every session `xa` spawned |
| `xa archive log <id>` | Raw pane log |
| `xa archive forensics <id>` | Death reason + last tool use |
| `xa sync [--host H]` | Refresh SSH-host caches |
| `xa serve …` | Run the HTTP service (secure-by-default) |
| `xa gen-secret` | Cryptographically strong random hex string |

Every command supports `--help`; every listing supports `--json-out`.

## Configuration

Config lives at `~/.config/xa/config.toml` (respects `$XDG_CONFIG_HOME`;
override with `$XA_CONFIG`). Missing file → implicit `LocalHost`.

```toml
[settings]
cache_dir = "~/.cache/xa/remotes"
stale_threshold_sec = 3600
claude_bin = "claude"
tmux_bin = "tmux"

[hosts.local]
kind = "local"

# Another machine via SSH. Uses your ~/.ssh/config.
[hosts.devbox]
kind = "ssh"
host = "devbox"
user = "deploy"
remote_claude_home = "~/.claude"

# Another xa server over HTTPS.
[hosts.phone_server]
kind = "http"
base_url = "https://xa.example.com"
auth = "basic"
username = "me"
password_env = "XA_PHONE_PW"   # secret stays in env, not TOML
```

After editing, `xa sync` pulls SSH hosts into the local cache.

## Python API

Everything the CLI does is available as a library:

```python
import xa

# List all sessions on this machine.
for s in xa.list_sessions(limit=10):
    print(s.id[:8], s.state, s.cwd, s.url)

# Multi-host.
hosts = xa.load_hosts()
for s in xa.list_sessions(hosts=list(hosts.values())):
    print(s.host, s.id[:8], s.url)

# Spawn / resume / kill.
local = xa.LocalHost()
result = local.spawn("my-session", cwd="/tmp")
print(result.url)

# Read the archive.
events = xa.default_events_store()
panes  = xa.default_pane_store()
for rec in xa.records(events, panes):
    print(rec.id, rec.gone_reason, rec.url)
```

For embedding the HTTP service in a larger app:

```python
from xa.service import build_api, make_basic_auth, Captcha
import os

api = build_api(
    auth=make_basic_auth("me", os.environ["XA_PASSWORD"]),
    captcha=Captcha(key=os.environ["XA_CAPTCHA_KEY"]),
)
outer_app.mount("/api/xa", api)
```

## HTTP API surface

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/sessions` | List (query: `project`, `state`, `include_forks`, `limit`) |
| `POST` | `/sessions` | Create a tmux+claude session; up to 120 s for the bridge URL |
| `DELETE` | `/sessions/{name}` | Kill (captcha-gated when `--captcha`) |
| `GET` | `/sessions/{id}/info` | Metadata + forensics + pane tail |
| `POST` | `/sessions/{id}/resume` | `claude --resume` in a new pane |
| `GET` | `/archive` | Postmortem list |
| `GET` | `/archive/{id}/forensics` | Rich forensics |
| `GET` | `/archive/{id}/log` | Pane log (`?tail_kb=`) |
| `GET` | `/captcha` | 4-letter challenge (only with `--captcha`) |
| `GET` | `/health` | Liveness |

## How it compares

| Feature | `xa` | [`cc-sessions`](https://github.com/chronologos/cc-sessions) | `claude --resume` | [`claude-history-viewer`](https://marketplace.visualstudio.com/items?itemName=agsoft.claude-history-viewer) |
| --- | :---: | :---: | :---: | :---: |
| Cross-project discovery | ✓ | ✓ | per-project | ✓ |
| Cross-machine discovery | ✓ (SSH + HTTP) | ✓ (SSH) | ✗ | ✗ |
| Live tmux / spawn / kill | ✓ | ✗ | ✗ | ✗ |
| Bridge URL resolution (phone) | ✓ | ✗ | N/A | ✗ |
| Postmortem / death reason | ✓ | ✗ | ✗ | ✗ |
| HTTP API surface | ✓ | ✗ | ✗ | ✗ |
| Python library | ✓ | ✗ | — | ✗ |
| Language | Python | Rust | — | TypeScript (VS Code) |

Short version: cc-sessions pioneered cross-machine listing; the VS Code
extension pioneered transcript search with a UI; `xa` unifies those with
live CRUD + bridge URL resolution + postmortem, and exposes all of it as
both a CLI and a Python library you can embed.

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
 │        CLI / API / phone URL           │
 └────────────────────────────────────────┘
```

## Development

Layered architecture in [`misc/docs/design.md`](misc/docs/design.md);
phased plan in [`misc/docs/roadmap.md`](misc/docs/roadmap.md); research
on Claude Code internals in [`misc/docs/research.md`](misc/docs/research.md).

```bash
git clone https://github.com/thorwhalen/xa.git
cd xa
pip install -e '.[dev,service]'
pytest -q                              # ~100 tests, ~8 seconds
XA_RUN_INTEGRATION=1 pytest -q         # also runs the real-claude spawn test
```

The [`xa-dev`](.claude/skills/xa-dev/SKILL.md) skill is picked up
automatically when you're working on this repo (architecture overview,
testing conventions, known pitfalls).

## License

MIT
