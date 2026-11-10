"""Tests for ``xa.claude_env``: the env and permission rules a fresh claude starts with."""

from __future__ import annotations

import os

import pytest

from xa import claude_env as cenv


def _unset(tokens: list[str]) -> list[str]:
    return [b for a, b in zip(tokens, tokens[1:]) if a == "-u"]


def test_session_vars_covers_every_marker_this_live_session_carries() -> None:
    """The tripwire for drift: inside a real session the environment is the oracle.

    ``SESSION_VARS`` is derived from one version of the claude binary, so the
    failure mode is a family it grew later going quietly un-unset.
    """
    uncovered = (
        {k for k in os.environ if k.startswith("CLAUDE")}
        - set(cenv.SESSION_VARS)
        - set(cenv.ACCOUNT_VARS)
    )
    assert not uncovered, (
        f"this session carries {sorted(uncovered)}, which SESSION_VARS does not name. "
        "If they identify the session, add them (see SESSION_VARS for the derivation)."
    )


def test_env_prefix_unsets_markers_this_process_does_not_have() -> None:
    tokens = cenv.env_prefix({"PATH": "/b"}, environ={"PATH": "/b"})
    assert set(cenv.SESSION_VARS) <= set(_unset(tokens))


def test_env_prefix_hands_on_is_sandbox() -> None:
    tokens = cenv.env_prefix({"IS_SANDBOX": "1"}, environ={})
    assert "IS_SANDBOX=1" in tokens and "IS_SANDBOX" not in _unset(tokens)


def test_permission_args_refuses_an_unknown_mode() -> None:
    """Claude would refuse it at startup, invisibly behind tmux; refuse it here."""
    assert cenv.permission_args("default") == ["--permission-mode", "default"]
    with pytest.raises(ValueError):
        cenv.permission_args("someFutureMode")


def test_same_rules_as_crowsnest_while_it_still_has_its_own_copy() -> None:
    """crowsnest is to import these from xa; until it does, the two must not drift."""
    cn = pytest.importorskip("crowsnest.spawn")
    assert set(cenv.SESSION_VARS) >= set(cn.SESSION_VARS)
    assert tuple(cenv.ACCOUNT_VARS) == tuple(cn.ACCOUNT_VARS)
    assert set(cenv.KNOWN_MODES) == set(cn.KNOWN_MODES)
