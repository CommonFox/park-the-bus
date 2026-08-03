import pytest

from ptb.core import cli


def test_version_flag_prints_version(capsys):
    exit_code = cli.main(["--version"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "0.1.0" in captured.out


def test_no_command_prints_usage_and_fails(capsys):
    exit_code = cli.main([])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "usage" in captured.out.lower()


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit):
        cli.main(["nonsense-command"])
