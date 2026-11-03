from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

FROM = datetime(2026, 9, 1, tzinfo=timezone.utc)
TO = datetime(2026, 11, 3, tzinfo=timezone.utc)
AS_OF = datetime(2026, 11, 3, tzinfo=timezone.utc)


class Seeder:
    def __init__(self, repo):
        self.repo = repo
        self.next_deck = 1
        self.archetypes: dict[tuple[str, str], int] = {}

    def event(self, eid: int, fmt: str, d: date, league: bool = False):
        self.repo.conn.execute(
            "INSERT INTO event (id, format_code, name, event_date, is_league) VALUES (%s,%s,%s,%s,%s)",
            (eid, fmt, f"ev{eid}", d, league),
        )

    def deck(self, eid: int, fmt: str, arch: str, rank: int = 5, commander: str | None = None,
             name: str | None = None):
        key = (fmt, arch)
        if key not in self.archetypes:
            aid = len(self.archetypes) + 1
            self.archetypes[key] = aid
            self.repo.conn.execute(
                "INSERT INTO archetype (id, format_code, name) VALUES (%s,%s,%s)", (aid, fmt, arch)
            )
        self.repo.conn.execute(
            "INSERT INTO deck (id, event_id, rank_raw, rank_min, rank_max, deck_name, player, archetype_id, commander)"
            " VALUES (%s,%s,%s,%s,%s,%s,'p',%s,%s)",
            (self.next_deck, eid, str(rank), rank, rank, name or arch, self.archetypes[key], commander),
        )
        self.next_deck += 1

    def many(self, eid, fmt, arch, n, **kw):
        for _ in range(n):
            self.deck(eid, fmt, arch, **kw)


@pytest.fixture
def seed(repo):
    s = Seeder(repo)
    yield s


def q(repo, sql, *args):
    rows = repo.conn.execute(sql, args).fetchall()
    repo.conn.commit()
    return rows


def test_placement_points_weights(repo):
    got = [q(repo, "SELECT placement_points(%s)", r)[0][0] for r in (1, 2, 3, 5, 9, 17)]
    assert got == [8, 6, 4, 2, 1, Decimal("0.5")]


def test_share_series_sums_to_100_per_bucket(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    seed.many(1, "MO", "A", 3)
    seed.many(1, "MO", "B", 1)
    seed.event(2, "MO", date(2026, 10, 20))
    seed.many(2, "MO", "A", 1)
    seed.many(2, "MO", "C", 4)
    rows = q(repo, "SELECT time, sum(share) FROM meta_share_series('MO', '7 days', %s, %s, 100) GROUP BY time",
             FROM, TO)
    assert len(rows) == 2
    assert all(abs(float(total) - 100) < 1e-6 for _, total in rows)


def test_share_includes_leagues_performance_excludes_them(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    seed.many(1, "MO", "A", 4, rank=1)
    seed.event(2, "MO", date(2026, 10, 5), league=True)
    seed.many(2, "MO", "L", 4, rank=1)
    share = {g for _, g, _ in q(repo, "SELECT * FROM meta_share_series('MO', '7 days', %s, %s, 10)", FROM, TO)}
    perf = {g for _, g, _ in q(repo, "SELECT * FROM performance_series('MO', '7 days', %s, %s, 10)", FROM, TO)}
    assert "L" in share
    assert "L" not in perf and "A" in perf


def test_commander_grouping_for_edh(repo, seed):
    seed.event(1, "cEDH", date(2026, 10, 5))
    seed.deck(1, "cEDH", "Partner WUBR", commander="Kraum + Tymna")
    seed.deck(1, "cEDH", "Partner WUBG", commander="Kraum + Tymna")
    groups = {g for _, g, _ in q(repo, "SELECT * FROM meta_share_series('cEDH', '7 days', %s, %s, 10)", FROM, TO)}
    assert groups == {"Kraum + Tymna"}


def test_rising_deltas(repo, seed):
    seed.event(1, "MO", date(2026, 10, 10))  # periodo precedente
    seed.many(1, "MO", "A", 2, rank=5)
    seed.many(1, "MO", "B", 8, rank=5)
    seed.event(2, "MO", date(2026, 10, 30))  # periodo corrente
    seed.many(2, "MO", "A", 6, rank=1)
    seed.many(2, "MO", "B", 4, rank=5)
    rows = q(repo, "SELECT * FROM rising('MO', '14 days', 1, %s)", AS_OF)
    by = {r[0]: r for r in rows}
    group, decks, share, share_delta, avg_points, points_delta, win_pct = by["A"]
    assert decks == 6
    assert float(share) == 60 and float(share_delta) == 40
    assert float(avg_points) == 8 and float(points_delta) == 6
    assert float(win_pct) == 100
    assert rows[0][0] == "A"  # ordinato per share_delta


def test_rising_respects_min_decks(repo, seed):
    seed.event(2, "MO", date(2026, 10, 30))
    seed.many(2, "MO", "A", 2)
    seed.many(2, "MO", "B", 6)
    groups = {r[0] for r in q(repo, "SELECT * FROM rising('MO', '14 days', 5, %s)", AS_OF)}
    assert groups == {"B"}


def test_share_series_empty_bucket_no_division_error(repo):
    assert q(repo, "SELECT * FROM meta_share_series('VI', '7 days', %s, %s, 10)", FROM, TO) == []
    assert q(repo, "SELECT * FROM performance_series('VI', '7 days', %s, %s, 10)", FROM, TO) == []
    assert q(repo, "SELECT * FROM rising('VI', '14 days', 1, %s)", AS_OF) == []


def test_group_breakdown_counts_deck_names(repo, seed):
    seed.event(1, "MO", date(2026, 10, 5))
    seed.many(1, "MO", "Rakdos Aggro", 3, name="Rakdos Delirium")
    seed.many(1, "MO", "Rakdos Aggro", 1, name="Rakdos Scam")
    rows = q(repo, "SELECT * FROM group_breakdown('MO', 'Rakdos Aggro', %s, %s)", FROM, TO)
    assert rows == [("Rakdos Delirium", 3), ("Rakdos Scam", 1)]


def test_rising_new_group_has_null_points_delta(repo, seed):
    seed.event(1, "MO", date(2026, 10, 10))
    seed.many(1, "MO", "B", 4)
    seed.event(2, "MO", date(2026, 10, 30))
    seed.many(2, "MO", "A", 4, rank=3)
    by = {r[0]: r for r in q(repo, "SELECT * FROM rising('MO', '14 days', 1, %s)", AS_OF)}
    assert by["A"][4] == 4  # avg_points
    assert by["A"][5] is None  # points_delta: nessun dato precedente
    assert float(by["A"][3]) == 100  # share_delta: prima era 0%
