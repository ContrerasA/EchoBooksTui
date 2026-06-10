from echobooks.util import (
    format_runtime,
    parse_date,
    parse_rating,
    parse_runtime,
    split_csv,
    stars,
)


def test_parse_runtime_formats():
    assert parse_runtime("970") == 970
    assert parse_runtime("16:10") == 970
    assert parse_runtime("16h 10m") == 970
    assert parse_runtime("2h") == 120
    assert parse_runtime("45m") == 45
    assert parse_runtime("") is None
    assert parse_runtime("garbage") is None


def test_format_runtime():
    assert format_runtime(970) == "16h 10m"
    assert format_runtime(120) == "2h"
    assert format_runtime(45) == "45m"
    assert format_runtime(None) == "—"


def test_parse_rating_clamps():
    assert parse_rating("4.5") == 4.5
    assert parse_rating("9") == 5.0
    assert parse_rating("0") == 0.5
    assert parse_rating("") is None
    assert parse_rating("x") is None


def test_parse_date():
    assert parse_date("2026-01-15").isoformat() == "2026-01-15"
    assert parse_date("nope") is None
    assert parse_date("") is None


def test_stars():
    assert stars(5.0) == "★★★★★"
    assert stars(4.5) == "★★★★⯨"
    assert stars(None) == "—"


def test_split_csv():
    assert split_csv("a, b ,  c") == ["a", "b", "c"]
    assert split_csv("") == []
