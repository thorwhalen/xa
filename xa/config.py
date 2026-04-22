"""TOML config loader for ``xa``.

Resolves a host registry and settings from ``~/.config/xa/config.toml``
(respecting ``$XDG_CONFIG_HOME``). Override the path via ``$XA_CONFIG``.

Example:

.. code-block:: toml

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

The registry exposed by :func:`load_hosts` is ``{name: Host}`` and can be
passed directly to :func:`xa.sessions.list_sessions`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover — fallback for 3.10
    import tomli as tomllib  # type: ignore


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #


def _xdg_config_home() -> Path:
    v = os.environ.get("XDG_CONFIG_HOME")
    return Path(v) if v else Path.home() / ".config"


def default_config_path() -> Path:
    override = os.environ.get("XA_CONFIG")
    if override:
        return Path(override)
    return _xdg_config_home() / "xa" / "config.toml"


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    stale_threshold_sec: int = 3600
    claude_bin: str = "claude"
    tmux_bin: str = "tmux"


def _default_settings() -> Settings:
    return Settings(cache_dir=Path.home() / ".cache" / "xa" / "remotes")


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def _expand(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value))


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _build_settings(raw: dict) -> Settings:
    s = raw.get("settings", {})
    cache_dir = s.get("cache_dir")
    return Settings(
        cache_dir=Path(_expand(cache_dir))
        if cache_dir
        else _default_settings().cache_dir,
        stale_threshold_sec=int(s.get("stale_threshold_sec", 3600)),
        claude_bin=s.get("claude_bin", "claude"),
        tmux_bin=s.get("tmux_bin", "tmux"),
    )


def _build_host(name: str, entry: dict, settings: Settings):
    """Turn one ``[hosts.<name>]`` entry into a concrete Host."""
    # Delay imports so that, e.g., a config referencing only SSH hosts
    # doesn't fail if the user hasn't installed fastapi for HTTPHost.
    kind = entry.get("kind", "local")
    if kind == "local":
        from xa.hosts import LocalHost

        return LocalHost(
            name=name,
            claude_bin=settings.claude_bin,
            tmux_bin=settings.tmux_bin,
        )
    if kind == "ssh":
        from xa.hosts import SSHHost

        if "host" not in entry:
            raise ValueError(f"[hosts.{name}] missing required key 'host'")
        return SSHHost(
            name=name,
            host=entry["host"],
            user=entry.get("user"),
            remote_claude_home=entry.get("remote_claude_home", "~/.claude"),
            cache_dir=settings.cache_dir,
            stale_threshold_sec=settings.stale_threshold_sec,
            claude_bin=settings.claude_bin,
            tmux_bin=settings.tmux_bin,
        )
    if kind == "http":
        from xa.hosts import HTTPHost

        if "base_url" not in entry:
            raise ValueError(f"[hosts.{name}] missing required key 'base_url'")
        password = entry.get("password")
        if not password and entry.get("password_env"):
            password = os.environ.get(entry["password_env"])
        token = entry.get("token")
        if not token and entry.get("token_env"):
            token = os.environ.get(entry["token_env"])
        return HTTPHost(
            name=name,
            base_url=entry["base_url"],
            auth=entry.get("auth"),
            username=entry.get("username"),
            password=password,
            token=token,
            timeout=float(entry.get("timeout", 30.0)),
        )
    raise ValueError(f"[hosts.{name}] unknown kind '{kind}'")


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def load(path: Optional[Path] = None) -> tuple[Settings, dict]:
    """Return ``(settings, hosts_registry)`` from a config file.

    Missing file → ``(defaults, {"local": LocalHost()})``.
    """
    raw = _read_toml(path or default_config_path())
    settings = _build_settings(raw)
    hosts_raw = raw.get("hosts") or {}
    hosts: dict[str, Any] = {}
    if not hosts_raw:
        from xa.hosts import LocalHost

        hosts["local"] = LocalHost(
            claude_bin=settings.claude_bin, tmux_bin=settings.tmux_bin
        )
    else:
        for name, entry in hosts_raw.items():
            hosts[name] = _build_host(name, entry, settings)
    return settings, hosts


def load_hosts(path: Optional[Path] = None) -> dict:
    """Shortcut: just the host registry."""
    return load(path)[1]
