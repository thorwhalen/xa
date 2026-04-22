# Research — remote Claude Code sessions

Notes toward `xa`, a toolkit for discovering, listing, resuming, and managing
Claude Code sessions across multiple projects and multiple machines. This file
is the raw intelligence; architectural synthesis goes in `design.md`.

---

## 1. The Claude Code CLI itself — what's built-in

### Resume / continue commands

| Command | Behavior |
| --- | --- |
| `claude -c` / `claude --continue` | Jumps straight back into the most recent session in the current directory |
| `claude -r` / `claude --resume` | Opens an interactive picker of recent sessions (current project, current machine) |
| `claude -r "<id-or-name>"` | Resumes a specific session by ID or name directly (non-interactive) |
| `claude -n "my-name"` / `--name` | Names a session on start so it's easy to find later |
| `/resume` (inside a session) | Opens the same picker as `claude -r` |
| `/rename` (inside a session) | Changes the session's name; the new name is also shown on the prompt bar |

### Critical limitation

The built-in picker is **per-project, per-machine**. It reads
`~/.claude/projects/<slugified-project-path>/*.jsonl` on the current host only.
It does **not** see sessions on your other machines, nor sessions in other
projects on the same machine.

There is **no official `claude --list` flag** (despite claims in some
third-party blogs). The closest official answer is `claude --resume` with no
arguments.

### Where the data lives

- `~/.claude/projects/<slugified-project-path>/<session-uuid>.jsonl` — one file
  per session, full transcript (append-only JSONL, one event per line).
- `~/.claude/history.jsonl` — global append-only log of every prompt you've
  ever sent. Each line carries the project path + session ID. Useful for
  cross-session full-text search of past prompts.
- `~/.claude/sessions-index.json` — session summary index.
- `~/.claude/sessions/<pid>.json` — **per-process ephemeral** session metadata
  while a claude process is alive. Contains `pid`, `sessionId`,
  `bridgeSessionId`, `cwd`, `startedAt`, `version`, `kind`, `entrypoint`.
  Deleted when claude exits. (This is what `edualc` reads to get the
  remote-control URL.)

Everything is **file-based** → `rsync`-friendly for multi-machine sync.

### Project-path slugification

`cwd` → `<slugified>` rule observed in practice: replace `/` with `-`, with a
leading `-` kept. Example: `/root/py/proj/tt/glossa` becomes
`-root-py-proj-tt-glossa`. Any code walking projects must match this exact
encoding to map slug ↔ cwd.

### Session JSONL contents (inferred from transcript-forensics code)

Each line is a JSON event. Relevant shapes when walking from the end for a
"what was happening" postmortem:

```jsonc
{
  "message": {
    "role": "assistant" | "user",
    "content": [
      { "type": "text", "text": "..." },
      { "type": "tool_use", "name": "Bash", "input": { "command": "..." } },
      { "type": "tool_result", "content": "..." }
    ]
  }
}
```

The marker `"Request interrupted by user for tool use"` appears in
`tool_result` content when the orchestrator aborts — **not only** on human
ESC, but **also** on bridge WebSocket resets (phone standby / reconnect),
remote-stop, and parent-process cancellation. Can't be used alone to
diagnose "user intent".

The clean-exit marker `"Resume this session with:"` appears at the tail of
the pane (not the JSONL) when the session terminates via `/exit`, Ctrl-D, or
app shutdown.

### Remote-control URL

Format: `https://claude.ai/code/<bridgeSessionId>`. The `bridgeSessionId`
values already start with `session_` — do not prepend anything. This URL is
enabled by `/remote-control` inside the session; the `bridgeSessionId` lands
in the per-pid session file as soon as the bridge registers.

---

## 2. cc-sessions (chronologos/cc-sessions) — the closest existing tool

### What it is

A Rust CLI (MIT-licensed) that aggregates Claude Code sessions across all
projects and (via rsync over SSH) across multiple machines. Explicitly solves
the "which machine was I on when I fixed that thing" problem.

