from mtgmeta.parsing.format_page import parse_latest_event_id, parse_meta_periods, parse_metagame


def test_parse_metagame_modern(load_fixture):
    shares = parse_metagame(load_fixture("format_mo_meta51.html"))
    assert [(s.name, s.share_pct) for s in shares[:5]] == [
        ("UR Aggro", 9.0),
        ("Affinity", 6.0),
        ("Rakdos Aggro", 6.0),
        ("Blink", 5.0),
        ("4/5c Aggro", 5.0),
    ]
    assert shares[0].macro == "Aggro"
    assert all(s.share_pct > 0 for s in shares)
    assert 95 <= sum(s.share_pct for s in shares) <= 105


def test_parse_metagame_has_macro_groups(load_fixture):
    macros = {s.macro for s in parse_metagame(load_fixture("format_mo_meta51.html"))}
    assert {"Control", "Combo"} <= macros


def test_parse_meta_periods(load_fixture):
    periods = parse_meta_periods(load_fixture("format_mo_meta51.html"))
    assert periods["Last 2 Weeks"] == 54
    assert periods["Last 2 Months"] == 51


def test_parse_latest_event_id(load_fixture):
    assert parse_latest_event_id(load_fixture("home.html")) == 93139
