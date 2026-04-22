---
name: xa-install
description: Use when the user wants to install, configure, provision, secure, or troubleshoot `xa` — the cross-machine Claude Code session manager. Triggers on "install xa", "set up xa on this server", "configure xa hosts", "start xa serve", "xa from my phone", "make xa accessible from my phone", "secure xa", "xa config", or any request to use xa for the first time. Also use if the user asks why `xa list` is empty, why a remote host isn't showing, why they can't resume a session, or whether their xa deployment is safe.
---

# xa — installation, configuration, and secure deployment

Help the user get `xa` working on their machine, on a server, or across multiple machines — **and make it very hard to deploy it insecurely**. `xa` runs `claude`, which can execute arbitrary shell commands, so a weakly-protected `xa serve` endpoint is a remote code-execution endpoint. Treat every setup decision from that lens.

## Scenarios and what to install

Ask the user (or infer from context) which scenario applies:

- **A. Local use only** — listing / resuming sessions on this one machine. `pip install xa`.
- **B. Phone-accessible server** — headless box runs `xa serve`, phone connects over HTTPS. `pip install 'xa[service]'`. **This is the high-leverage case; it's also the dangerous one.** Follow §Securing the HTTP service below, end to end. Don't shortcut.
- **C. Multi-machine via SSH** — pull another machine's sessions into this one's `xa list`. `pip install xa` (SSH transport uses system `ssh` / `rsync`, no extras).

## 1. Verify system prerequisites

Before declaring install complete:

```bash
command -v tmux   && tmux -V        # ≥3.0 required for live sessions
command -v claude && claude --version
command -v ssh    && command -v rsync    # scenario C only
```

If missing:

- **tmux**: `apt install tmux` / `brew install tmux`. Required to host detached `claude` processes.
- **claude**: point the user at Anthropic's official Claude Code install docs; don't guess URLs.
- **rsync**: `apt install rsync` / `brew install rsync`.

## 2. Smoke test the install

```bash
xa --help                            # should list: list, info, history, spawn, resume, kill, archive, sync, pick, serve, gen-secret
xa list                              # should print a table (or "(no rows)" if this is a fresh machine)
```

If `xa list` crashes, the most common cause is `tmux` missing → fix that before anything else.

## 3. Configure hosts (optional)

Config lives at `~/.config/xa/config.toml` (respects `$XDG_CONFIG_HOME`; override with `$XA_CONFIG`). Missing file is fine — implicit `LocalHost` is used.

Ask the user which hosts they want to connect, then show them the content before writing.

### Add an SSH host (scenario C)

```toml
[hosts.devbox]
kind = "ssh"
host = "devbox"          # an alias in ~/.ssh/config OR a raw hostname/IP
user = "deploy"          # omit if covered by ssh config
remote_claude_home = "~/.claude"
```

```bash
xa sync --host devbox    # first rsync pull
xa list                  # devbox sessions should now show up
```

Sync errors → the remote SSH auth isn't working. Have the user run `ssh devbox whoami` on its own; get that working first. `xa` uses their ssh config and agent — it does not manage keys.

### Add an HTTP host (connect to another xa serve)

```toml
[hosts.phone_server]
kind = "http"
base_url = "https://xa.example.com"
auth = "basic"
username = "me"
password_env = "XA_PHONE_PW"       # secret stays in env, not TOML
```

```bash
export XA_PHONE_PW='…'
xa list --host phone_server
```

## 4. Securing the HTTP service (scenario B)

**This section is non-negotiable. Any deviation must be explained and defended.**

### What's at stake

A request to `POST /sessions` spawns a `claude` process. Claude's `Bash` tool can run shell commands as the user running `xa serve`. An attacker who can reach the endpoint **with valid credentials** can execute arbitrary code as that user. Downstream consequences: file exfiltration, lateral movement, billing abuse through the Anthropic account, and permanent host compromise.

`xa` is **secure by default**: `xa serve` refuses to bind to a non-loopback interface without `--username`/`--password`. That guardrail exists for a reason. Do not suggest `--i-know-its-insecure` unless the user has an mTLS-terminating proxy in front.

### The standard recipe (follow verbatim)

Walk the user through each step. Print commands; don't run destructive ones without explicit confirmation.

```bash
# 1. Install on the server.
pip install 'xa[service]'

# 2. Generate strong secrets with xa gen-secret (do NOT reuse values from this skill).
#    Both should be 64 hex chars. Store them only in systemd Environment= or a secret manager.
XA_PASSWORD=$(xa gen-secret)
XA_CAPTCHA_KEY=$(xa gen-secret)

# 3. Bind to loopback. The reverse proxy is the only thing on a public port.
xa serve \
  --host 127.0.0.1 --port 8010 \
  --username "$USER" --password-env XA_PASSWORD \
  --captcha
```

Then a TLS-terminating reverse proxy in front of `127.0.0.1:8010`. Caddy is the simplest:

```caddy
xa.example.com {
    reverse_proxy 127.0.0.1:8010
}
```

Or nginx:

