from datetime import date

import httpx
import pytest

from conftest import scalar
from mtg_meta_scraper.config import Settings
from mtg_meta_scraper.pipeline import backfill, scrape_event, update, update_with_retries

TODAY = date(2026, 11, 3)


class FakeFetcher:
    """Return HTML by path; record the requests."""

    def __init__(self, load_fixture, latest: int = 93105, events: dict | None = None, default=None):
        self.load = load_fixture
        self.latest = latest
        self.events = events or {}
        self.default = default
        self.requests: list[str] = []
        self.formats: dict[str, str] = {}
        self.deck_html = lambda: load_fixture("event_mo_93105_d907299.html")

    def get_text(self, path: str) -> str | None:
        self.requests.append(path)
        if path in ("", "/"):
            return f"<a href=event?e={self.latest}&f=MO>x</a><a href=event?e=5&f=MO>y</a>"
        if path.startswith("format?"):
            return self.formats.get(path)
        if path.startswith("event?e="):
            eid = int(path.split("=")[1].split("&")[0])
            if "&d=" in path:
                return self.deck_html()
            html = self.events.get(eid, self.default)
            return html() if callable(html) else html
        return None


def settings(**kw) -> Settings:
    return Settings(database_url="unused", request_interval=0, formats=("MO", "cEDH"), **kw)


def _old_league(load_fixture) -> str:
    return load_fixture("event_mo_league_93120.html").replace("02/11/26", "01/01/25")


def _premodern(load_fixture) -> str:
    return load_fixture("event_mo_league_93120.html").replace(
        'class=meta_arch style="padding:2px;">Modern', 'class=meta_arch style="padding:2px;">Premodern'
    )


@pytest.fixture
def fake(load_fixture):
    return FakeFetcher(load_fixture, events={93105: load_fixture("event_mo_93105.html")})


def test_scrape_event_saves_all_deck_details(repo, fake):
    scrape_event(fake, repo, 93105, ("MO",))
    assert fake.requests[0] == "event?e=93105"
    assert len(fake.requests) == 16  # event page + 15 decks
    assert scalar(repo, "SELECT archetype_id FROM deck WHERE id = 907299") == 209
    assert scalar(repo, "SELECT count(*) FROM deck WHERE NOT detail_scraped") == 0
    assert repo.scan_status(93105) == "done"


def test_scrape_event_does_not_refetch_known_details(repo, fake):
    scrape_event(fake, repo, 93105, ("MO",))
    fake.requests.clear()
    scrape_event(fake, repo, 93105, ("MO",))
    assert fake.requests == ["event?e=93105"]


def test_backfill_skips_missing_and_non_target_events(repo, load_fixture):
    league = load_fixture("event_mo_league_93120.html")
    f = FakeFetcher(load_fixture, latest=12, events={12: league, 11: None, 10: _premodern(load_fixture)},
                    default=lambda: _old_league(load_fixture))
    stats = backfill(f, repo, settings(), TODAY)
    assert repo.scan_status(12) == "done"
    assert repo.scan_status(11) == "missing"
    assert repo.scan_status(10) == "skipped"
    assert stats.done == 1 and stats.missing == 1 and stats.skipped >= 1


def test_backfill_resumes_without_refetching_done_events(repo, load_fixture):
    f = FakeFetcher(load_fixture, latest=12, events={12: load_fixture("event_mo_league_93120.html")},
                    default=lambda: _old_league(load_fixture))
    repo.mark_scan(12, "done")
    backfill(f, repo, settings(), TODAY)
    assert "event?e=12" not in f.requests


def test_backfill_stops_after_300_old_events(repo, load_fixture):
    f = FakeFetcher(load_fixture, latest=1000, default=lambda: _old_league(load_fixture))
    stats = backfill(f, repo, settings(), TODAY)
    assert stats.scanned == 300
    assert "event?e=700" not in f.requests
    # events outside the window are neither stored nor drilled into
    assert scalar(repo, "SELECT count(*) FROM event") == 0
    assert not any("&d=" in r for r in f.requests)


def test_error_event_is_retried_at_most_three_times(repo, load_fixture):
    def boom():
        raise httpx.ConnectError("down")

    f = FakeFetcher(load_fixture, latest=2, events={2: boom}, default=None)
    for _ in range(5):
        backfill(f, repo, settings(), TODAY)
    assert repo.scan_status(2) == "error"
    assert f.requests.count("event?e=2") == 3


def test_update_rechecks_recent_events(repo, load_fixture):
    league = load_fixture("event_mo_league_93120.html")
    f = FakeFetcher(load_fixture, latest=12, events={12: league})
    f.formats = {"format?f=MO": load_fixture("format_mo_meta51.html"),
                 "format?f=MO&meta=51": load_fixture("format_mo_meta51.html")}
    scrape_event(f, repo, 12, ("MO",))
    repo.mark_scan(12, "done")
    f.requests.clear()
    update(f, repo, settings(), TODAY)
    assert "event?e=12" in f.requests
    assert "format?f=MO&meta=51" in f.requests
    assert scalar(repo, "SELECT macro FROM archetype WHERE name = 'UR Aggro'") == "Aggro"


