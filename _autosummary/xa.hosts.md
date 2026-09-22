# xa.hosts

Host abstraction — local, SSH, HTTP backends.

A `Host` is anything that can enumerate Claude Code sessions and
perform lifecycle actions on them (spawn / resume / kill). All transports
return the same [`xa.sessions.Session`](xa.sessions.md#xa.sessions.Session) shape so higher layers
don’t care whether sessions live on this machine, on another machine
over SSH, or behind another `xa` server over HTTPS.

The protocol is duck-typed (`runtime_checkable`), so user extensions
only need to implement the methods they actually use — unused methods
can raise `NotImplementedError`.

### Functions

| [`default_hosts`](#xa.hosts.default_hosts)()   | The out-of-the-box registry: one local host, nothing else.   |
|--------------------------------------------------------------------|--------------------------------------------------------------|

### Classes

| [`Host`](#xa.hosts.Host)(\*args, \*\*kwargs)                        | Duck-typed interface every transport satisfies.   |
|--------------------------------------------------------------------------------------------------|---------------------------------------------------|
| [`LocalHost`](#xa.hosts.LocalHost)([name, claude_home, claude_bin, ...]) | The machine `xa` is running on.                   |
| [`SSHHost`](#xa.hosts.SSHHost)(name, \*, host[, user, ...])            | Transcripts via rsync; actions via ssh exec.      |
| [`HTTPHost`](#xa.hosts.HTTPHost)(name, \*, base_url[, auth, ...])       | Client for a remote `xa serve` instance.          |

### *class* xa.hosts.HTTPHost(name, , base_url, auth=None, username=None, password=None, token=None, timeout=30.0)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Client for a remote `xa serve` instance.

#### sync(, force=False)

HTTP sessions are fetched fresh on each listing — no separate sync.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### *class* xa.hosts.Host(\*args, \*\*kwargs)

Bases: [`Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)

Duck-typed interface every transport satisfies.

### *class* xa.hosts.LocalHost(name='local', , claude_home=PosixPath('/home/runner/.claude'), claude_bin='claude', tmux_bin='tmux', alive_predicate=None)

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

The machine `xa` is running on.

#### sync(, force=False)

No-op — local has nothing to sync.

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### *class* xa.hosts.SSHHost(name, , host, user=None, remote_claude_home='~/.claude', cache_dir=PosixPath('/home/runner/.cache/xa/remotes'), stale_threshold_sec=3600, claude_bin='claude', tmux_bin='tmux', ssh_bin='ssh', rsync_bin='rsync')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

Transcripts via rsync; actions via ssh exec.

* **Parameters:**
  * **name** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Logical name used in `Session.host` and elsewhere.
  * **host** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – SSH hostname / alias. Matches cc-sessions: either an
    `~/.ssh/config` alias (`devbox`) or a raw host (`1.2.3.4`).
  * **user** ([`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]) – Optional SSH user for raw hosts.
  * **remote_claude_home** ([`str`](https://docs.python.org/3/builtins/stdtypes.html#str)) – Remote path to `~/.claude/`. Defaults to `~/.claude` which
    resolves relative to the SSH user’s home on the remote side.
  * **cache_dir** ([`Path`](https://docs.python.org/3/library/pathlib.html#pathlib.Path)) – Where to stage the rsync’d tree locally.
  * **stale_threshold_sec** ([`int`](https://docs.python.org/3/builtins/functions.html#int)) – Re-sync when the cached copy is older than this. `0` means
    always sync.

#### spawn(name, , cwd, \*\*opts)

Spawn `tmux new-session -d 's <name>' 'cd <cwd> && exec claude'` over SSH.

URL detection is left to the caller’s next [`sync()`](#xa.hosts.SSHHost.sync) + listing —
we don’t round-trip waiting for the bridge URL here (that would
hold an SSH connection open for up to 2 minutes).

* **Return type:**
  [`SpawnResult`](xa.claude_cli.md#xa.claude_cli.SpawnResult)

#### sync(, force=False)

Pull `<remote_claude_home>/projects/` and `/sessions/` into the cache.

Non-existent remote directories are tolerated (rsync returns 23).

* **Return type:**
  [`None`](https://docs.python.org/3/builtins/constants.html#None)

### xa.hosts.default_hosts()

The out-of-the-box registry: one local host, nothing else.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`Host`](#xa.hosts.Host)]

### Modules

| [`http`](xa.hosts.http.md#module-xa.hosts.http)   | HTTP host — a remote `xa serve` instance.   |
|------------------------------------------------------------------------------|---------------------------------------------|
| [`local`](xa.hosts.local.md#module-xa.hosts.local) | Local in-process host.                      |
| [`ssh`](xa.hosts.ssh.md#module-xa.hosts.ssh)     | SSH-backed host.                            |
