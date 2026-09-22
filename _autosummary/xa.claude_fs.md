# xa.claude_fs

Pure filesystem view of `~/.claude/`.

This layer reads Claude Code’s on-disk state and nothing else: no tmux, no
subprocess, no network. It is the substrate every higher layer of `xa`
reads from.

What lives under `~/.claude/` (as of Claude Code 2.x):

- `projects/<slug>/<uuid>.jsonl` — one file per session, full transcript.
  `<slug>` is the cwd with `/` replaced by `-` (leading `-` kept).
  `<uuid>` is the Claude `sessionId`.
- `sessions/<pid>.json` — per-process ephemeral metadata while a
  `claude` process is alive. Holds `pid`, `sessionId`,
  `bridgeSessionId`, `cwd`, `startedAt`, `version`. Deleted when
  the process exits.
- `history.jsonl` — global append-only log of every prompt sent. Each
  line carries `sessionId` + `cwd`.
- `sessions-index.json` — summary index (not yet consumed here).

All functions are read-only. All paths default to `~/.claude/` but accept
a `claude_home` override (set `XA_CLAUDE_HOME` in the env to point at a
fixture tree during testing).

### Functions

| [`encode_project_slug`](#xa.claude_fs.encode_project_slug)(cwd)                       | Encode a cwd into the `~/.claude/projects/` slug form.                     |
|-------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|
| [`ephemeral_session_alive`](#xa.claude_fs.ephemeral_session_alive)(eph, \*[, proc_root])  | Is the process behind an ephemeral session dict actually alive?            |
| [`history_iter`](#xa.claude_fs.history_iter)(\*[, claude_home])                | Yield entries from `~/.claude/history.jsonl` in file order (oldest first). |
| [`iter_ephemeral_sessions`](#xa.claude_fs.iter_ephemeral_sessions)(\*[, claude_home])     | Yield all live ephemeral session dicts.                                    |
| [`iter_project_slugs`](#xa.claude_fs.iter_project_slugs)(\*[, claude_home])          | Yield slugs (directory names) under `~/.claude/projects/`.                 |
| [`iter_transcript_events`](#xa.claude_fs.iter_transcript_events)(path)                   | Yield every event from a transcript JSONL.                                 |
| [`iter_transcript_files`](#xa.claude_fs.iter_transcript_files)(\*[, claude_home, ...])  | Yield transcript JSONL paths, optionally restricted to one project.        |
| [`parse_project_slug`](#xa.claude_fs.parse_project_slug)(slug)                       | Decode a `~/.claude/projects/` slug back to a cwd.                         |
| [`read_ephemeral_session`](#xa.claude_fs.read_ephemeral_session)(pid, \*[, claude_home]) | Read the per-process session file for `pid` if present.                    |
| [`remote_control_at_startup`](#xa.claude_fs.remote_control_at_startup)(\*[, claude_home])   | Is this host set to auto-connect Remote Control for every session?         |
| [`transcript_forensics`](#xa.claude_fs.transcript_forensics)(path)                     | Extract postmortem-relevant facts from the tail of a transcript.           |
| [`transcript_metadata`](#xa.claude_fs.transcript_metadata)(path)                      | Summarise a transcript without holding it all in memory.                   |
| [`transcript_path`](#xa.claude_fs.transcript_path)(cwd, session_id, \*[, ...])    | Return the transcript path for `(cwd, session_id)` if it exists.           |

### Classes

| [`HistoryEntry`](#xa.claude_fs.HistoryEntry)(cwd, project, display, ...)   | One line from `~/.claude/history.jsonl`.                             |
|---------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| [`TranscriptForensics`](#xa.claude_fs.TranscriptForensics)(transcript_path, ...)  | Postmortem facts extracted by walking a transcript from the end.     |
| [`TranscriptMeta`](#xa.claude_fs.TranscriptMeta)(path, session_id, cwd, ...) | Summary of a transcript JSONL, cheap enough to compute for listings. |

### *class* xa.claude_fs.HistoryEntry(cwd, project, display, pasted_contents)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

One line from `~/.claude/history.jsonl`.

### *class* xa.claude_fs.TranscriptForensics(transcript_path, line_count, last_tool_name, last_tool_command, last_tool_exit_code, last_tool_result_tail, final_assistant_text, user_interrupted)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Postmortem facts extracted by walking a transcript from the end.

`user_interrupted` is the raw marker presence — it is **ambiguous**
(also fires on phone-standby bridge drops), so do not derive user intent
from it alone. Callers should corroborate against pane-log tails.

### *class* xa.claude_fs.TranscriptMeta(path, session_id, cwd, project_slug, summary, custom_title, first_user_message, turn_count, forked_from, created, modified, size_bytes)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Summary of a transcript JSONL, cheap enough to compute for listings.

### xa.claude_fs.encode_project_slug(cwd)

Encode a cwd into the `~/.claude/projects/` slug form.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> encode_project_slug('/root/py/proj/tt/glossa')
'-root-py-proj-tt-glossa'
>>> encode_project_slug('/')
'-'
```

### xa.claude_fs.ephemeral_session_alive(eph, , proc_root=PosixPath('/proc'))

Is the process behind an ephemeral session dict actually alive?

The mere presence of `~/.claude/sessions/<pid>.json` proves nothing:
claude removes it on clean exit, but crashes, SIGKILL, and host
reboots leave stale files behind — and a stale file must not surface
as a “live” session (with a dead URL, an unkillable card, and no
postmortem).

Checks, strongest first:

- newer claude versions write `procStart` (the kernel start-time
  ticks) into the file — when present, it must match
  `/proc/<pid>/stat`, which also defeats PID reuse;
- otherwise the pid must exist and look like a claude process
  (`comm == "claude"` for the native binary, or `claude` in the
  cmdline for npm installs running under `node`);
- on platforms without `/proc` (macOS), fall back to signal-0
  existence (no PID-reuse protection, best available).

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### xa.claude_fs.history_iter(, claude_home=PosixPath('/home/runner/.claude'))

Yield entries from `~/.claude/history.jsonl` in file order (oldest first).

Useful for cross-project full-text prompt search without loading
every transcript.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`HistoryEntry`](#xa.claude_fs.HistoryEntry)]

### xa.claude_fs.iter_ephemeral_sessions(, claude_home=PosixPath('/home/runner/.claude'))

Yield all live ephemeral session dicts.

A session file disappears when claude exits, so this reflects
momentary state only. NB: claude deletes the file on *clean* exit
only — a crash / SIGKILL / reboot leaves a stale file behind, so
callers who care about actual liveness must validate each dict with
[`ephemeral_session_alive()`](#xa.claude_fs.ephemeral_session_alive) (or an equivalent predicate).

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.claude_fs.iter_project_slugs(, claude_home=PosixPath('/home/runner/.claude'))

Yield slugs (directory names) under `~/.claude/projects/`.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### xa.claude_fs.iter_transcript_events(path)

Yield every event from a transcript JSONL.

Lines that fail to parse are silently skipped — transcripts are
append-only on a live process, so the tail can be partial.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.claude_fs.iter_transcript_files(, claude_home=PosixPath('/home/runner/.claude'), project_slug=None)

Yield transcript JSONL paths, optionally restricted to one project.

Only files whose stem is a valid UUID are yielded — this filters out
the `memory/` subfolder and other non-session artefacts Claude Code
stores alongside transcripts.

* **Return type:**
  [`Iterator`](https://docs.python.org/3/library/typing.html#typing.Iterator)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

### xa.claude_fs.parse_project_slug(slug)

Decode a `~/.claude/projects/` slug back to a cwd.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

```pycon
>>> parse_project_slug('-root-py-proj-tt-glossa')
'/root/py/proj/tt/glossa'
>>> parse_project_slug('-Users-thorwhalen-tw-server')
'/Users/thorwhalen/tw/server'
```

NB: This transform is lossy — a cwd with literal `-` in segment names
round-trips to a different slug. Claude Code itself has the same
limitation, so we accept it.

### xa.claude_fs.read_ephemeral_session(pid, , claude_home=PosixPath('/home/runner/.claude'))

Read the per-process session file for `pid` if present.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.claude_fs.remote_control_at_startup(, claude_home=PosixPath('/home/runner/.claude'))

Is this host set to auto-connect Remote Control for every session?

Reads `remoteControlAtStartup` from the user `settings.json`.
`None` means “can’t tell” — no file, unreadable, or the key absent
(in which case Claude Code follows its own default, which xa does not
get to observe).

Deliberately only the *user* settings file. Claude Code also honors
the key in project, local and managed settings, and a project-level
`false` outranks a user-level `true` — so a `True` here is
“configured on for this user”, not a guarantee about a given session.
xa uses it to explain a missing URL, never to decide behavior.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`bool`](https://docs.python.org/3/builtins/functions.html#bool)]

### xa.claude_fs.transcript_forensics(path)

Extract postmortem-relevant facts from the tail of a transcript.

Walks from the *end* of the file so we find the most recent
`tool_use` / `tool_result` / assistant `text` without reparsing
the whole transcript.

* **Return type:**
  [`TranscriptForensics`](#xa.claude_fs.TranscriptForensics)

### xa.claude_fs.transcript_metadata(path)

Summarise a transcript without holding it all in memory.

Walks forward, counting turns and capturing the first user message /
summary / custom title / fork pointer / cwd / sessionId. Stops reading
content fields once it has what it needs, but still counts turns to
the end. For very large transcripts, callers who only need counts
should use `iter_transcript_events` directly.

* **Return type:**
  [`TranscriptMeta`](#xa.claude_fs.TranscriptMeta)

### xa.claude_fs.transcript_path(cwd, session_id, , claude_home=PosixPath('/home/runner/.claude'))

Return the transcript path for `(cwd, session_id)` if it exists.

* **Return type:**
  [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)]

```pycon
>>> from pathlib import Path
>>> import tempfile
>>> with tempfile.TemporaryDirectory() as td:
...     home = Path(td)
...     (home / 'projects' / '-foo-bar').mkdir(parents=True)
...     f = home / 'projects' / '-foo-bar' / 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl'
...     _ = f.write_text('')
...     p = transcript_path('/foo/bar', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', claude_home=home)
...     p == f
True
```
