from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from conftest import fetch
from mtg_meta_scraper.db.tables import Archetype, Deck, DeckCard, Event

FROM = datetime(2026, 9, 1, tzinfo=timezone.utc)
TO = datetime(2026, 11, 3, tzinfo=timezone.utc)
AS_OF = datetime(2026, 11, 3, tzinfo=timezone.utc)


class Seeder:
    def __init__(self, repo):
        self.repo = repo
        self.next_deck = 1
        self.archetypes: dict[tuple[str, str], int] = {}

    def _add(self, *objs):
        with self.repo.session.begin():
            self.repo.session.add_all(objs)

    def event(self, eid: int, fmt: str, d: date, league: bool = False):
        self._add(Event(id=eid, format_code=fmt, name=f"ev{eid}", event_date=d, is_league=league))

    def deck(self, eid: int, fmt: str, arch: str, rank: int = 5, commander: str | None = None,
             name: str | None = None):
        key = (fmt, arch)
        if key not in self.archetypes:
            self.archetypes[key] = len(self.archetypes) + 1
            self._add(Archetype(id=self.archetypes[key], format_code=fmt, name=arch))
        self._add(Deck(id=self.next_deck, event_id=eid, rank_raw=str(rank), rank_min=rank, rank_max=rank,
                       deck_name=name or arch, player="p", archetype_id=self.archetypes[key],
                       commander=commander))
        self.next_deck += 1
        return self.next_deck - 1

    def cards(self, deck_id: int, *lines: tuple[str, str, int]):
        self._add(*(DeckCard(deck_id=deck_id, section=s, card_name=n, qty=q) for s, n, q in lines))

    def many(self, eid, fmt, arch, n, **kw):
        for _ in range(n):
            self.deck(eid, fmt, arch, **kw)


@pytest.fixture
def seed(repo):
    return Seeder(repo)


def test_placement_points_weights(repo):
    got = [fetch(repo, "SELECT placement_points(:r)", r=r)[0][0] for r in (1, 2, 3, 5, 9, 17)]
    assert got == [8, 6, 4, 2, 1, Decimal("0.5")]


