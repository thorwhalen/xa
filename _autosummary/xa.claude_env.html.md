# xa.claude_env

The environment and flags a fresh `claude` process starts with: account and permissions.

No xa imports and no third-party ones: this is the lowest layer, so that any
launcher (xa’s tmux panes, crowsnest’s spawner, a future terminal-tab provider)
can import one copy of the rules instead of re-deriving them.

**Account.** Claude Code picks its account by `CLAUDE_CONFIG_DIR` (config,
credentials and transcripts all move with it), and reaches the *default* home
only by the variable’s absence. A command line handed to tmux runs in a shell
whose environment is the tmux *server’s*, which belongs to whoever started the
server: possibly another account, possibly a session long gone, whose identity
markers (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, its messaging socket, its
effort) would make the new process register as that dead session’s child.
[`env_prefix()`](#xa.claude_env.env_prefix) therefore states every variable it controls either way round:
set when the child should have it, unset (`env -u`) when not. `tmux -e` can
only set, so it cannot express the default home.

**Permissions.** [`permission_args()`](#xa.claude_env.permission_args) turns a mode into flags,
[`bypass_refused()`](#xa.claude_env.bypass_refused) says where claude would refuse the bypass flags (root,
unless `IS_SANDBOX=1`; the refusal is invisible behind tmux), and
[`resume_permission()`](#xa.claude_env.resume_permission) picks the mode a resumed session comes back in.

Moved here from `crowsnest.spawn` (thorwhalen/xa#23), whose tests and
derivation notes it keeps; crowsnest is to import it from here.

### Module Attributes

| [`HOME_ENV_VAR`](#xa.claude_env.HOME_ENV_VAR)   | Claude Code's account variable, and the home it means when unset.                                                                                                                                                                                                                                                                                                    |
|-----------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`ACCOUNT_VARS`](#xa.claude_env.ACCOUNT_VARS)   | The variables that select which account a session runs under.                                                                                                                                                                                                                                                                                                        |
| [`DROPPED_VARS`](#xa.claude_env.DROPPED_VARS)   | they would override the account the home selects (an API key that bills elsewhere).                                                                                                                                                                                                                                                                                  |
| [`SESSION_VARS`](#xa.claude_env.SESSION_VARS)   | The `CLAUDE*` variables that identify *one running session* -- who it is, what started it, how to talk to it -- named so that [`env_prefix()`](#xa.claude_env.env_prefix) can unset them whether or not *this* process has them (a tmux server keeps the markers of the session that started it and hands them to every pane it opens afterwards). |
| [`SANDBOX_VAR`](#xa.claude_env.SANDBOX_VAR)    | Under this variable (`"1"`) Claude Code accepts the bypass flag from root.                                                                                                                                                                                                                                                                                           |
| [`BYPASS_MODE`](#xa.claude_env.BYPASS_MODE)    | Claude Code's name for "skip every permission check", and its aliases.                                                                                                                                                                                                                                                                                               |
| [`KNOWN_MODES`](#xa.claude_env.KNOWN_MODES)    | The modes `claude --permission-mode` accepts (`default` is the older name of `manual`).                                                                                                                                                                                                                                                                              |

### Functions

| [`account_env_tokens`](#xa.claude_env.account_env_tokens)(home, \*[, environ])         | The `env` prefix that runs a command under `home`'s account, markers stripped.   |
|--------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| [`bypass_refused`](#xa.claude_env.bypass_refused)(\*[, euid, environ, ...])        | Would claude refuse the bypass flags here? Root, unsandboxed, not vouched for.   |
| [`canonical_permission_mode`](#xa.claude_env.canonical_permission_mode)(mode)                 | `mode` spelled as claude spells it; `''` stays `''` (claude's own default).      |
| [`child_env`](#xa.claude_env.child_env)([environ, home, drop])                | `environ` (default `os.environ`) as a new session should inherit it.             |
| [`env_prefix`](#xa.claude_env.env_prefix)(env, \*[, environ, drop, markers])   | The <br/><br/>```<br/>``<br/>```<br/><br/>env -u .                               |
| [`permission_args`](#xa.claude_env.permission_args)(mode, \*[, allow_bypass])       | The flags that start claude in permission `mode` (`''`/`None`: none).            |
| [`resume_permission`](#xa.claude_env.resume_permission)(mode, \*, bypass_seen[, ...]) | `(mode, allow_bypass, warning)` for resuming a session last seen in `mode`.      |

### xa.claude_env.ACCOUNT_VARS *= ('CLAUDE_CONFIG_DIR', 'CLAUDE_PROFILE')*

The variables that select which account a session runs under. A shell profile
scheme commonly pairs `CLAUDE_CONFIG_DIR` with a `CLAUDE_PROFILE` label and
re-derives one from the other, so the two travel together or not at all.

### xa.claude_env.BYPASS_MODE *= 'bypassPermissions'*

Claude Code’s name for “skip every permission check”, and its aliases.

### xa.claude_env.DROPPED_VARS *= ('ANTHROPIC_API_KEY',)*

they would
override the account the home selects (an API key that bills elsewhere).

* **Type:**
  Variables a new session must not inherit whatever they are set to

### xa.claude_env.HOME_ENV_VAR *= 'CLAUDE_CONFIG_DIR'*

Claude Code’s account variable, and the home it means when unset.

### xa.claude_env.KNOWN_MODES *= frozenset({'acceptEdits', 'auto', 'bypassPermissions', 'default', 'dontAsk', 'manual', 'plan'})*

The modes `claude --permission-mode` accepts (`default` is the older name of
`manual`). A mode a newer claude adds goes here.

### xa.claude_env.SANDBOX_VAR *= 'IS_SANDBOX'*

Under this variable (`"1"`) Claude Code accepts the bypass flag from root.
[`env_prefix()`](#xa.claude_env.env_prefix) hands it on and never unsets it: a launcher may set it.

### xa.claude_env.SESSION_VARS *= ('CLAUDECODE', 'CLAUDE_AGENT_SDK_VERSION', 'CLAUDE_BG_AUTH_SNAPSHOT_PATH', 'CLAUDE_BG_BACKEND', 'CLAUDE_BG_CLAIM_AUTH', 'CLAUDE_BG_DISPATCHER_RATE_LIMIT_TIER', 'CLAUDE_BG_DISPATCHER_SUBSCRIPTION_TYPE', 'CLAUDE_BG_ISOLATION', 'CLAUDE_BG_MEMORY_TOGGLED_OFF', 'CLAUDE_BG_POST_CLEAR_RESPAWN', 'CLAUDE_BG_PTY_AUTH', 'CLAUDE_BG_RENDEZVOUS_SOCK', 'CLAUDE_BG_RV_AUTH', 'CLAUDE_BG_SESSION_PERMISSION_RULES', 'CLAUDE_BG_SOCKET_TOKENS_PATH', 'CLAUDE_BG_SOURCE', 'CLAUDE_BG_STARTUP_WEDGE_MS', 'CLAUDE_BG_TCC_DISCLAIMED', 'CLAUDE_CODE_BRIDGE_SESSION_ID', 'CLAUDE_CODE_CHILD_SESSION', 'CLAUDE_CODE_CLOUD_SESSION_ID', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_EXECPATH', 'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_CODE_REMOTE_SESSION_ID', 'CLAUDE_CODE_REMOTE_SESSION_ORIGIN', 'CLAUDE_CODE_REMOTE_SESSION_UUID', 'CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'CLAUDE_CODE_SESSION_ATTENDED', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_SESSION_KIND', 'CLAUDE_CODE_SESSION_LOG', 'CLAUDE_CODE_SESSION_NAME', 'CLAUDE_CODE_SESSION_ORIGIN', 'CLAUDE_CODE_SPAWN_TIMESTAMP_MS', 'CLAUDE_CODE_SSE_PORT', 'CLAUDE_CODE_TRIGGER_ID', 'CLAUDE_CODE_WORKER_EPOCH', 'CLAUDE_EFFORT', 'CLAUDE_JOB_DIR', 'CLAUDE_PID', 'CLAUDE_PROJECT_DIR', 'CLAUDE_PROJECT_UUID', 'CLAUDE_SESSION_ID')*

The `CLAUDE*` variables that identify *one running session* – who it is, what
started it, how to talk to it – named so that [`env_prefix()`](#xa.claude_env.env_prefix) can unset them
whether or not *this* process has them (a tmux server keeps the markers of the
session that started it and hands them to every pane it opens afterwards).

*Identity, not configuration.* Claude Code reads hundreds of `CLAUDE*`
variables, nearly all settings a person may deliberately export; those stay.
Derived by matching the identity families (session and background-job plumbing,
messaging and bridge sockets, job and project directories) against the name table
of the `claude` binary (`strings $(command -v claude) | grep -oE
'CLAUDE[A-Z0-9_]*'`), not typed out. Re-derive it when Claude Code grows a new
family; `test_session_vars_covers_every_marker_this_live_session_carries` is the
tripwire. Account variables are deliberately absent: see [`ACCOUNT_VARS`](#xa.claude_env.ACCOUNT_VARS).

### xa.claude_env.account_env_tokens(home, , environ=None)

The `env` prefix that runs a command under `home`’s account, markers stripped.

```pycon
>>> toks = account_env_tokens('/h/b', environ={'CLAUDECODE': '1'})
>>> toks[-1], ('-u', 'CLAUDECODE') in zip(toks, toks[1:])
('CLAUDE_CONFIG_DIR=/h/b', True)
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### xa.claude_env.bypass_refused(, euid=None, environ=None, root_bypass_ok=False)

Would claude refuse the bypass flags here? Root, unsandboxed, not vouched for.

`root_bypass_ok` is for a launcher known to set `IS_SANDBOX` itself.

```pycon
>>> bypass_refused(euid=0, environ={}), bypass_refused(euid=0, environ={'IS_SANDBOX': '1'})
(True, False)
>>> bypass_refused(euid=501, environ={})
False
```

* **Return type:**
  [`bool`](https://docs.python.org/3/builtins/functions.html#bool)

### xa.claude_env.canonical_permission_mode(mode)

`mode` spelled as claude spells it; `''` stays `''` (claude’s own default).

Case-insensitive; every bypass alias is `'bypassPermissions'`. An unknown name
raises `ValueError` listing the known ones.

```pycon
>>> canonical_permission_mode('Bypass'), canonical_permission_mode('acceptedits')
('bypassPermissions', 'acceptEdits')
```

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### xa.claude_env.child_env(environ=None, , home=None, drop=('ANTHROPIC_API_KEY',))

`environ` (default `os.environ`) as a new session should inherit it.

The spawning session’s identity markers ([`SESSION_VARS`](#xa.claude_env.SESSION_VARS)) go; the account
and every `CLAUDE*` *setting* (`CLAUDE_CODE_OAUTH_TOKEN`,
`CLAUDE_CODE_USE_BEDROCK`, …) stay. With
`home` the child runs under that home instead: `CLAUDE_CONFIG_DIR` set to it,
or *unset* when it is the default home. The `CLAUDE_PROFILE` label travels only
with the account it labels. `drop` goes too (pass `()` to keep it).

```pycon
>>> child_env({'CLAUDECODE': '1', 'CLAUDE_CONFIG_DIR': '/h/b', 'PATH': '/bin'})
{'CLAUDE_CONFIG_DIR': '/h/b', 'PATH': '/bin'}
>>> child_env({'ANTHROPIC_API_KEY': 'sk-x', 'PATH': '/bin'})
{'PATH': '/bin'}
```

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### xa.claude_env.env_prefix(env, , environ=None, drop=('ANTHROPIC_API_KEY',), markers=('CLAUDECODE', 'CLAUDE_AGENT_SDK_VERSION', 'CLAUDE_BG_AUTH_SNAPSHOT_PATH', 'CLAUDE_BG_BACKEND', 'CLAUDE_BG_CLAIM_AUTH', 'CLAUDE_BG_DISPATCHER_RATE_LIMIT_TIER', 'CLAUDE_BG_DISPATCHER_SUBSCRIPTION_TYPE', 'CLAUDE_BG_ISOLATION', 'CLAUDE_BG_MEMORY_TOGGLED_OFF', 'CLAUDE_BG_POST_CLEAR_RESPAWN', 'CLAUDE_BG_PTY_AUTH', 'CLAUDE_BG_RENDEZVOUS_SOCK', 'CLAUDE_BG_RV_AUTH', 'CLAUDE_BG_SESSION_PERMISSION_RULES', 'CLAUDE_BG_SOCKET_TOKENS_PATH', 'CLAUDE_BG_SOURCE', 'CLAUDE_BG_STARTUP_WEDGE_MS', 'CLAUDE_BG_TCC_DISCLAIMED', 'CLAUDE_CODE_BRIDGE_SESSION_ID', 'CLAUDE_CODE_CHILD_SESSION', 'CLAUDE_CODE_CLOUD_SESSION_ID', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_EXECPATH', 'CLAUDE_CODE_MESSAGING_SOCKET', 'CLAUDE_CODE_MESSAGING_TOKEN', 'CLAUDE_CODE_REMOTE_SESSION_ID', 'CLAUDE_CODE_REMOTE_SESSION_ORIGIN', 'CLAUDE_CODE_REMOTE_SESSION_UUID', 'CLAUDE_CODE_SESSION_ACCESS_TOKEN', 'CLAUDE_CODE_SESSION_ATTENDED', 'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_SESSION_KIND', 'CLAUDE_CODE_SESSION_LOG', 'CLAUDE_CODE_SESSION_NAME', 'CLAUDE_CODE_SESSION_ORIGIN', 'CLAUDE_CODE_SPAWN_TIMESTAMP_MS', 'CLAUDE_CODE_SSE_PORT', 'CLAUDE_CODE_TRIGGER_ID', 'CLAUDE_CODE_WORKER_EPOCH', 'CLAUDE_EFFORT', 'CLAUDE_JOB_DIR', 'CLAUDE_PID', 'CLAUDE_PROJECT_DIR', 'CLAUDE_PROJECT_UUID', 'CLAUDE_SESSION_ID'))

The `env -u ... K=V ...` tokens that make a fresh shell run a command under `env`.

Each name this controls – `markers`, the `CLAUDE*` names of `environ`
that `env` lacks, [`ACCOUNT_VARS`](#xa.claude_env.ACCOUNT_VARS), `drop` and [`DROPPED_VARS`](#xa.claude_env.DROPPED_VARS) – is
stated either way round: set when `env` has it, unset when it does not, because
the shell’s environment is not this process’s. Everything else (other settings,
and so any token) is left to the shell rather than written on a command line,
where `ps` and tmux would show it.

```pycon
>>> env_prefix({'CLAUDE_CONFIG_DIR': '/h/b'}, environ={}, markers=('CLAUDECODE',))
['env', '-u', 'CLAUDECODE', '-u', 'CLAUDE_PROFILE', '-u', 'ANTHROPIC_API_KEY', 'CLAUDE_CONFIG_DIR=/h/b']
>>> env_prefix({}, environ={}, markers=('CLAUDECODE',), drop=())
['env', '-u', 'CLAUDECODE', '-u', 'CLAUDE_CONFIG_DIR', '-u', 'CLAUDE_PROFILE', '-u', 'ANTHROPIC_API_KEY']
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### xa.claude_env.permission_args(mode, , allow_bypass=False)

The flags that start claude in permission `mode` (`''`/`None`: none).

`allow_bypass` keeps bypass *reachable* (shift-tab) for a session that starts
in another mode.

```pycon
>>> permission_args('bypass')
['--dangerously-skip-permissions']
>>> permission_args('auto', allow_bypass=True)
['--allow-dangerously-skip-permissions', '--permission-mode', 'auto']
>>> permission_args('')
[]
```

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### xa.claude_env.resume_permission(mode, , bypass_seen, euid=None, environ=None)

`(mode, allow_bypass, warning)` for resuming a session last seen in `mode`.

The session comes back in the mode it was last in. If it ever reached bypass
(`bypass_seen`) it was started with the bypass flag, so bypass stays reachable
even when it was last in another mode. Where claude would refuse the bypass flags
([`bypass_refused()`](#xa.claude_env.bypass_refused)) they are dropped with a warning: the machine’s own
settings then decide, as they would for a person there. A mode this module does
not know (renamed in a newer claude) is dropped with a warning too, since claude
would refuse it at startup, invisibly, behind tmux.

```pycon
>>> resume_permission('auto', bypass_seen=True, euid=501)
('auto', True, None)
>>> resume_permission('bypassPermissions', bypass_seen=True, euid=0, environ={})[:2]
('', False)
```

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`bool`](https://docs.python.org/3/builtins/functions.html#bool), [`Optional`](https://docs.python.org/3/library/typing.html#typing.Optional)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]
