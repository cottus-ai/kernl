import pytest

from akernl.cli import _flag, _help_text, _parse_json


def test_parse_json_valid() -> None:
    assert _parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_invalid_exits() -> None:
    with pytest.raises(SystemExit):
        _parse_json("not json {")


def test_flag_present() -> None:
    assert _flag(["-o", "out.krn", "x"], "-o") == "out.krn"
    assert _flag(["compile", "-o", "a", "b"], "-o") == "a"


def test_flag_missing_returns_default() -> None:
    assert _flag(["compile", "a.py"], "-o", default=None) is None


def test_help_text_nonempty() -> None:
    assert len(_help_text()) > 100
    assert "compile" in _help_text()
