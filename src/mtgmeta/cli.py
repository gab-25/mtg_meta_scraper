"""Riga di comando: `mtgmeta init-db | backfill | update | validate | run`."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from datetime import UTC, date, datetime, timedelta

from mtgmeta.config import Settings
from mtgmeta.db.repo import Repo
from mtgmeta.http import PoliteClient
from mtgmeta.pipeline import backfill, update
from mtgmeta.validate import ValidationRow, validate

log = logging.getLogger("mtgmeta")


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
        repo.conn.close()


def _print_validation(rows: list[ValidationRow]) -> None:
    print(f"{'formato':<8} {'archetipo':<32} {'sito %':>7} {'nostro %':>9} {'diff':>6}  esito")
    for r in rows:
        ours = "-" if r.ours_pct is None else f"{r.ours_pct:.1f}"
        diff = "-" if r.diff is None else f"{r.diff:.1f}"
        print(f"{r.format_code:<8} {r.name[:32]:<32} {r.site_pct:>7.1f} {ours:>9} {diff:>6}  {'ok' if r.ok else 'SCARTO'}")


def _next_run(now: datetime, hour: int) -> datetime:
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def _run_scheduler(settings: Settings) -> None:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    client, repo = _open(settings)
    try:
        repo.init_schema()
        backfill(client, repo, settings, _today())
        while not stop.is_set():
            nxt = _next_run(datetime.now(UTC), settings.update_hour_utc)
            log.info("prossimo aggiornamento: %s", nxt.isoformat())
            if stop.wait((nxt - datetime.now(UTC)).total_seconds()):
                break
            try:
                update(client, repo, settings, _today())
            except Exception:  # noqa: BLE001 - lo scheduler non deve morire
                log.exception("aggiornamento fallito")
                repo.conn.rollback()
    finally:
        client.close()
        repo.conn.close()
        log.info("scheduler terminato")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mtgmeta", description="Scraper metagame MTGTop8")
    parser.add_argument("command", choices=["init-db", "backfill", "update", "validate", "run"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()

    if args.command == "validate":
        rows = _run_validate(settings)
        _print_validation(rows)
        if any(not r.ok for r in rows):
            raise SystemExit(1)
        return
    if args.command == "run":
        _run_scheduler(settings)
        return

    client, repo = _open(settings)
    try:
        repo.init_schema()
        if args.command == "backfill":
            backfill(client, repo, settings, _today())
        elif args.command == "update":
            update(client, repo, settings, _today())
    finally:
        client.close()
        repo.conn.close()


if __name__ == "__main__":
    main()