Repo: https://github.com/chronologos/cc-sessions — Rust 99.4%, Cargo + Just
build system, Rust 1.85+ (edition 2024). Pre-built binaries for macOS
(ARM64/Intel) and Linux (x86_64/ARM64).

### Commands / flags

```
# Interactive (default)
cc-sessions                      # fuzzy picker across everything
cc-sessions --fork               # fork mode instead of resume
cc-sessions --project <name>     # filter (case-insensitive)
cc-sessions --debug              # show session ID prefixes

# Non-interactive
cc-sessions --list               # plain table
cc-sessions --list --count 30
cc-sessions --list --include-forks

# Remote sync
cc-sessions --sync               # force sync before listing
cc-sessions --no-sync            # skip auto-sync
cc-sessions --sync-only          # sync and exit
cc-sessions --strict             # fail on sync errors (default: warn-and-continue)
```

### Interactive UI

Columns: `CRE MOD MSG SOURCE PROJECT SUMMARY`
(creation time, modification time, message count, source, project, summary).

Keys: Enter = resume · Esc = clear search / exit · → = explore forks ·
← = back · Ctrl-S = full-text search across session transcripts.

`★` prefix marks sessions that have been renamed (custom title).

### Session discovery

1. Scan `~/.claude/projects/` for `.jsonl` files whose basename is a valid UUID.
2. Open each file and pull metadata from its contents: `cwd`, first user
   message, summary, custom title (from `/rename`).
3. Sort by filesystem mtime (modification timestamp).
4. Filter out empty or non-session files.

### Fork detection

Forked sessions carry a `forkedFrom.sessionId` reference inside the JSONL.
The tool builds a parent → child tree and displays forks nested under their
parent in the picker. `--fork` resumes in fork-mode (new session that
preserves history).

### Remote config: `~/.config/cc-sessions/remotes.toml`

Struct definitions (from `src/remote.rs`):

```rust
pub struct RemoteConfig {
    pub host: String,                  // SSH host/alias
    pub user: Option<String>,          // optional, for raw IPs
    pub projects_dir: Option<String>,  // override path to ~/.claude/projects
}

pub struct Settings {
    pub cache_dir: String,       // where to keep synced copies
    pub stale_threshold: u64,    // seconds before a remote is re-synced
}
```

Example:

```toml
[remotes.devbox]
host = "devbox"

[remotes.workstation]
host = "192.168.1.100"
user = "ec2-user"

[settings]
cache_dir = "~/.cache/cc-sessions/remotes"
stale_threshold = 3600
```

### Sync mechanism

Whole-directory **rsync over SSH**:

```
rsync -a -z --delete -e ssh <user@host>:~/.claude/projects/ <cache_dir>/<name>/
```

- `-a` archive (preserves timestamps, permissions — important because the
  listing sorts by mtime)
