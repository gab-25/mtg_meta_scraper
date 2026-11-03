from datetime import date

from mtg_meta_scraper.models import CardLine, DeckRow
from mtg_meta_scraper.parsing.common import parse_rank, parse_site_date
from mtg_meta_scraper.parsing.event import parse_deck_detail, parse_event


def _sum(detail, section):
    return sum(c.qty for c in detail.cards if c.section == section)


def _rows(detail, section):
    return [c for c in detail.cards if c.section == section]


def test_parse_rank_variants():
    assert parse_rank("3-4") == (3, 4)
    assert parse_rank("1") == (1, 1)
    assert parse_rank("17-32") == (17, 32)


def test_parse_site_date():
    assert parse_site_date("01/11/26") == date(2026, 11, 1)


def test_parse_regular_event(load_fixture):
    ev = parse_event(load_fixture("event_mo_93105.html"), 93105)
    assert ev.event_id == 93105
    assert ev.name == "Regional Championship - MIT Championship @ GameSquare (Taipei, Taiwan)"
    assert ev.format_code == "MO"
    assert ev.format_label == "Modern"
    assert ev.event_date == date(2026, 11, 1)
    assert ev.players == 83
    assert ev.source == "melee.gg"
    assert ev.stars == 3
    assert ev.is_major is False
    assert ev.is_online is False
    assert ev.is_league is False
    assert len(ev.decks) == 16
    assert ev.decks[0] == DeckRow(907298, "1", 1, 1, "Rakdos Delirium", "Wei_hao_huang")
    assert ev.decks[1].deck_id == 907299
    assert [d.rank_raw for d in ev.decks] == ["1", "2", "3-4", "3-4"] + ["5-8"] * 4 + ["9-16"] * 8


def test_shown_deck_of_regular_event(load_fixture):
    d = parse_event(load_fixture("event_mo_93105.html"), 93105).shown_deck
    assert d.deck_id == 907298
    assert (d.archetype_id, d.archetype_name) == (918, "Rakdos Aggro")
    assert _sum(d, "main") == 60 and len(_rows(d, "main")) == 20
    assert d.cards[0] == CardLine("main", "Blackcleave Cliffs", 1)
    assert _sum(d, "side") == 15 and len(_rows(d, "side")) == 10
    assert d.commanders == ()
    assert d.commander_key is None


def test_parse_deck_detail_specific_deck(load_fixture):
    d = parse_deck_detail(load_fixture("event_mo_93105_d907299.html"), 907299)
    assert (d.archetype_id, d.archetype_name) == (209, "UW Control")
    assert d.cards[0] == CardLine("main", "Arid Mesa", 3)
    assert _sum(d, "main") == 60
    assert _sum(d, "side") == 15


def test_parse_league_event(load_fixture):
    ev = parse_event(load_fixture("event_mo_league_93120.html"), 93120)
    assert ev.is_league is True
    assert ev.players is None
    assert ev.event_date == date(2026, 11, 2)
    assert [d.rank_min for d in ev.decks] == list(range(1, 9))
    assert ev.decks[0].deck_name == "Devoted Combo"
    assert (ev.shown_deck.archetype_id, ev.shown_deck.archetype_name) == (191, "Creatures Toolbox")


def test_parse_cedh_event(load_fixture):
    ev = parse_event(load_fixture("event_cedh_92803.html"), 92803)
    assert ev.format_code == "cEDH"
    assert ev.is_major is True
    assert ev.players == 150
    assert len(ev.decks) == 64
    d = ev.shown_deck
    assert d.deck_id == 904886
    assert d.archetype_name == "Partner WUBR"
    assert d.commanders == ("Kraum, Ludevic's Opus", "Tymna the Weaver")
    assert d.commander_key == "Kraum, Ludevic's Opus + Tymna the Weaver"
    assert _sum(d, "main") == 98


def test_parse_duel_commander_event(load_fixture):
    ev = parse_event(load_fixture("event_edh_93122.html"), 93122)
    assert ev.format_code == "EDH"
    assert ev.players == 923
    assert ev.event_date == date(2026, 10, 31)
    assert "Labège" in ev.name
    assert ev.shown_deck.commander_key == "Satya, Aetherflux Genius"
    assert _sum(ev.shown_deck, "main") == 99


def test_missing_event_returns_none(load_fixture):
    assert parse_event(load_fixture("event_missing.html"), 99999999) is None


def test_player_names_are_unescaped_text(load_fixture):
    ev = parse_event(load_fixture("event_mo_93105.html"), 93105)
    for d in ev.decks:
        for s in (d.player, d.deck_name):
            assert s and "&#" not in s and "&amp;" not in s


def test_card_rows_not_parsed_as_decks(load_fixture):
    ev = parse_event(load_fixture("event_mo_93105.html"), 93105)
    assert "Blackcleave Cliffs" not in {d.deck_name for d in ev.decks}