```nginx
server {
    listen 443 ssl;
    server_name xa.example.com;
    # ssl_certificate, ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

**Without TLS, HTTP Basic sends the password in plaintext on every request.** This is not negotiable. If they don't have TLS yet, fix that before exposing `xa serve`.

### Defense in depth — ratchet up from the recipe

Offer these in order of cost-benefit:

1. **mTLS client certs** (highest-value if the user has 1-3 devices). Caddy can gate with `client_auth { mode require_and_verify }`. Issue one cert per device (phone, iPad, laptop); install each once; Caddy rejects anything else. Removes the "shared password" problem entirely. Sketch:
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
   Generate a CA with `step-ca` or plain `openssl`. Export per-device `.p12` bundles. Once mTLS is in place, `--i-know-its-insecure` becomes defensible (the proxy already authenticated).

2. **Obscurity in the URL** (free). Don't name it `xa.example.com`. Use a random subdomain so scanners can't find it.

3. **Systemd hardening** (free, takes 2 minutes):
   ```ini
   [Service]
   User=xa
   WorkingDirectory=/home/xa
   Environment="XA_PASSWORD=…"
   Environment="XA_CAPTCHA_KEY=…"
   ExecStart=/path/to/venv/bin/xa serve --host 127.0.0.1 --port 8010 \
     --username xa --password-env XA_PASSWORD --captcha
   Restart=on-failure
   ProtectSystem=strict
   ProtectHome=true
   NoNewPrivileges=true
   PrivateTmp=true
   ```
   Run as a dedicated user with a minimal home directory.

4. **Firewall** (free). Confirm the service port is only reachable via the proxy. On a DigitalOcean/Hetzner/AWS box: ensure the cloud firewall has nothing exposing 8010 directly.

5. **Rotate credentials** (free, 1 minute a month). `xa gen-secret` → new value → restart. Captcha tokens invalidate; sessions live-merge unaffected.

### Red flags to refuse

If the user insists on any of these, **don't write the setup for them** — explain the risk, and if they still want it, make them add `--i-know-its-insecure` themselves:

- Binding `0.0.0.0` with no TLS proxy.
- Using a password like `admin`, `password`, or anything from a blog post.
- Writing the password directly into `config.toml` (use env vars).
- Exposing `xa serve` on a public VPS with no firewall rule scoping.
- Running `xa serve` as root.

## 5. Systemd service (Linux headless)

If the user is setting up a long-running server, write this file with the values from step 4.2:

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
ProtectSystem=strict
ProtectHome=true
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now xa
curl -u xa:$XA_PASSWORD http://127.0.0.1:8010/health    # should print {"ok":true}
```

## 6. Embedding in an existing FastAPI app

If the user already has a server stack (enlace, nginx-unit FastAPI, etc.):

```python
from xa.service import build_api, make_basic_auth, Captcha
import os

xa_api = build_api(
    auth=make_basic_auth("me", os.environ["XA_PASSWORD"]),
    captcha=Captcha(key=os.environ["XA_CAPTCHA_KEY"]),
)
outer_app.mount("/api/xa", xa_api)
```

All the security guidance above still applies — the enclosing app must be served over TLS and must not expose `build_api()` without some auth dependency.

## 7. Diagnosing common issues

**`xa list` is empty** → Has the user ever run `claude` on this machine?
```bash
ls ~/.claude/projects/
```
If empty, spawn one to test:
```bash
xa spawn ~/some/project
```

**Remote host has no sessions but should** → Is the cache stale or empty?
```bash
xa sync --host devbox --force
ls ~/.cache/xa/remotes/devbox/projects/
```

**`xa sync` hangs or fails** → SSH itself is the problem:
```bash
ssh devbox 'ls ~/.claude/projects | head'   # must work standalone
```

**`xa serve` errors "refusing to bind"** → The guardrail is doing its job. Work through §4; do not hand them `--i-know-its-insecure` without the mTLS prerequisite.

**`xa serve` warns "XA_CAPTCHA_KEY not set"** → They passed `--captcha` but didn't export the key. Outstanding tokens invalidate on restart. Tell them to `export XA_CAPTCHA_KEY=$(xa gen-secret)` and add it to their systemd `Environment=`.

**"Request interrupted by user" appears in forensics unexpectedly** → This marker also fires on phone-standby bridge resets, not only human ESC. Tell the user not to read it as user intent — consult the pane log tail for the real cause.

## 8. Don't

- Don't guess download URLs for `claude` or other external tools — direct them to the official docs.
- Don't silently write config files without showing the content first.
- Don't put passwords / tokens directly in `~/.config/xa/config.toml`. Use `password_env` / `token_env`.
- Don't expose `xa serve` on a public interface without TLS in front of it.
- Don't run `xa serve` as root.
- Don't suggest `--i-know-its-insecure` unless mTLS or equivalent outer auth is in place. If the user asks for it, ask *why*, and offer the secure recipe first.
- Don't generate credentials "temporarily" and leave them in shell history. Use `xa gen-secret` piped directly into `systemd` / secret manager.

## 9. Verification checklist (complete when all pass)

- [ ] `xa --help` lists all subcommands
- [ ] `xa list` runs without error (even if empty)
- [ ] For each SSH host: `xa sync --host NAME` succeeds
- [ ] For each HTTP host: `xa list --host NAME` returns rows
- [ ] If running the service: the process bound to `127.0.0.1`, not `0.0.0.0`
- [ ] If running the service: TLS-terminating proxy is configured
- [ ] If running the service: `curl -u user:pass http://127.0.0.1:PORT/health` returns `{"ok": true}`
- [ ] If running the service: `curl http://127.0.0.1:PORT/sessions` (no creds) returns `401`
- [ ] If running the service: `curl https://PUBLIC-URL/health` (no creds) returns `401`
- [ ] Credentials came from `xa gen-secret`, not a dictionary word
- [ ] Captcha is enabled (`--captcha`)
- [ ] Secrets live in `systemd Environment=` or a secret manager, not the config file
