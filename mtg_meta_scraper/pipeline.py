"""Pipeline di scraping: singolo evento, backfill riprendibile, aggiornamento giornaliero."""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from mtg_meta_scraper.config import Settings
from mtg_meta_scraper.db.repo import Repo
from mtg_meta_scraper.models import ParsedEvent
from mtg_meta_scraper.parsing.event import parse_deck_detail, parse_event
from mtg_meta_scraper.parsing.format_page import parse_latest_event_id, parse_meta_periods, parse_metagame

log = logging.getLogger("mtg_meta_scraper")

MAX_ATTEMPTS = 3
OLD_EVENTS_STOP = 300
# Interruttore di sicurezza: tante pagine mancanti o in errore di fila = sito giù, blocco o HTML cambiato.
MAX_CONSECUTIVE_FAILURES = 100
META_PERIOD_LABEL = "Last 2 Months"


class Fetcher(Protocol):
    def get_text(self, path: str) -> str | None: ...


@dataclass
class BackfillStats:
    scanned: int = 0
    done: int = 0
    skipped: int = 0
    missing: int = 0
    errors: int = 0
    aborted: bool = False
    failure_streak: int = 0

    def add(self, status: str) -> None:
        self.scanned += 1
        if status == "done":
            self.done += 1
        elif status == "skipped":
            self.skipped += 1
        elif status == "missing":
            self.missing += 1
        else:
            self.errors += 1
        self.failure_streak = self.failure_streak + 1 if status in ("missing", "error") else 0

    def should_abort(self) -> bool:
        if self.failure_streak >= MAX_CONSECUTIVE_FAILURES:
            log.error(
                "%s pagine mancanti/in errore di fila: interrompo per non martellare il sito "
                "(sito giù, blocco o HTML cambiato?)",
                self.failure_streak,
            )
            self.aborted = True
        return self.aborted


def scrape_event(
    fetcher: Fetcher,
    repo: Repo,
    event_id: int,
    formats: Collection[str],
    min_date: date | None = None,
) -> ParsedEvent | None:
    """Scarica e salva un evento con i dettagli dei mazzi. Non solleva: gli errori finiscono in scan_state."""
    status, ev = _scrape_event(fetcher, repo, event_id, formats, min_date)
    log.info(
        "evento %s: %s, formato=%s, mazzi=%s",
        event_id, status, ev.format_label if ev else "-", len(ev.decks) if ev else 0,
    )
    return ev if status == "done" else None


def _scrape_event(fetcher, repo, event_id, formats, min_date) -> tuple[str, ParsedEvent | None]:
    try:
        html = fetcher.get_text(f"event?e={event_id}")
        ev = parse_event(html, event_id) if html else None
        if ev is None:
            repo.mark_scan(event_id, "missing")
            return "missing", None
        if ev.format_code is None or ev.format_code not in formats or (min_date and ev.event_date < min_date):
            repo.mark_scan(event_id, "skipped", ev.format_label, ev.event_date)
            return "skipped", ev
        repo.upsert_event(ev)
        if ev.shown_deck is not None and ev.shown_deck.deck_id in repo.decks_missing_detail(event_id):
            repo.save_deck_detail(event_id, ev.format_code, ev.shown_deck)
        for deck_id in repo.decks_missing_detail(event_id):
            deck_html = fetcher.get_text(f"event?e={event_id}&d={deck_id}&f={ev.format_code}")
            if deck_html:
                repo.save_deck_detail(event_id, ev.format_code, parse_deck_detail(deck_html, deck_id))
        repo.mark_scan(event_id, "done", ev.format_label, ev.event_date)
        return "done", ev
    except Exception:  # noqa: BLE001 - un evento rotto non deve fermare il backfill
        log.exception("evento %s: errore", event_id)
        repo.conn.rollback()
        repo.mark_scan(event_id, "error")
        return "error", None


def _latest_event_id(fetcher: Fetcher) -> int:
    html = fetcher.get_text("")
    if not html:
        raise RuntimeError("home page MTGTop8 non disponibile")
    return parse_latest_event_id(html)


def backfill(fetcher: Fetcher, repo: Repo, settings: Settings, today: date) -> BackfillStats:
    """Scorre gli ID evento all'indietro finché trova OLD_EVENTS_STOP eventi consecutivi fuori finestra."""
    cutoff = today - timedelta(days=settings.backfill_days)
    stats = BackfillStats()
    old_streak = 0
    for event_id in range(_latest_event_id(fetcher), 0, -1):
        state = repo.scan_state(event_id)
        if state is not None:
            status, attempts, ev_date = state
            if status in ("done", "missing", "skipped") or attempts >= MAX_ATTEMPTS:
                old_streak = _update_streak(old_streak, ev_date, cutoff)
                if old_streak >= OLD_EVENTS_STOP:
                    break
                continue
        status, ev = _scrape_event(fetcher, repo, event_id, settings.formats, cutoff)
        stats.add(status)
        log.info("backfill evento %s: %s (%s)", event_id, status, ev.event_date if ev else "-")
        old_streak = _update_streak(old_streak, ev.event_date if ev else None, cutoff)
        if old_streak >= OLD_EVENTS_STOP or stats.should_abort():
            break
    log.info("backfill concluso: %s", stats)
    return stats


def _update_streak(streak: int, ev_date: date | None, cutoff: date) -> int:
    if ev_date is None:
        return streak
    return streak + 1 if ev_date < cutoff else 0


def update(fetcher: Fetcher, repo: Repo, settings: Settings, today: date) -> BackfillStats:
    """Eventi nuovi + ricontrollo degli ultimi giorni + macro-categorie dalle pagine formato."""
    cutoff = today - timedelta(days=settings.backfill_days)
    stats = BackfillStats()
    latest = _latest_event_id(fetcher)
    known = repo.max_known_event_id()
    start = latest if known is None else known + 1
    for event_id in range(start, latest + 1):
        status, _ = _scrape_event(fetcher, repo, event_id, settings.formats, cutoff)
        stats.add(status)
        if stats.should_abort():
            return stats

    recheck_from = today - timedelta(days=settings.recheck_days)
    for event_id, _code in repo.events_since(recheck_from, settings.formats):
        status, _ = _scrape_event(fetcher, repo, event_id, settings.formats, None)
        stats.add(status)

    for code in settings.formats:
        _refresh_macro(fetcher, repo, code)
    log.info("update concluso: %s", stats)
    return stats


def _refresh_macro(fetcher: Fetcher, repo: Repo, code: str) -> None:
    try:
        page = fetcher.get_text(f"format?f={code}")
        meta_id = parse_meta_periods(page).get(META_PERIOD_LABEL) if page else None
        if meta_id is None:
            log.warning("formato %s: periodo %r non trovato", code, META_PERIOD_LABEL)
            return
        meta_page = fetcher.get_text(f"format?f={code}&meta={meta_id}")
        if meta_page:
            repo.set_archetype_macro(parse_metagame(meta_page), code)
    except Exception:  # noqa: BLE001
        log.exception("formato %s: aggiornamento macro-categorie fallito", code)
        repo.conn.rollback()
