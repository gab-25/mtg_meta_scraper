from mtg_meta_scraper.parsing.event import parse_event


def _count(repo, sql, *args):
    return repo.conn.execute(sql, args).fetchone()[0]


def test_upsert_event_is_idempotent(repo, load_fixture):
    ev = parse_event(load_fixture("event_mo_93105.html"), 93105)
    repo.upsert_event(ev)
    repo.upsert_event(ev)
    assert _count(repo, "SELECT count(*) FROM event") == 1
    assert _count(repo, "SELECT count(*) FROM deck") == 16


def test_save_deck_detail_sets_archetype_and_cards(repo, load_fixture):
    ev = parse_event(load_fixture("event_mo_93105.html"), 93105)
    repo.upsert_event(ev)
    repo.save_deck_detail(93105, "MO", ev.shown_deck)
    row = repo.conn.execute(
        "SELECT a.id, a.name, d.detail_scraped FROM deck d JOIN archetype a ON a.id = d.archetype_id WHERE d.id = 907298"
    ).fetchone()
    assert row == (918, "Rakdos Aggro", True)
    assert _count(repo, "SELECT count(*) FROM deck_card WHERE deck_id = 907298") == 30
    assert _count(repo, "SELECT sum(qty) FROM deck_card WHERE deck_id = 907298 AND section = 'main'") == 60


def test_reupsert_event_keeps_known_archetype(repo, load_fixture):
    ev = parse_event(load_fixture("event_mo_93105.html"), 93105)
    repo.upsert_event(ev)
    repo.save_deck_detail(93105, "MO", ev.shown_deck)
    repo.upsert_event(ev)
    assert _count(repo, "SELECT archetype_id FROM deck WHERE id = 907298") == 918


def test_commander_key_saved_for_cedh(repo, load_fixture):
    ev = parse_event(load_fixture("event_cedh_92803.html"), 92803)
    repo.upsert_event(ev)
    repo.save_deck_detail(92803, "cEDH", ev.shown_deck)
    assert _count(repo, "SELECT commander FROM deck WHERE id = 904886") == "Kraum, Ludevic's Opus + Tymna the Weaver"


def test_scan_state_attempts_increment(repo):
    repo.mark_scan(1, "error")
    repo.mark_scan(1, "error")
    assert _count(repo, "SELECT attempts FROM scan_state WHERE event_id = 1") == 2
    assert repo.scan_status(1) == "error"
