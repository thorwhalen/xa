"""The shape of the ``xa`` command line, asserted on the parser itself.

``xa.cli.mk_parser`` builds a plain :class:`argparse.ArgumentParser` and runs
nothing, so the grammar is checkable in-process: the subcommand names a user
types, the ``archive`` group, and the flags each verb offers. That matters most
for the names that are *not* their handler's ``__name__`` -- ``gen-secret`` is
not a Python identifier, and ``archive list`` collides with the top-level
``list``.
"""

import argparse

import pytest

from xa import cli


TOP_LEVEL = {
    "list",
    "info",
    "history",
    "spawn",
    "resume",
    "kill",
    "serve",
    "sync",
    "pick",
    "gen-secret",
    "revive",
    "archive",
}

ARCHIVE = {"list", "log", "forensics"}


def _choices(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("parser has no subcommands")


@pytest.fixture(scope="module")
def parser():
    return cli.mk_parser()


def test_parser_is_plain_argparse(parser):
    # Not a subclass: argcomplete and every argparse-typed tool keep working.
    assert type(parser) is argparse.ArgumentParser
    assert parser.prog == "xa"


def test_top_level_subcommands(parser):
    assert set(_choices(parser)) == TOP_LEVEL


def test_gen_secret_is_hyphenated(parser):
    # The handler is gen_secret_cmd; the command a user types is `gen-secret`.
    assert "gen_secret" not in _choices(parser)
    assert "gen_secret_cmd" not in _choices(parser)


def test_archive_group(parser):
    assert set(_choices(_choices(parser)["archive"])) == ARCHIVE


def test_archive_list_does_not_shadow_top_level_list(parser):
    # Same leaf name, two different handlers -- the group keeps them apart.
    top = _choices(parser)["list"]
    nested = _choices(_choices(parser)["archive"])["list"]
    assert top is not nested


def test_archive_group_listing_row_has_no_help_yet():
    # Pinned, not endorsed. ARCHIVE_GROUP_KWARGS says ``help=``, but the row a
    # group gets in the *parent's* --help is fed by ``title``; ``help`` only
    # reaches add_subparsers(), where it renders nowhere the user looks. So the
    # `archive` row has always been bare -- under argh, and identically under cw.
    # See https://github.com/thorwhalen/xa/issues/13. When that is fixed, this
    # test is what will tell you.
    parser = cli.mk_parser()
    action = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    entry = next(c for c in action._choices_actions if c.dest == "archive")
    assert entry.help is None


@pytest.mark.parametrize(
    "command,flags",
    [
        ("list", {"--project", "--limit", "--include-forks", "--no-live",
                   "--state", "--host", "--json-out"}),
        ("info", {"--json-out"}),
        ("gen-secret", {"--length"}),
        ("revive", {"--apply", "--include-held-elsewhere", "--server-mode",
                    "--min-interval", "--json-out"}),
    ],
)
def test_verb_offers_its_flags(parser, command, flags):
    offered = {
        flag
        for action in _choices(parser)[command]._actions
        for flag in action.option_strings
        if flag.startswith("--")
    }
    assert flags <= offered


@pytest.mark.parametrize("command", ["info", "kill"])
def test_verb_takes_its_session_id_positional(parser, command):
    # The dest is spelled `session-id`, hyphen and all -- argh's naming, which cw
    # reproduces. It is never read as an attribute, so the hyphen is harmless.
    action = next(
        a for a in _choices(parser)[command]._actions if a.dest == "session-id"
    )
    assert not action.option_strings


def test_list_takes_no_positional(parser):
    assert all(a.option_strings for a in _choices(parser)["list"]._actions)


def test_bad_command_line_exits_2(parser, monkeypatch):
    # A console script that starts exiting 0 on a bad command line breaks every
    # CI step that checks $?. cw.run returns the code, so main() re-raises it.
    monkeypatch.setattr("sys.argv", ["xa", "nosuchcommand"])
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 2
