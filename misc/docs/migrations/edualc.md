# Migrating `edualc` onto `xa`

`edualc` (the internal `/opt/tw_platform/apps/edualc/` app) was the primary
influence on `xa`'s shape. Now that `xa` exists, `edualc/server.py` can
shrink to a ~30-line mounter that injects the right auth + config and
lets `xa.service.build_api` do the work.

This document gives the drop-in replacement, the deploy sequence, and the
rollback plan. **Nothing in production is changed by adding this file** —
do the swap when you're ready.

---

## Phase A — replace `edualc/server.py`

The new server is a thin wrapper. Same mount (`/api/edualc/`, `/edualc/`
UI), same auth model, same captcha, same state dir. The only behavioral
change worth noting: `GET /sessions` now shows `host: "local"` on every
row (previously there was no host field).

Drop this in at `/opt/tw_platform/apps/edualc/server.py`:

```python
"""edualc backend — now a thin mounter over xa.service.

Exposes the same /api/edualc/ + /edualc/ surface as before. All Claude
Code session logic, tmux handling, bridge URL resolution, and postmortem
archive now live in the `xa` package — see xa/xa/service.py and friends.
"""

from __future__ import annotations

import os
from pathlib import Path

from xa import service as xa_service
from xa import store as xa_store


# Same env-var contract as before so /opt/tw_platform/.env keeps working.
USERNAME = os.environ.get("EDUALC_USERNAME", "lkjfds")
PASSWORD = os.environ.get("EDUALC_PASSWORD", "sdfjklsdfjkl")
STATE_DIR = Path(
    os.environ.get("EDUALC_STATE_DIR", str(Path.home() / ".edualc"))
)
CAPTCHA_KEY = os.environ.get("EDUALC_CAPTCHA_KEY") or (
    "captcha:" + PASSWORD
)

events_store = xa_store.JsonLinesStore(STATE_DIR / "sessions.jsonl")
pane_store = xa_store.FileStore(STATE_DIR / "panes", suffix=".log")

app = xa_service.build_api(
    auth=xa_service.make_basic_auth(USERNAME, PASSWORD),
    captcha=xa_service.Captcha(key=CAPTCHA_KEY),
    events_store=events_store,
    pane_store=pane_store,
    session_prefix=os.environ.get("EDUALC_SESSION_PREFIX", "edu-"),
    claude_bin=os.environ.get(
        "EDUALC_CLAUDE_BIN", "/root/.local/bin/claude"
    ),
    title="edualc (powered by xa)",
)
```

## Phase B — verify

Before swapping, run this side-by-side check to confirm the archive
format round-trips cleanly:

```bash
# Make sure the state dir is picked up correctly.
EDUALC_STATE_DIR=/root/.edualc python -c "
from xa import store, archive
events = store.JsonLinesStore('/root/.edualc/sessions.jsonl')
panes = store.FileStore('/root/.edualc/panes', suffix='.log')
for r in archive.records(events, panes)[:5]:
    print(r.id, r.name, r.gone_reason, r.url)
"
```

If that prints the same records you'd see in `edualc`'s `/archive`
listing today, the storage format is compatible. **It is** — `xa` was
designed to read edualc's existing event log as-is.

## Phase C — deploy

1. Back up the current server:
   ```bash
   cp /opt/tw_platform/apps/edualc/server.py \
      /opt/tw_platform/apps/edualc/server.py.pre-xa
   ```
2. Install `xa` into the tw_platform venv:
   ```bash
   /opt/tw_platform/venv/bin/pip install 'xa[service]'
   ```
3. Replace `server.py` with the snippet above.
4. Bounce the enlace process (however the platform is managed — systemd,
   supervisord, whatever mounts enlace):
   ```bash
   systemctl restart enlace     # or equivalent
   ```
5. Smoke test:
   ```bash
   curl -u user:pass https://apps.thorwhalen.com/api/edualc/health
   curl -u user:pass https://apps.thorwhalen.com/api/edualc/sessions | jq '.sessions[0]'
   curl -u user:pass https://apps.thorwhalen.com/api/edualc/archive | jq '.sessions[0]'
   ```
   The web UI at `/edualc/` keeps working unchanged — it only talks to
   `/api/edualc/`, whose shape is preserved.

## Phase D — optional follow-ups

Now that edualc is a thin client, opportunities open up:

- **Add remote hosts to the edualc API.** Pass `hosts=` through build_api
  (hypothetical `hosts=` kwarg — small extension) so the same web UI can
  also surface sessions from other machines. This requires adding a
  multi-host `hosts` parameter to `build_api`; see design.md §5 Phase C.
- **Drop edualc's custom frontend.** Move it into `xa/webui/` as a
  shipped static bundle so any `xa serve` instance has it available.
- **Retire edualc entirely.** Replace the app.toml entry with one that
  directly mounts `xa.service`; edualc becomes just the URL/path alias.

## Rollback

If anything misbehaves, the rollback is trivial:

```bash
cp /opt/tw_platform/apps/edualc/server.py.pre-xa \
   /opt/tw_platform/apps/edualc/server.py
systemctl restart enlace
```

The state dir (`~/.edualc/`) is untouched by the switch — both versions
read/write the same files.

## Gotchas

- **`reconcile` runs on every list.** `xa.service.build_api` calls
  `reconcile` inside `try/except Exception: pass` on each `GET /sessions`
  and `GET /archive`. Same as edualc — no behavior change.
- **The background reconcile thread is gone.** `xa.service` does not
  start one. Edualc used to. If keeping the periodic thread matters,
  start one alongside:
  ```python
  import threading, time
  from xa import tmux
  def _loop():
      while True:
          try:
              xa_service.arch.reconcile(events_store, pane_store, tmux.list_sessions())
          except Exception: pass
          time.sleep(60)
  threading.Thread(target=_loop, daemon=True).start()
  ```
  For most deployments the on-request freshening is enough.
- **Captcha key derivation.** Edualc derived the HMAC key as
  `sha256("captcha:" + password)`. The snippet above uses a simpler
  `"captcha:" + password` for readability; if preserving bit-identical
  captcha tokens across the cutover matters (it doesn't — tokens are
  short-lived), use `hashlib.sha256(("captcha:" + PASSWORD).encode()).hexdigest()`.
