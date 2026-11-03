"""Database access: migrations, idempotent upserts, scan state."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date
from pathlib import Path

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mtg_meta_scraper.db.engine import make_engine, migrate
from mtg_meta_scraper.db.tables import Archetype, Deck, DeckCard, Event, ScanState
from mtg_meta_scraper.models import DeckDetail, MetaShare, ParsedEvent

METRICS_SQL = Path(__file__).parent / "metrics.sql"


class Repo:
    """Each method opens and closes its own transaction."""

    def __init__(self, session: Session, url: str) -> None:
        self.session = session
        self.url = url

    @classmethod
    def connect(cls, url: str) -> Repo:
        return cls(Session(make_engine(url), autobegin=False), url)

    def close(self) -> None:
        bind = self.session.get_bind()
        self.session.close()
        bind.dispose()

    def rollback(self) -> None:
        self.session.rollback()

    def init_schema(self) -> None:
        """Alembic migrations, then the metrics.sql functions (recreated on every start)."""
        migrate(self.url)
        with self.session.begin():
            # Parameterless DBAPI cursor: '%' in the file must not be treated as placeholders.
            with self.session.connection().connection.cursor() as cur:
                cur.execute(METRICS_SQL.read_text())

    def upsert_event(self, ev: ParsedEvent) -> None:
        if ev.format_code is None:
            raise ValueError(f"event {ev.event_id}: non-target format {ev.format_label!r}")
        event_values = dict(
            id=ev.event_id, format_code=ev.format_code, name=ev.name, event_date=ev.event_date,
            players=ev.players, source=ev.source, stars=ev.stars, is_major=ev.is_major,
            is_online=ev.is_online, is_league=ev.is_league, scraped_at=func.now(),
        )
        with self.session.begin():
            stmt = insert(Event).values(event_values)
            self.session.execute(stmt.on_conflict_do_update(
                index_elements=[Event.id],
                set_={k: stmt.excluded[k] for k in event_values if k != "id"},
            ))
            if not ev.decks:
                return
            # archetype_id, commander and detail_scraped are left untouched: save_deck_detail sets them.
            stmt = insert(Deck).values([
                dict(id=d.deck_id, event_id=ev.event_id, rank_raw=d.rank_raw, rank_min=d.rank_min,
                     rank_max=d.rank_max, deck_name=d.deck_name, player=d.player)
                for d in ev.decks
            ])
            self.session.execute(stmt.on_conflict_do_update(
                index_elements=[Deck.id],
                set_={k: stmt.excluded[k] for k in
                      ("event_id", "rank_raw", "rank_min", "rank_max", "deck_name", "player")},
            ))

    def save_deck_detail(self, event_id: int, format_code: str, d: DeckDetail) -> None:
        with self.session.begin():
            if d.archetype_id is not None:
                stmt = insert(Archetype).values(id=d.archetype_id, format_code=format_code,
                                                name=d.archetype_name or "?")
                self.session.execute(stmt.on_conflict_do_update(
                    index_elements=[Archetype.id], set_={"name": stmt.excluded.name},
                ))
            changes: dict = {"commander": d.commander_key, "detail_scraped": True}
            if d.archetype_id is not None:  # a page without a category does not erase the known one
                changes["archetype_id"] = d.archetype_id
            self.session.execute(
                update(Deck).where(Deck.id == d.deck_id, Deck.event_id == event_id).values(changes)
            )
            self.session.execute(delete(DeckCard).where(DeckCard.deck_id == d.deck_id))
            # Duplicate rows (same card in the same section) are summed.
            merged: dict[tuple[str, str], int] = {}
            for c in d.cards:
                merged[(c.section, c.card_name)] = merged.get((c.section, c.card_name), 0) + c.qty
            if merged:
                self.session.execute(insert(DeckCard).values([
                    dict(deck_id=d.deck_id, section=s, card_name=n, qty=q) for (s, n), q in merged.items()
                ]))

    def set_archetype_macro(self, shares: list[MetaShare], format_code: str) -> None:
        if not shares:
            return
        with self.session.begin():
            stmt = insert(Archetype).values([
                dict(id=s.archetype_id, format_code=format_code, name=s.name, macro=s.macro) for s in shares
            ])
            self.session.execute(stmt.on_conflict_do_update(
                index_elements=[Archetype.id],
                set_={"macro": func.coalesce(stmt.excluded.macro, Archetype.macro)},
            ))

    def mark_scan(
        self, event_id: int, status: str, format_label: str | None = None, event_date: date | None = None
    ) -> None:
        with self.session.begin():
            stmt = insert(ScanState).values(
                event_id=event_id, status=status, format_label=format_label, event_date=event_date,
                attempts=1, updated_at=func.now(),
            )
            self.session.execute(stmt.on_conflict_do_update(
                index_elements=[ScanState.event_id],
                set_={
                    "status": stmt.excluded.status,
                    "format_label": func.coalesce(stmt.excluded.format_label, ScanState.format_label),
                    "event_date": func.coalesce(stmt.excluded.event_date, ScanState.event_date),
                    "attempts": ScanState.attempts + 1,
                    "updated_at": func.now(),
                },
            ))

    def scan_state(self, event_id: int) -> tuple[str, int, date | None] | None:
        """(status, attempts, event_date) or None."""
        with self.session.begin():
            row = self.session.execute(
                select(ScanState.status, ScanState.attempts, ScanState.event_date)
                .where(ScanState.event_id == event_id)
            ).first()
        return tuple(row) if row else None

    def scan_status(self, event_id: int) -> str | None:
        st = self.scan_state(event_id)
        return st[0] if st else None

    def max_known_event_id(self) -> int | None:
        # missing/error do not count: the next update will retry them.
        with self.session.begin():
            return self.session.scalar(
                select(func.max(ScanState.event_id)).where(ScanState.status.in_(("done", "skipped")))
            )

    def events_since(self, d: date, formats: Collection[str]) -> list[tuple[int, str]]:
        with self.session.begin():
            rows = self.session.execute(
                select(Event.id, Event.format_code)
                .where(Event.event_date >= d, Event.format_code.in_(list(formats)))
                .order_by(Event.id)
            ).all()
        return [tuple(r) for r in rows]

    def decks_missing_detail(self, event_id: int) -> list[int]:
        """Decks without detail or still uncategorized (MTGTop8 categorizes them late)."""
        with self.session.begin():
            return list(self.session.scalars(
                select(Deck.id)
                .where(Deck.event_id == event_id, (~Deck.detail_scraped) | Deck.archetype_id.is_(None))
                .order_by(Deck.rank_min, Deck.id)
            ))

    def archetype_shares(self, format_code: str, after: date) -> dict[str, float]:
        """Share (%) of each MTGTop8 archetype for events after `after`."""
        with self.session.begin():
            rows = self.session.execute(
                text("""
                    SELECT archetype_name, 100.0 * count(*) / sum(count(*)) OVER ()
                    FROM deck_facts
                    WHERE format_code = :fmt AND archetype_name IS NOT NULL AND event_date > :after
                    GROUP BY archetype_name
                """),
                {"fmt": format_code, "after": after},
            ).all()
        return {name: float(pct) for name, pct in rows}
