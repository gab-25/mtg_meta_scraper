from sqlalchemy import func, select

from mtg_meta_scraper.db.tables import Deck, DeckCard, Event, ScanState
from mtg_meta_scraper.parsing.event import parse_event


def _scalar(repo, stmt):
    with repo.session.begin():
        return repo.session.scalar(stmt)


def test_upsert_event_is_idempotent(repo, load_fixture):
    ev = parse_event(load_fixture("event_mo_93105.html"), 93105)
    repo.upsert_event(ev)
    repo.upsert_event(ev)
    assert _scalar(repo, select(func.count()).select_from(Event)) == 1
    assert _scalar(repo, select(func.count()).select_from(Deck)) == 16


def test_save_deck_detail_sets_archetype_and_cards(repo, load_fixture):
    ev = parse_event(load_fixture("event_mo_93105.html"), 93105)
    repo.upsert_event(ev)
    repo.save_deck_detail(93105, "MO", ev.shown_deck)
    with repo.session.begin():
        deck = repo.session.get(Deck, 907298)
        assert (deck.archetype.id, deck.archetype.name, deck.detail_scraped) == (918, "Rakdos Aggro", True)
        assert len(deck.cards) == 30
        assert sum(c.qty for c in deck.cards if c.section == "main") == 60


def test_reupsert_event_keeps_known_archetype(repo, load_fixture):
    ev = parse_event(load_fixture("event_mo_93105.html"), 93105)
    repo.upsert_event(ev)
    repo.save_deck_detail(93105, "MO", ev.shown_deck)
    repo.upsert_event(ev)
    assert _scalar(repo, select(Deck.archetype_id).where(Deck.id == 907298)) == 918


def test_resaving_detail_replaces_cards(repo, load_fixture):
    ev = parse_event(load_fixture("event_mo_93105.html"), 93105)
    repo.upsert_event(ev)
    repo.save_deck_detail(93105, "MO", ev.shown_deck)
    repo.save_deck_detail(93105, "MO", ev.shown_deck)
    assert _scalar(repo, select(func.count()).select_from(DeckCard).where(DeckCard.deck_id == 907298)) == 30


def test_commander_key_saved_for_cedh(repo, load_fixture):
    ev = parse_event(load_fixture("event_cedh_92803.html"), 92803)
    repo.upsert_event(ev)
    repo.save_deck_detail(92803, "cEDH", ev.shown_deck)
    assert _scalar(repo, select(Deck.commander).where(Deck.id == 904886)) == "Kraum, Ludevic's Opus + Tymna the Weaver"


def test_scan_state_attempts_increment(repo):
    repo.mark_scan(1, "error")
    repo.mark_scan(1, "error")
    assert _scalar(repo, select(ScanState.attempts).where(ScanState.event_id == 1)) == 2
    assert repo.scan_status(1) == "error"


def test_mark_scan_keeps_known_label_and_date(repo):
    from datetime import date

    repo.mark_scan(1, "skipped", "Premodern", date(2026, 10, 1))
    repo.mark_scan(1, "error")
    assert repo.scan_state(1) == ("error", 2, date(2026, 10, 1))
