"""Confronto tra la nostra quota di meta e il Metagame Breakdown di MTGTop8."""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta

from mtg_meta_scraper.db.repo import Repo
from mtg_meta_scraper.models import MetaShare
from mtg_meta_scraper.parsing.format_page import parse_meta_periods, parse_metagame
from mtg_meta_scraper.pipeline import META_PERIOD_LABEL, Fetcher

log = logging.getLogger("mtg_meta_scraper")

PERIOD_DAYS = 60
TOP = 10
TOLERANCE = 2.0


@dataclass(frozen=True)
class ValidationRow:
    format_code: str
    name: str
    site_pct: float
    ours_pct: float | None
    diff: float | None
    ok: bool


def compare_shares(
    site: list[MetaShare],
    ours: dict[str, float],
    *,
    format_code: str,
    top: int = TOP,
    tolerance: float = TOLERANCE,
) -> list[ValidationRow]:
    rows = []
    for s in sorted(site, key=lambda s: -s.share_pct)[:top]:
        mine = ours.get(s.name)
        diff = None if mine is None else abs(mine - s.share_pct)
        rows.append(ValidationRow(format_code, s.name, s.share_pct, mine, diff, diff is not None and diff <= tolerance))
    return rows


def our_shares(repo: Repo, format_code: str, today: date) -> dict[str, float]:
    return repo.archetype_shares(format_code, today - timedelta(days=PERIOD_DAYS))


def validate(fetcher: Fetcher, repo: Repo, formats: Collection[str], today: date) -> list[ValidationRow]:
    result: list[ValidationRow] = []
    for code in formats:
        page = fetcher.get_text(f"format?f={code}")
        meta_id = parse_meta_periods(page).get(META_PERIOD_LABEL) if page else None
        if meta_id is None:
            log.warning("formato %s: periodo %r non disponibile, salto", code, META_PERIOD_LABEL)
            continue
        site_page = fetcher.get_text(f"format?f={code}&meta={meta_id}")
        if not site_page:
            continue
        result += compare_shares(parse_metagame(site_page), our_shares(repo, code, today), format_code=code)
    return result