def test_recheck_refetches_decks_without_archetype(repo, fake, load_fixture):
    uncategorised = load_fixture("event_mo_93105_d907299.html").replace("archetype?a=", "nothing?a=")
    fake.deck_html = lambda: uncategorised
    scrape_event(fake, repo, 93105, ("MO",))
    assert scalar(repo, "SELECT archetype_id FROM deck WHERE id = 907299") is None
    fake.deck_html = lambda: load_fixture("event_mo_93105_d907299.html")
    fake.requests.clear()
    scrape_event(fake, repo, 93105, ("MO",))
    assert "event?e=93105&d=907299&f=MO" in fake.requests
    assert scalar(repo, "SELECT archetype_id FROM deck WHERE id = 907299") == 209


def test_deck_page_unavailable_is_retried_on_next_scrape(repo, fake):
    fake.deck_html = lambda: None
    scrape_event(fake, repo, 93105, ("MO",))
    fake.deck_html = lambda: fake.load("event_mo_93105_d907299.html")
    fake.requests.clear()
    scrape_event(fake, repo, 93105, ("MO",))
    assert len(fake.requests) == 16


def test_update_retries_ids_marked_missing(repo, load_fixture):
    f = FakeFetcher(load_fixture, latest=11, events={11: load_fixture("event_mo_league_93120.html")})
    repo.mark_scan(10, "done")
    repo.mark_scan(11, "missing")
    update(f, repo, settings(), TODAY)
    assert repo.scan_status(11) == "done"


def test_update_retries_ids_marked_error(repo, load_fixture):
    f = FakeFetcher(load_fixture, latest=11, events={11: load_fixture("event_mo_league_93120.html")})
    repo.mark_scan(10, "done")
    repo.mark_scan(11, "error")
    update(f, repo, settings(), TODAY)
    assert repo.scan_status(11) == "done"


def test_backfill_stops_after_consecutive_failures(repo, load_fixture):
    def boom():
        raise httpx.ConnectError("down")

    f = FakeFetcher(load_fixture, latest=1000, default=boom)
    stats = backfill(f, repo, settings(), TODAY)
    assert stats.aborted is True
    assert stats.scanned == 100


def test_backfill_stops_after_consecutive_missing_pages(repo, load_fixture):
    f = FakeFetcher(load_fixture, latest=1000, default=None)
    stats = backfill(f, repo, settings(), TODAY)
    assert stats.aborted is True
    assert stats.scanned == 100


def test_update_stops_after_consecutive_failures(repo, load_fixture):
    def boom():
        raise httpx.ConnectError("down")

    f = FakeFetcher(load_fixture, latest=1000, default=boom)
    repo.mark_scan(1, "done")
    stats = update(f, repo, settings(), TODAY)
    assert stats.aborted is True
    assert stats.scanned == 100


class RollbackRepo:
    def __init__(self):
        self.rollbacks = 0
        self.runs: list[tuple[int, str]] = []

    def rollback(self):
        self.rollbacks += 1

    def log_update_run(self, attempt, _started_at, status, scanned=None, error=None):
        self.runs.append((attempt, status))


def _scripted_update(monkeypatch, outcomes):
    """Each outcome is an exception to raise or the `aborted` flag of the returned stats."""
    from types import SimpleNamespace

    from mtg_meta_scraper import pipeline

    it = iter(outcomes)

    def fake_update(*_args):
        outcome = next(it)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(aborted=outcome, scanned=0)

    monkeypatch.setattr(pipeline, "update", fake_update)


def test_update_with_retries_first_attempt_ok(monkeypatch):
    _scripted_update(monkeypatch, [False])
    repo, sleeps = RollbackRepo(), []
    assert update_with_retries(None, repo, settings(), TODAY, sleep=sleeps.append) is True
    assert sleeps == []
    assert repo.runs == [(1, "ok")]


def test_update_with_retries_retries_failures_and_early_stops(monkeypatch):
    _scripted_update(monkeypatch, [RuntimeError("MTGTop8 home page unavailable"), True, False])
    repo, sleeps = RollbackRepo(), []
    assert update_with_retries(None, repo, settings(), TODAY, sleep=sleeps.append) is True
    assert sleeps == [900, 900]
    assert repo.rollbacks == 1
    assert repo.runs == [(1, "failed"), (2, "failed"), (3, "ok")]


def test_update_with_retries_gives_up_after_four_attempts(monkeypatch):
    _scripted_update(monkeypatch, [RuntimeError("down")] * 2 + [True] * 2)
    repo, sleeps = RollbackRepo(), []
    assert update_with_retries(None, repo, settings(), TODAY, sleep=sleeps.append) is False
    assert sleeps == [900, 900, 900]
    assert [s for _, s in repo.runs] == ["failed"] * 4
