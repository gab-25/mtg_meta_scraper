"""Configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_FORMATS = ("ST", "PI", "MO", "LE", "VI", "PAU", "EDH", "cEDH")


@dataclass(frozen=True)
class Settings:
    database_url: str
    request_interval: float = 1.5
    backfill_days: int = 90
    formats: tuple[str, ...] = DEFAULT_FORMATS
    recheck_days: int = 3
    update_hour_utc: int = 6
    base_url: str = "https://mtgtop8.com/"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        formats = env.get("MTG_META_SCRAPER_FORMATS")
        return cls(
            database_url=env.get("DATABASE_URL", "postgresql://mtg_meta_scraper:mtg_meta_scraper@localhost:5432/mtg_meta_scraper"),
            request_interval=float(env.get("MTG_META_SCRAPER_REQUEST_INTERVAL", "1.5")),
            backfill_days=int(env.get("MTG_META_SCRAPER_BACKFILL_DAYS", "90")),
            formats=tuple(f.strip() for f in formats.split(",") if f.strip()) if formats else DEFAULT_FORMATS,
            recheck_days=int(env.get("MTG_META_SCRAPER_RECHECK_DAYS", "3")),
            update_hour_utc=int(env.get("MTG_META_SCRAPER_UPDATE_HOUR_UTC", "6")),
        )
