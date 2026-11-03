"""Accesso al database: schema, upsert idempotenti, stato di scansione."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date
from pathlib import Path

import psycopg

from mtg_meta_scraper.models import DeckDetail, MetaShare, ParsedEvent

_SQL_DIR = Path(__file__).parent


class Repo:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    @classmethod
    def connect(cls, url: str) -> Repo:
        return cls(psycopg.connect(url))

    def init_schema(self) -> None:
        for name in ("schema.sql", "metrics.sql"):
            path = _SQL_DIR / name
            if path.exists():
                self.conn.execute(path.read_text())
        self.conn.commit()

    def upsert_event(self, ev: ParsedEvent) -> None:
        if ev.format_code is None:
            raise ValueError(f"evento {ev.event_id}: formato non target {ev.format_label!r}")
        with self.conn.transaction():
            self.conn.execute(
                """
                INSERT INTO event (id, format_code, name, event_date, players, source, stars,
                                   is_major, is_online, is_league, scraped_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    format_code = EXCLUDED.format_code, name = EXCLUDED.name,
                    event_date = EXCLUDED.event_date, players = EXCLUDED.players,
                    source = EXCLUDED.source, stars = EXCLUDED.stars, is_major = EXCLUDED.is_major,
                    is_online = EXCLUDED.is_online, is_league = EXCLUDED.is_league,
                    scraped_at = now()
                """,
                (ev.event_id, ev.format_code, ev.name, ev.event_date, ev.players, ev.source,
                 ev.stars, ev.is_major, ev.is_online, ev.is_league),
            )
            with self.conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO deck (id, event_id, rank_raw, rank_min, rank_max, deck_name, player)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        event_id = EXCLUDED.event_id, rank_raw = EXCLUDED.rank_raw,
                        rank_min = EXCLUDED.rank_min, rank_max = EXCLUDED.rank_max,
                        deck_name = EXCLUDED.deck_name, player = EXCLUDED.player
                    """,
                    [(d.deck_id, ev.event_id, d.rank_raw, d.rank_min, d.rank_max, d.deck_name, d.player)
                     for d in ev.decks],
                )

    def save_deck_detail(self, event_id: int, format_code: str, d: DeckDetail) -> None:
        with self.conn.transaction():
            if d.archetype_id is not None:
                self.conn.execute(
                    """
                    INSERT INTO archetype (id, format_code, name) VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                    """,
                    (d.archetype_id, format_code, d.archetype_name or "?"),
                )
            self.conn.execute(
                """
                UPDATE deck SET archetype_id = COALESCE(%s, archetype_id), commander = %s,
                                detail_scraped = true
                WHERE id = %s AND event_id = %s
                """,
                (d.archetype_id, d.commander_key, d.deck_id, event_id),
            )
            self.conn.execute("DELETE FROM deck_card WHERE deck_id = %s", (d.deck_id,))
            # Righe duplicate (stessa carta nella stessa sezione) vengono sommate.
            merged: dict[tuple[str, str], int] = {}
            for c in d.cards:
                merged[(c.section, c.card_name)] = merged.get((c.section, c.card_name), 0) + c.qty
            with self.conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO deck_card (deck_id, section, card_name, qty) VALUES (%s, %s, %s, %s)",
                    [(d.deck_id, s, n, q) for (s, n), q in merged.items()],
                )

    def set_archetype_macro(self, shares: list[MetaShare], format_code: str) -> None:
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO archetype (id, format_code, name, macro) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET macro = COALESCE(EXCLUDED.macro, archetype.macro)
                    """,
                    [(s.archetype_id, format_code, s.name, s.macro) for s in shares],
                )

    def mark_scan(
        self, event_id: int, status: str, format_label: str | None = None, event_date: date | None = None
    ) -> None:
        with self.conn.transaction():
            self.conn.execute(
                """
                INSERT INTO scan_state (event_id, status, format_label, event_date, attempts, updated_at)
                VALUES (%s, %s, %s, %s, 1, now())
                ON CONFLICT (event_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    format_label = COALESCE(EXCLUDED.format_label, scan_state.format_label),
                    event_date = COALESCE(EXCLUDED.event_date, scan_state.event_date),
                    attempts = scan_state.attempts + 1, updated_at = now()
                """,
                (event_id, status, format_label, event_date),
            )

    def scan_state(self, event_id: int) -> tuple[str, int, date | None] | None:
        """(status, attempts, event_date) oppure None."""
        row = self.conn.execute(
            "SELECT status, attempts, event_date FROM scan_state WHERE event_id = %s", (event_id,)
        ).fetchone()
        self.conn.commit()
        return row

    def scan_status(self, event_id: int) -> str | None:
        st = self.scan_state(event_id)
        return st[0] if st else None

    def max_known_event_id(self) -> int | None:
        # missing/error non contano: verranno ritentati dal prossimo update.
        row = self.conn.execute(
            "SELECT max(event_id) FROM scan_state WHERE status IN ('done', 'skipped')"
        ).fetchone()
        self.conn.commit()
        return row[0]

    def events_since(self, d: date, formats: Collection[str]) -> list[tuple[int, str]]:
        rows = self.conn.execute(
            "SELECT id, format_code FROM event WHERE event_date >= %s AND format_code = ANY(%s) ORDER BY id",
            (d, list(formats)),
        ).fetchall()
        self.conn.commit()
        return rows

    def decks_missing_detail(self, event_id: int) -> list[int]:
        """Mazzi senza dettaglio o ancora senza categoria (MTGTop8 li categorizza in ritardo)."""
        rows = self.conn.execute(
            "SELECT id FROM deck WHERE event_id = %s AND (NOT detail_scraped OR archetype_id IS NULL)"
            " ORDER BY rank_min, id",
            (event_id,),
        ).fetchall()
        self.conn.commit()
        return [r[0] for r in rows]
