---
name: xa-install
description: Use when the user wants to install, configure, provision, or troubleshoot `xa` — the cross-machine Claude Code session manager. Triggers on "install xa", "set up xa on this server", "configure xa hosts", "start xa serve", "xa config", "xa on my phone", "connect xa to another machine", or any request to use xa for the first time. Also use if the user asks why `xa list` is empty, why a remote host isn't showing, or why they can't resume a session.
---

# xa — installation & setup skill

Help the user get `xa` working on their machine, on a server, or across multiple machines. `xa` is a Python package for listing, spawning, resuming, and killing Claude Code sessions across projects and hosts.

## Decide what to install

Ask the user (or infer from context) which scenario applies:

**A. Local use only** — listing / resuming sessions on this one machine.
```
pip install xa
```

**B. Server / headless** — let a phone or laptop reach this machine's sessions via HTTPS.
```
pip install 'xa[service]'
```

**C. Multi-machine via SSH** — pull another machine's sessions into this one's listing.
```
pip install xa         # (SSH transport uses system ssh/rsync, no extras needed)
```

## Verify system prerequisites

Run these checks before declaring installation complete:

```bash
command -v tmux    && tmux -V       # ≥3.0 required for live sessions
command -v claude  && claude --version
# Only for scenario C (SSH hosts):
command -v ssh && command -v rsync
```

If any are missing:

- **tmux**: `apt install tmux` / `brew install tmux`. Required to host detached `claude` processes.
- **claude**: point the user at Anthropic's Claude Code install docs; don't guess URLs.
- **rsync**: `apt install rsync` / `brew install rsync`.

## Smoke test

```bash
xa --help                            # should list: list, info, history, spawn, resume, kill, serve, sync, pick, archive
xa list                              # should print a table (or "(no rows)" if this is a fresh machine)
```

If `xa list` crashes, the most common cause is tmux missing → fix that before anything else.

## Configure hosts

Config lives at `~/.config/xa/config.toml` (respects `$XDG_CONFIG_HOME`, override with `$XA_CONFIG`). Missing file is fine — one implicit `LocalHost` is used.

Ask which hosts they want to connect, then write the file.

### Local only

No config file needed. Skip.

### Add an SSH host

```toml
[hosts.devbox]
kind = "ssh"
host = "devbox"          # an alias in ~/.ssh/config OR a raw hostname/IP
user = "deploy"          # omit if covered by ssh config
remote_claude_home = "~/.claude"
```

Then:
```bash
xa sync --host devbox    # first rsync pull
xa list                  # devbox sessions should now show up
```

**If `xa sync` errors**: the remote SSH auth isn't working. Have the user try `ssh devbox whoami` on its own; get that working first. `xa` piggybacks on the user's ssh config and agent — it does not manage keys.

### Add an HTTP host (another machine running `xa serve`)

```toml
[hosts.phone]
kind = "http"
base_url = "https://apps.example.com/api/xa"
auth = "basic"
username = "me"
password_env = "XA_PHONE_PW"       # secret stays in env, not TOML
```

Then:
```bash
export XA_PHONE_PW='…'
xa list --host phone
```

## Run the HTTP service

For "my phone / my laptop should see this server's sessions":

```bash
pip install 'xa[service]'
xa serve --host 0.0.0.0 --port 8010 \
         --username me --password "$XA_PW" \
         --captcha
```

Put it behind a TLS-terminating reverse proxy. **Don't expose `xa serve` directly on the internet without one** — the included HTTP Basic + 4-letter captcha are appropriate once TLS is in place, not before.

For embedding in an existing FastAPI app:

```python
from xa.service import build_api, make_basic_auth, Captcha

xa_api = build_api(
    auth=make_basic_auth("me", os.environ["XA_PW"]),
    captcha=Captcha(key=os.environ["XA_CAPTCHA_KEY"]),
)
outer_app.mount("/api/xa", xa_api)
```

### Systemd service (Linux headless)

If the user is setting up a long-running server, offer this snippet (substitute paths and the venv location):

```ini
# /etc/systemd/system/xa.service
[Unit]
Description=xa Claude Code session server
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/home/your-user
Environment="XA_PW=<set-this>"
Environment="XA_CAPTCHA_KEY=<random-hex>"
ExecStart=/path/to/venv/bin/xa serve --host 127.0.0.1 --port 8010 --username me --password ${XA_PW} --captcha
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now xa
curl http://127.0.0.1:8010/health
```

## Diagnosing common issues

**`xa list` is empty** → Are there any sessions to find?
```bash
ls ~/.claude/projects/
```
If empty, the user has never run `claude` on this machine. That's fine — they can spawn one:
```bash
xa spawn ~/some/project
```

**Remote host has no sessions but should** → Is the cache stale or empty?
```bash
xa sync --host devbox --force        # force pull
ls ~/.cache/xa/remotes/devbox/projects/
```

**`xa sync` hangs or fails** → SSH is the blocker, not xa:
```bash
ssh devbox 'ls ~/.claude/projects | head'   # must work standalone
```

**`xa serve` says "xa[service] extra not installed"** → They installed `xa` but not the optional service extra:
```bash
pip install 'xa[service]'
```

**"Request interrupted by user" appears in forensics unexpectedly** → This marker also fires on phone-standby bridge resets, not only human ESC. Tell the user not to read it as user intent — consult the pane log tail for the real cause.

## Don't

- Don't guess download URLs for `claude` or other external tools — direct the user to the official docs.
- Don't silently write config files without showing the user the content first.
- Don't put passwords / tokens directly in `~/.config/xa/config.toml` — use `password_env` / `token_env` referencing environment variables.
- Don't expose `xa serve` on a public interface without TLS in front of it.

## Verification checklist (complete when all pass)

- [ ] `xa --help` lists all subcommands
- [ ] `xa list` runs without error (even if empty)
- [ ] For each configured SSH host: `xa sync --host NAME` succeeds
- [ ] For each configured HTTP host: `xa list --host NAME` returns rows
- [ ] If running the service: `curl http://<bind>:<port>/health` returns `{"ok": true}`