- `-z` compression (bigger transcripts)
- `--delete` mirrors remote deletions
- trailing `/` on the source is deliberate ("copies contents, not the
  directory itself")
- no selective file fetching — whole projects tree every sync

Result: local cache looks exactly like each remote's `~/.claude/projects/`,
so the same discovery/parsing code works across local and remote without
special cases.

### Source-file layout (`src/`)

| File | Purpose |
| --- | --- |
| `main.rs` | Entry point, CLI dispatch |
| `session.rs` | Core `Session` struct + JSONL loading + discovery |
| `claude_code.rs` | `claude` binary invocation / resume plumbing |
| `interactive_state.rs` | TUI picker state machine |
| `message_classification.rs` | Transcript message type handling |
| `remote.rs` | `remotes.toml` parsing + rsync-sync + cache lifecycle |

### `Session` struct (from `src/session.rs`)

```rust
pub struct Session {
    pub id: String,
    pub project: String,
    pub project_path: String,
    pub filepath: PathBuf,
    pub created: SystemTime,
    pub modified: SystemTime,
    pub first_message: Option<String>,
    pub summary: Option<String>,
    pub name: Option<String>,        // customTitle from /rename
    pub tag: Option<String>,
    pub turn_count: usize,
    pub source: SessionSource,        // local / remote-host
    pub forked_from: Option<String>,  // parent session ID
}
```

### What cc-sessions does NOT do

- No **live** (running) session visibility — it lists transcript files, not
  tmux/process state. So "which of my sessions is currently running on the
  Hetzner box" is out of scope. `edualc`'s tmux/pid model covers that side.
- No postmortem / forensics view. Files that belong to dead sessions look the
  same as files that belong to live ones; death-reason taxonomy is absent.
- No remote-control URL resolution (no `bridgeSessionId` awareness). It's
  strictly a resume tool, not a "grab the mobile URL" tool.
- Auth is SSH-only (relies on `~/.ssh/config`); no HTTPS API layer.
- Writes no events (purely read-only over the sessions tree).

---

## 3. Other third-party tools

### agsoft.claude-history-viewer (VS Code extension)

Sidebar showing all conversations with diffs, search, one-click resume.
Scope: single machine. Mentioned in community writeups.

### Custom `/history` slash command

Documented in community writeups, not part of Claude Code itself. Lives at
`~/.claude/commands/history.md` and greps `~/.claude/history.jsonl`
directly to find past prompts by phrase. Good example of how far you can
get with pure file access + a bit of grep glue.

---

## 4. The edualc precedent (our own)

`edualc` is the Claude Code session manager shipped on `tw_platform` today.
It solves an orthogonal problem from cc-sessions — **live** tmux-hosted
sessions on one remote host — and uses an HTTP API + browser UI rather than
a CLI + SSH.

Detailed notes: `/root/edualc_notes.md` (outside this repo).

Key overlap-and-contrast points worth carrying into `xa`:

| Aspect | cc-sessions | edualc |
| --- | --- | --- |
| Transport | rsync over SSH (pull) | HTTPS API on the remote host (push on create) |
| Session visibility | Transcript files on disk | Live tmux sessions + archive log |
| Remote URL | not handled | primary feature (`bridgeSessionId` resolution + handshake) |
| Lifecycle actions | resume, fork | create, kill (captcha), resume, inspect pane |
| Auth | SSH keys | HTTP Basic (single user) |
| Postmortem | none | full: event log, pane pipe, transcript forensics, death taxonomy |
| UI | TUI picker (fzf-style) | web UI (dark, single-page, vanilla JS) |
| Language | Rust | Python (FastAPI) + HTML |
| Scope | cross-project, cross-machine | single host, single user |

Neither covers the full matrix. `xa` is the place to unify them.

---

## 5. Capabilities summary — what a "reusable package" needs

Treating "everything useful for doing remote sessions" as the target scope,
here are the primitive capabilities surfaced by the tools above:

### Discovery
- Enumerate local transcript files in `~/.claude/projects/`
- Enumerate local ephemeral session files in `~/.claude/sessions/`
- Enumerate remote transcript trees (via sync into a local cache)
- Enumerate live tmux sessions running `claude` on a host (local or remote)
- Correlate: live pid ↔ ephemeral session file ↔ transcript file ↔ tmux session name

### Metadata extraction (per session)
- `sessionId`, `bridgeSessionId`, `pid`, `cwd`, `startedAt`, `version`
- First user message, last assistant text, summary, custom title, tag
- `forkedFrom.sessionId`
- Turn count, file size, mtime, ctime
- Last tool use (name, input), last tool result, exit code
- Clean-exit marker presence in pane tail
- Bridge-interrupt marker presence in transcript tail

### Lifecycle actions
- Create tmux session running `claude` from a given cwd (+ dismiss trust prompt, send `/remote-control`)
- Resume by session ID (`claude --resume <id>`)
- Fork (new session preserving history)
- Kill tmux session (optionally captcha-gated)
- Capture pane (tail)
- Pipe pane to file (for postmortem)

### Remote-host abstraction
- Host definition (SSH alias or host+user)
- Projects-dir override per host
- Transport: SSH exec (for running `claude --resume`, tmux commands)
- Transport: rsync pull (for bulk transcript sync)
- Transport: HTTPS API (for edualc-style live CRUD)
- Cache directory + staleness policy
- Auth: SSH keys (dominant), HTTP basic, client certs (mTLS sketch in edualc)

### URL / bridge handling
- Extract `bridgeSessionId` from `~/.claude/sessions/<pid>.json`
- Fallback: regex-scrape pane capture for `https://claude.ai/code/session_…`
- Auto-issue `/remote-control` when main prompt visible and "remote control active" absent
- Auto-dismiss "trust this folder" on first start

### Archive / postmortem
- Append-only JSONL event log (created, url_acquired, gone)
- Per-session pane log via `tmux pipe-pane -o`
- Reconciler that diffs live list vs archive and emits `gone` events
- Death-reason classifier: clean_exit, abrupt, interrupted, tool_crash,
  replaced, missing
- Death-time estimator (pane log mtime >> reconcile run time)

### Search / listing
- Cross-project list with columns (time, project, turns, summary)
- Fuzzy picker (CLI) / tabular list
- Full-text transcript search (cc-sessions Ctrl-S)
- History prompt search (grep `~/.claude/history.jsonl`)
- Filter by project, by host, include/exclude forks

### Interfaces
- CLI (interactive TUI + non-interactive table for scripting)
- HTTP API (FastAPI pattern from edualc, for browser / mobile clients)
- Optional web UI (edualc shows a minimal single-file pattern that works)

---

## 6. Questions still open for `xa`

Deliberately not answered here — belong in `design.md`:

- **Python vs Rust.** cc-sessions is Rust for speed on large trees. Is Python
  fast enough for the same task, or do we need pyo3 / a Rust core?
- **Dependency on cc-sessions vs reimplement.** Use cc-sessions as an
  external binary that `xa` shells out to? Rewrap it via pyo3? Reimplement
  in Python for tighter integration with the storage / auth primitives we
  already use (`dol`, enlace)?
- **Storage abstraction.** Keep the JSONL event log, or swap for a `dol`-style
  store so backends (sqlite, redis, s3) are pluggable from day 1?
- **Transport plurality.** cc-sessions picks rsync-over-SSH; edualc picks
  HTTPS. Should `xa` support both, and if so, is the `Host` abstraction
  transport-agnostic or do we split `SSHHost` / `HTTPHost`?
- **Live vs archive unification.** cc-sessions only sees transcripts;
  edualc's live data is ephemeral (session file + tmux). Do we merge these
  into one `Session` record, or keep them typed apart (`RunningSession`,
  `ArchivedSession`)?
- **Auth model.** cc-sessions leans on SSH keys end-to-end. edualc is HTTP
  Basic. The platform (enlace) supports real multi-user auth. Which does
  `xa` default to, and which does it make pluggable?
- **UI surfaces.** CLI-first like cc-sessions, or API-first like edualc
  (with CLI on top)? Both answers are defensible.

---

## Sources

1. [Claude Code CLI reference](https://docs.claude.com/en/docs/claude-code/cli-reference) — official docs
2. [chronologos/cc-sessions](https://github.com/chronologos/cc-sessions) — Rust CLI, MIT
3. [agsoft.claude-history-viewer](https://marketplace.visualstudio.com/items?itemName=agsoft.claude-history-viewer) — VS Code extension
4. `/root/edualc_notes.md` — local notes on the edualc Python server (mounted at `/api/edualc/` on `tw_platform`)
5. `/opt/tw_platform/apps/edualc/server.py` — the edualc source as deployed
