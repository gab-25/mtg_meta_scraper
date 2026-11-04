"""Command line: `python -m mtg_meta_scraper update` (the daily run), `backfill` (once), `init-db`, `validate`."""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, date, datetime

from mtg_meta_scraper.config import Settings
from mtg_meta_scraper.db.repo import Repo
from mtg_meta_scraper.http import PoliteClient
from mtg_meta_scraper.pipeline import backfill, update_with_retries
from mtg_meta_scraper.validate import ValidationRow, validate

log = logging.getLogger("mtg_meta_scraper")


def _today() -> date:
    return datetime.now(UTC).date()


def _open(settings: Settings) -> tuple[PoliteClient, Repo]:
    return PoliteClient(settings.base_url, settings.request_interval), Repo.connect(settings.database_url)


def _run_validate(settings: Settings) -> list[ValidationRow]:
    client, repo = _open(settings)
    try:
        return validate(client, repo, settings.formats, _today())
    finally:
        client.close()
        repo.close()


def _print_validation(rows: list[ValidationRow]) -> None:
    print(f"{'format':<8} {'archetype':<32} {'site %':>7} {'ours %':>9} {'diff':>6}  result")
    for r in rows:
        ours = "-" if r.ours_pct is None else f"{r.ours_pct:.1f}"
        diff = "-" if r.diff is None else f"{r.diff:.1f}"
        print(f"{r.format_code:<8} {r.name[:32]:<32} {r.site_pct:>7.1f} {ours:>9} {diff:>6}  {'ok' if r.ok else 'MISMATCH'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m mtg_meta_scraper", description="MTGTop8 metagame scraper")
    parser.add_argument("command", choices=["init-db", "backfill", "update", "validate"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()

    if args.command == "validate":
        rows = _run_validate(settings)
        _print_validation(rows)
        if any(not r.ok for r in rows):
            raise SystemExit(1)
        return

    client, repo = _open(settings)
    try:
        repo.init_schema()
        if args.command == "backfill" and backfill(client, repo, settings, _today()).aborted:
            raise SystemExit(1)
        if args.command == "update" and not update_with_retries(client, repo, settings, _today()):
            raise SystemExit(1)
    finally:
        client.close()
        repo.close()


if __name__ == "__main__":
    main()
