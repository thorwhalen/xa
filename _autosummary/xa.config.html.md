# xa.config

TOML config loader for `xa`.

Resolves a host registry and settings from `~/.config/xa/config.toml`
(respecting `$XDG_CONFIG_HOME`). Override the path via `$XA_CONFIG`.

### Example

```toml
[settings]
cache_dir = "~/.cache/xa/remotes"
stale_threshold_sec = 3600

[hosts.local]
kind = "local"

[hosts.devbox]
kind = "ssh"
host = "devbox"

[hosts.phone_mirror]
kind = "http"
base_url = "https://apps.example.com/api/xa"
auth = "basic"
username = "me"
password_env = "XA_PHONE_PASSWORD"   # value read from env, not stored in TOML
```

The registry exposed by [`load_hosts()`](#xa.config.load_hosts) is `{name: Host}` and can be
passed directly to [`xa.sessions.list_sessions()`](xa.sessions.html.md#xa.sessions.list_sessions).

### Functions

| `default_config_path`()                                             |                                                         |
|---------------------------------------------------------------------|---------------------------------------------------------|
| [`load`](#xa.config.load)([path])       | Return `(settings, hosts_registry)` from a config file. |
| [`load_hosts`](#xa.config.load_hosts)([path]) | Shortcut: just the host registry.                       |

### Classes

| [`Settings`](#xa.config.Settings)(cache_dir[, stale_threshold_sec, ...])   |    |
|----------------------------------------------------------------------------------------------------|----|

### *class* xa.config.Settings(cache_dir, stale_threshold_sec=3600, claude_bin='claude', tmux_bin='tmux')

Bases: [`object`](https://docs.python.org/3/builtins/functions.html#object)

### xa.config.load(path=None)

Return `(settings, hosts_registry)` from a config file.

Missing file → `(defaults, {"local": LocalHost()})`.

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Settings`](#xa.config.Settings), [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### xa.config.load_hosts(path=None)

Shortcut: just the host registry.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)