def test_share_series_sums_to_100_per_bucket(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    seed.many(1, "MO", "A", 3)
    seed.many(1, "MO", "B", 1)
    seed.event(2, "MO", date(2026, 10, 20))
    seed.many(2, "MO", "A", 1)
    seed.many(2, "MO", "C", 4)
    rows = fetch(repo, "SELECT time, sum(share) FROM meta_share_series('MO', '7 days', :t_from, :t_to, 100) GROUP BY time",
                 t_from=FROM, t_to=TO)
    assert len(rows) == 2
    assert all(abs(float(total) - 100) < 1e-6 for _, total in rows)


def test_share_includes_leagues_performance_excludes_them(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    seed.many(1, "MO", "A", 4, rank=1)
    seed.event(2, "MO", date(2026, 10, 5), league=True)
    seed.many(2, "MO", "L", 4, rank=1)
    share = {g for _, g, _ in fetch(repo, "SELECT * FROM meta_share_series('MO', '7 days', :t_from, :t_to, 10)", t_from=FROM, t_to=TO)}
    perf = {g for _, g, _ in fetch(repo, "SELECT * FROM performance_series('MO', '7 days', :t_from, :t_to, 10)", t_from=FROM, t_to=TO)}
    assert "L" in share
    assert "L" not in perf and "A" in perf


def test_commander_grouping_for_edh(repo, seed):
    seed.event(1, "cEDH", date(2026, 10, 5))
    seed.deck(1, "cEDH", "Partner WUBR", commander="Kraum + Tymna")
    seed.deck(1, "cEDH", "Partner WUBG", commander="Kraum + Tymna")
    groups = {g for _, g, _ in fetch(repo, "SELECT * FROM meta_share_series('cEDH', '7 days', :t_from, :t_to, 10)", t_from=FROM, t_to=TO)}
    assert groups == {"Kraum + Tymna"}


def test_rising_deltas(repo, seed):
    seed.event(1, "MO", date(2026, 10, 10))  # previous period
    seed.many(1, "MO", "A", 2, rank=5)
    seed.many(1, "MO", "B", 8, rank=5)
    seed.event(2, "MO", date(2026, 10, 30))  # current period
    seed.many(2, "MO", "A", 6, rank=1)
    seed.many(2, "MO", "B", 4, rank=5)
    rows = fetch(repo, "SELECT * FROM rising('MO', '14 days', 1, :as_of)", as_of=AS_OF)
    by = {r[0]: r for r in rows}
    group, decks, share, share_delta, avg_points, points_delta, win_pct = by["A"]
    assert decks == 6
    assert float(share) == 60 and float(share_delta) == 40
    assert float(avg_points) == 8 and float(points_delta) == 6
    assert float(win_pct) == 100
    assert rows[0][0] == "A"  # sorted by share_delta


def test_rising_respects_min_decks(repo, seed):
    seed.event(2, "MO", date(2026, 10, 30))
    seed.many(2, "MO", "A", 2)
    seed.many(2, "MO", "B", 6)
    groups = {r[0] for r in fetch(repo, "SELECT * FROM rising('MO', '14 days', 5, :as_of)", as_of=AS_OF)}
    assert groups == {"B"}


def test_share_series_empty_bucket_no_division_error(repo):
    assert fetch(repo, "SELECT * FROM meta_share_series('VI', '7 days', :t_from, :t_to, 10)", t_from=FROM, t_to=TO) == []
    assert fetch(repo, "SELECT * FROM performance_series('VI', '7 days', :t_from, :t_to, 10)", t_from=FROM, t_to=TO) == []
    assert fetch(repo, "SELECT * FROM rising('VI', '14 days', 1, :as_of)", as_of=AS_OF) == []


def test_group_breakdown_counts_deck_names(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    seed.many(1, "MO", "Rakdos Aggro", 3, name="Rakdos Delirium")
    seed.many(1, "MO", "Rakdos Aggro", 1, name="Rakdos Scam")
    rows = fetch(repo, "SELECT * FROM group_breakdown('MO', 'Rakdos Aggro', :t_from, :t_to)", t_from=FROM, t_to=TO)
    assert rows == [("Rakdos Delirium", 3), ("Rakdos Scam", 1)]


def test_rising_new_group_has_null_points_delta(repo, seed):
    seed.event(1, "MO", date(2026, 10, 10))
    seed.many(1, "MO", "B", 4)
    seed.event(2, "MO", date(2026, 10, 30))
    seed.many(2, "MO", "A", 4, rank=3)
    by = {r[0]: r for r in fetch(repo, "SELECT * FROM rising('MO', '14 days', 1, :as_of)", as_of=AS_OF)}
    assert by["A"][4] == 4  # avg_points
    assert by["A"][5] is None  # points_delta: no previous data
    assert float(by["A"][3]) == 100  # share_delta: it was 0% before


def test_group_share_series_has_zero_buckets(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    seed.many(1, "MO", "A", 1)
    seed.many(1, "MO", "B", 3)
    seed.event(2, "MO", date(2026, 10, 20))
    seed.many(2, "MO", "B", 2)
    rows = fetch(repo, "SELECT share FROM group_share_series('MO', 'A', '7 days', :t_from, :t_to)",
                 t_from=FROM, t_to=TO)
    assert [float(s) for (s,) in rows] == [25.0, 0.0]


def test_group_top_cards_counts_only_decks_with_a_list(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    d1 = seed.deck(1, "MO", "A")
    d2 = seed.deck(1, "MO", "A")
    seed.deck(1, "MO", "A")  # no list scraped: not in the denominator
    other = seed.deck(1, "MO", "B")
    seed.cards(d1, ("main", "Bolt", 4), ("main", "Island", 2), ("side", "Pyroblast", 2))
    seed.cards(d2, ("main", "Bolt", 3))
    seed.cards(other, ("main", "Bolt", 4))
    rows = fetch(repo, "SELECT * FROM group_top_cards('MO', 'A', :t_from, :t_to)", t_from=FROM, t_to=TO)
    assert [(s, n, d, float(p), float(q)) for s, n, d, p, q in rows] == [
        ("main", "Bolt", 2, 100.0, 3.5),
        ("main", "Island", 1, 50.0, 2.0),
        ("side", "Pyroblast", 1, 50.0, 2.0),
    ]


def test_group_summary(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    seed.deck(1, "MO", "A", rank=1)
    seed.deck(1, "MO", "A", rank=5)
    seed.many(1, "MO", "B", 2)
    seed.event(2, "MO", date(2026, 10, 6), league=True)
    seed.deck(2, "MO", "A", rank=1)
    rows = fetch(repo, "SELECT * FROM group_summary('MO', 'A', :t_from, :t_to)", t_from=FROM, t_to=TO)
    [(decks, share, avg_points, win_pct)] = rows
    assert (decks, float(share), float(avg_points), float(win_pct)) == (3, 60.0, 5.0, 50.0)


def test_group_summary_unknown_group(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    seed.many(1, "MO", "B", 2)
    rows = fetch(repo, "SELECT * FROM group_summary('MO', 'A', :t_from, :t_to)", t_from=FROM, t_to=TO)
    assert rows == [(0, 0, None, None)]


def test_group_performance_series_excludes_leagues(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    seed.deck(1, "MO", "A", rank=1)
    seed.deck(1, "MO", "A", rank=3)
    seed.event(2, "MO", date(2026, 10, 6), league=True)
    seed.deck(2, "MO", "A", rank=17)
    rows = fetch(repo, "SELECT avg_points FROM group_performance_series('MO', 'A', '7 days', :t_from, :t_to)",
                 t_from=FROM, t_to=TO)
    assert [float(a) for (a,) in rows] == [6.0]
