# Roadmap — xa

Ordered phases toward a useful v1 and beyond. Each phase is a small, shippable
slice with a visible outcome. "Done" means: merged, tested, documented in
the README.

**Status at 0.0.6:** phases 1 through 8 landed; 98 tests passing. Remaining
work is the cross-cutting edualc migration plus v1 polish (see bottom).

---

## Phase 1 — Read-only local discovery (the foundation)

**Outcome:** `python -c "from xa import list_sessions; [print(s) for s in list_sessions()]"` prints every session Claude Code has ever run on this machine, with project, summary, turn count, mtime.

- [x] `xa.claude_fs` — transcript iteration, slug ↔ cwd, metadata extraction, forensics, history iterator.
- [x] Minimal `xa.sessions.Session` dataclass (transcript-only state).
- [x] `xa.sessions.list_sessions()` — local only, no host abstraction yet.
- [x] Unit tests with a fixture `~/.claude/` tree under `tests/fixtures/`.
- [x] README: quickstart with the 1-liner above.

Deliverables: `xa/claude_fs.py`, `xa/sessions.py` (thin), `tests/`.

---

## Phase 2 — CLI surface

**Outcome:** `xa list` works on the command line and looks a bit like `cc-sessions --list`.

- [x] `xa.cli` entry point (`argh` or similar).
- [x] `xa list [--project X] [--limit N]` → rich table.
- [x] `xa info <id>` → full forensics dump.
- [x] `xa history [--search PHRASE]` → grep over `~/.claude/history.jsonl`.
- [x] `[project.scripts]` entry in `pyproject.toml`.

Deliverables: `xa/cli.py`, console script wired up.

---

## Phase 3 — Live local sessions (tmux + bridge URL)

**Outcome:** `xa spawn <project>` starts a detached claude-in-tmux session and prints the phone URL. `xa list` shows it as `state=live`.

- [x] `xa.tmux` — pure tmux wrappers.
- [x] `xa.claude_cli` — spawn / resume / `resolve_bridge_url` / readiness handshake.
- [x] Wire live state into `Session`: merge transcript records with live tmux + ephemeral session-file data.
- [x] `xa spawn`, `xa resume <id>`, `xa kill <id>`.
- [x] Integration tests gated by `XA_RUN_INTEGRATION=1`.

Deliverables: `xa/tmux.py`, `xa/claude_cli.py`, session merging logic.

---

## Phase 4 — Archive / postmortem

**Outcome:** Every session spawned via `xa` is recorded; dead sessions are classified with a death reason and accessible via `xa archive`.

- [x] `xa.store` — minimal JSONL store (no `dol` dep yet; keep it tiny).
- [x] `xa.archive` — event log, reconciler, `classify_death`, pane-log pipe.
- [x] `xa archive [--host X]` and `xa archive log <id>`.
- [x] Background reconcile loop (thread or explicit command; avoid adding a daemon requirement).

Deliverables: `xa/store.py`, `xa/archive.py`.

---

## Phase 5 — HTTP service (edualc replacement)

**Outcome:** `xa serve` runs a FastAPI app that exposes the same routes edualc exposes today, backed by the `xa` primitives. Edualc's `server.py` can import from `xa` and shrink to a thin mounter.

- [x] `xa.service.build_api(...)` — parametrised FastAPI.
- [x] Auth dependency injection (HTTP Basic reference impl ships; enlace adapter is an extra).
- [x] Optional captcha dep, reproducing edualc's HMAC-signed pattern.
- [x] `xa serve` CLI command.
- [x] Port edualc's static `index.html` into `xa/webui/` (or leave it in edualc; decide per §5 of design.md).

Deliverables: `xa/service.py`, `xa/webui/` (optional).

---

## Phase 6 — Remote hosts: SSH

**Outcome:** Declaring an SSH host in `~/.config/xa/config.toml` makes `xa list` show sessions from that host too. Resume dispatches `ssh <host> claude --resume …`.

- [x] `xa.hosts.Host` protocol + `LocalHost`.
- [x] `xa.hosts.SSHHost` — rsync-pull cache, ssh-exec for actions.
- [x] `xa sync` — force-refresh remote caches.
- [x] `xa.config` — TOML loader for `[hosts.*]` + `[settings]`.
- [x] `list_sessions(hosts=...)` accepts multiple hosts; results carry `host` field.

Deliverables: `xa/hosts/{__init__.py,local.py,ssh.py}`, `xa/config.py`.

---

## Phase 7 — Remote hosts: HTTP

**Outcome:** Declaring an HTTP host (another machine running `xa serve`) in config also works. Now the Hetzner server and the Mac both surface in the same listing.

- [x] `xa.hosts.HTTPHost` — client against the `/api/xa/` surface.
- [x] Auth pluggability (Basic, bearer, enlace cookie).
- [x] Caching strategy for transcripts accessed over HTTP (materialize via API vs delegate to rsync; see open question in design.md §9).

Deliverables: `xa/hosts/http.py`.

---

## Phase 8 — TUI picker (fzf-style)

**Outcome:** `xa` (no args) opens an interactive picker across all configured hosts. Enter resumes; `/` filters; `Ctrl-S` full-text searches transcripts.

Deliberately last — a non-TUI v1 is genuinely useful, and TUI frameworks are a sink for time. Revisit after real usage of phases 1-7 to see what the picker actually needs.

- [x] Pick framework (`textual`, `prompt_toolkit`, or `rich` prompts) — see open question §9.
- [x] Fuzzy filter, fork tree display, transcript preview, search mode.

Deliverables: `xa/tui.py` (optional extra).

---

## Cross-cutting: edualc migration

Runs in parallel with phases 3-5 (`xa` must exist as a library before edualc
can depend on it):

- [x] Phase A — extract pure logic from `edualc/server.py` into `xa`.
- [x] Phase B — swap edualc routes for `xa.service.build_api(...)`.
- [x] Phase C — add remote hosts to the edualc-mounted `xa` instance.
- [x] Phase D — decide whether to retire `edualc` or keep it as the user-facing web app with `xa` as the engine.

---

## Release cadence

- `0.0.6` — initial scaffold (this).
- `0.1.0` — phases 1-2 done. Library + `xa list` CLI. First real release.
- `0.2.0` — phase 3. Live sessions + bridge URL. Useful on the phone.
- `0.3.0` — phase 4-5. Archive + HTTP service. Edualc can start migrating.
- `0.4.0` — phase 6. SSH hosts.
- `0.5.0` — phase 7. HTTP hosts.
- `1.0.0` — phase 8, stable public API, documented extension points. Drop
  `0.0.x` transitional compatibility shims.

---

## Not on the roadmap (yet)

- Bridge protocol implementation / WebSocket client.
- mTLS / client-cert issuance tooling.
- systemd / nohup / screen backends (tmux only for v1).
- Session transcript editing or replay.
- A native mobile app (web UI is enough).
