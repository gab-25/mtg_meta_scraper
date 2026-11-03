import pytest

from mtg_meta_scraper import cli
from mtg_meta_scraper.models import MetaShare
from mtg_meta_scraper.validate import ValidationRow, compare_shares


def _site(*pairs):
    return [MetaShare(i, n, None, p) for i, (n, p) in enumerate(pairs, 1)]


def test_compare_within_tolerance():
    [row] = compare_shares(_site(("A", 9.0)), {"A": 10.5}, format_code="MO")
    assert row.ok and row.diff == pytest.approx(1.5)


def test_compare_outside_tolerance():
    [row] = compare_shares(_site(("A", 9.0)), {"A": 12.0}, format_code="MO")
    assert not row.ok and row.diff == pytest.approx(3.0)


def test_compare_missing_archetype():
    [row] = compare_shares(_site(("A", 9.0)), {}, format_code="MO")
    assert not row.ok and row.ours_pct is None


def test_compare_only_top_n():
    site = _site(*[(f"A{i}", 20 - i) for i in range(15)])
    assert len(compare_shares(site, {}, format_code="MO", top=10)) == 10


def test_cli_validate_exit_code(monkeypatch):
    bad = ValidationRow("MO", "A", 9.0, 12.0, 3.0, False)
    monkeypatch.setattr(cli, "_run_validate", lambda settings: [bad])
    with pytest.raises(SystemExit) as exc:
        cli.main(["validate"])
    assert exc.value.code == 1
