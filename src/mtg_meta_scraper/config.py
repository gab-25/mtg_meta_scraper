"""Configurazione da variabili d'ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_FORMATS = ("ST", "PI", "MO", "LE", "VI", "PAU", "EDH", "cEDH")


@dataclass(frozen=True)
class Settings:
    database_url: str
    request_interval: float = 1.5
    backfill_days: int = 183
    formats: tuple[str, ...] = DEFAULT_FORMATS
    recheck_days: int = 3
    update_hour_utc: int = 6
    base_url: str = "https://mtgtop8.com/"

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        formats = env.get("MTGMETA_FORMATS")
        return cls(
            database_url=env.get("DATABASE_URL", "postgresql://mtgmeta:mtgmeta@localhost:5432/mtgmeta"),
            request_interval=float(env.get("MTGMETA_REQUEST_INTERVAL", "1.5")),
            backfill_days=int(env.get("MTGMETA_BACKFILL_DAYS", "183")),
            formats=tuple(f.strip() for f in formats.split(",") if f.strip()) if formats else DEFAULT_FORMATS,
            recheck_days=int(env.get("MTGMETA_RECHECK_DAYS", "3")),
            update_hour_utc=int(env.get("MTGMETA_UPDATE_HOUR_UTC", "6")),
        )
