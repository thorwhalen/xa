# xa.store

Minimal key-value and append-log storage.

This is deliberately thin and stdlib-only — enough to back `xa.archive`’s
event log and per-session pane captures without pulling in `dol`. Two
types:

- `JsonLinesStore` — append-only JSONL. Iterable, multi-writer safe on
  POSIX (one line == one atomic `write`), no random access.
- `FileStore` — dict-like view over a directory of flat files. Keys
  must match a strict allowlist regex so callers can’t escape `root`.

Later phases may swap these for `dol` equivalents without changing
the archive API.

### Functions

| [`default_events_store`](#xa.store.default_events_store)([state_dir])   | The event log at `<state_dir>/events.jsonl`.                                                                                 |
|--------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|
| [`default_pane_store`](#xa.store.default_pane_store)([state_dir])     | Per-session pane logs at `<state_dir>/panes/<id>.log`.                                                                       |
| [`default_revive_store`](#xa.store.default_revive_store)([state_dir])   | Per-pane last-reconnect stamps for [`xa.revive.RateGuard`](xa.revive.html.md#xa.revive.RateGuard). |

### Classes

| [`FileStore`](#xa.store.FileStore)(root, \*[, suffix])   | Directory of files, accessed by key.              |
|----------------------------------------------------------------------------------|---------------------------------------------------|
| [`JsonLinesStore`](#xa.store.JsonLinesStore)(path)            | Append-only JSONL file with an `Iterable` reader. |

### *class* xa.store.FileStore(root, , suffix='')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Directory of files, accessed by key.

Values are `bytes`. Keys must match `[A-Za-z0-9_.-]+` — this
rejects path traversal attempts like `../etc/passwd`.

#### path_for(key)

Public path accessor (for callers that need to hand the path to tmux).

* **Return type:**
  [`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)

### *class* xa.store.JsonLinesStore(path)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Append-only JSONL file with an `Iterable` reader.

```pycon
>>> import tempfile, pathlib
>>> with tempfile.TemporaryDirectory() as td:
...     s = JsonLinesStore(pathlib.Path(td) / 'log.jsonl')
...     s.append({'a': 1})
...     s.append({'b': 2})
...     list(s) == [{'a': 1}, {'b': 2}]
True
```

### xa.store.default_events_store(state_dir=PosixPath('/home/runner/.xa'))

The event log at `<state_dir>/events.jsonl`.

* **Return type:**
  [`JsonLinesStore`](#xa.store.JsonLinesStore)

### xa.store.default_pane_store(state_dir=PosixPath('/home/runner/.xa'))

Per-session pane logs at `<state_dir>/panes/<id>.log`.

* **Return type:**
  [`FileStore`](#xa.store.FileStore)

### xa.store.default_revive_store(state_dir=PosixPath('/home/runner/.xa'))

Per-pane last-reconnect stamps for [`xa.revive.RateGuard`](xa.revive.html.md#xa.revive.RateGuard).

On disk rather than in memory on purpose: the guard exists to stop a
cron tick, a hook and a human from each sending `/remote-control`
into the same pane seconds apart, and every one of those is a
different process.

* **Return type:**
  [`FileStore`](#xa.store.FileStore)
