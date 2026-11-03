"""Strutture dati condivise da parser, repository e pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

Section = Literal["main", "side", "commander"]


@dataclass(frozen=True)
class DeckRow:
    deck_id: int
    rank_raw: str
    rank_min: int
    rank_max: int
    deck_name: str
    player: str


@dataclass(frozen=True)
class CardLine:
    section: Section
    card_name: str
    qty: int


@dataclass(frozen=True)
class DeckDetail:
    deck_id: int
    archetype_id: int | None
    archetype_name: str | None
    cards: tuple[CardLine, ...]

    @property
    def commanders(self) -> tuple[str, ...]:
        return tuple(sorted(c.card_name for c in self.cards if c.section == "commander"))

    @property
    def commander_key(self) -> str | None:
        return " + ".join(self.commanders) or None


@dataclass(frozen=True)
class ParsedEvent:
    event_id: int
    format_code: str | None
    format_label: str
    name: str
    event_date: date
    players: int | None
    source: str | None
    stars: int
    is_major: bool
    is_online: bool | None
    is_league: bool
    decks: tuple[DeckRow, ...]
    shown_deck: DeckDetail | None


@dataclass(frozen=True)
class MetaShare:
    archetype_id: int
    name: str
    macro: str | None
    share_pct: float
