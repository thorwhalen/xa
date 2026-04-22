"""Unit tests for ``xa.config`` — TOML loading."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from xa import config as cfg
from xa.hosts import HTTPHost, LocalHost, SSHHost


def _write_config(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_load_missing_file_uses_defaults(tmp_path: Path) -> None:
    settings, hosts = cfg.load(tmp_path / "missing.toml")
    assert settings.claude_bin == "claude"
    assert settings.tmux_bin == "tmux"
    assert set(hosts) == {"local"}
    assert isinstance(hosts["local"], LocalHost)


def test_load_full_config(tmp_path: Path) -> None:
    path = tmp_path / "xa.toml"
    _write_config(
        path,
        '''
[settings]
cache_dir = "~/.cache/xa/custom"
stale_threshold_sec = 7200
claude_bin = "/opt/claude"
tmux_bin = "/usr/bin/tmux"

[hosts.local]
kind = "local"

[hosts.devbox]
kind = "ssh"
host = "devbox.example.com"
user = "deploy"
remote_claude_home = "/home/deploy/.claude"

[hosts.phone]
kind = "http"
base_url = "https://phone.example/api/xa"
auth = "basic"
username = "me"
password = "sekret"
''',
    )
    settings, hosts = cfg.load(path)
    assert settings.cache_dir == Path("~/.cache/xa/custom").expanduser()
    assert settings.stale_threshold_sec == 7200
    assert settings.claude_bin == "/opt/claude"

    assert set(hosts) == {"local", "devbox", "phone"}
    assert isinstance(hosts["local"], LocalHost)
    ssh = hosts["devbox"]
    assert isinstance(ssh, SSHHost) and ssh.host == "devbox.example.com"
    assert ssh.user == "deploy"
    http = hosts["phone"]
    assert isinstance(http, HTTPHost)
    assert http.base_url == "https://phone.example/api/xa"
    assert http.username == "me" and http.password == "sekret"


def test_password_env_is_read_from_env(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "xa.toml"
    _write_config(
        path,
        '''
[hosts.phone]
kind = "http"
base_url = "https://x"
auth = "basic"
username = "me"
password_env = "XA_TEST_PW"
''',
    )
    monkeypatch.setenv("XA_TEST_PW", "from-env")
    _, hosts = cfg.load(path)
    assert hosts["phone"].password == "from-env"


def test_invalid_kind_raises(tmp_path: Path) -> None:
    path = tmp_path / "xa.toml"
    _write_config(path, '[hosts.bad]\nkind = "ftp"\n')
    with pytest.raises(ValueError, match="unknown kind"):
        cfg.load(path)


def test_ssh_missing_host_raises(tmp_path: Path) -> None:
    path = tmp_path / "xa.toml"
    _write_config(path, '[hosts.x]\nkind = "ssh"\n')
    with pytest.raises(ValueError, match="missing required key 'host'"):
        cfg.load(path)


def test_xa_config_env_var_override(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "custom.toml"
    _write_config(path, '[hosts.local]\nkind = "local"\n')
    monkeypatch.setenv("XA_CONFIG", str(path))
    assert cfg.default_config_path() == path
