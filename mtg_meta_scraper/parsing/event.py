"""Parser for the MTGTop8 event page (`event?e=<id>[&d=<deck>]`)."""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from mtg_meta_scraper.models import CardLine, DeckDetail, DeckRow, ParsedEvent, Section
from mtg_meta_scraper.parsing.common import FORMAT_LABELS, parse_rank, parse_site_date, query_int

_DATE_LINE = re.compile(r"(?:(\d+)\s+players\s*-\s*)?(\d{2}/\d{2}/\d{2})")
_MAIN_HEADER = re.compile(r"^\d+\s+[A-Z]")


def _text(node: Node | None) -> str:
    return node.text(deep=True, separator="", strip=False).strip() if node else ""


def parse_event(html: str, event_id: int) -> ParsedEvent | None:
    tree = HTMLParser(html)
    titles = tree.css("div.event_title")
    meta = tree.css_first("div.meta_arch")
    if not titles or meta is None:
        return None

    label = (meta.text(deep=False) or "").strip()
    star_imgs = meta.css("img")
    stars = sum(1 for i in star_imgs if (i.attributes.get("src") or "").endswith("/star.png"))
    is_major = any((i.attributes.get("src") or "").endswith("bigstar.png") for i in star_imgs)
    online_img = next((i for i in star_imgs if "/online/" in (i.attributes.get("src") or "")), None)
    is_online = None if online_img is None else not (online_img.attributes.get("src") or "").endswith("paper.png")

    info = meta.parent
    players = None
    event_date = None
    for div in info.css("div"):
        m = _DATE_LINE.search(_text(div))
        if m and div is not meta:
            players = int(m.group(1)) if m.group(1) else None
            event_date = parse_site_date(m.group(2))
            break
    if event_date is None:
        return None

    source = None
    for div in info.css("div"):
        if _text(div).startswith("Source:"):
            a = div.css_first("a")
            source = _text(a) or None
            break

    name = _text(titles[0])
    decks = tuple(_parse_deck_rows(tree))
    shown_id = decks[0].deck_id if decks else None
    # Without `d` the page shows deck #1; with `d` the caller uses parse_deck_detail.
    shown = parse_deck_detail(html, shown_id) if shown_id is not None else None

    return ParsedEvent(
        event_id=event_id,
        format_code=FORMAT_LABELS.get(label),
        format_label=label,
        name=name,
        event_date=event_date,
        players=players,
        source=source,
        stars=stars,
        is_major=is_major,
        is_online=is_online,
        is_league=bool(source == "mtgo.com" and "league" in name.lower()),
        decks=decks,
        shown_deck=shown,
    )


def _parse_deck_rows(tree: HTMLParser) -> list[DeckRow]:
    rows: list[DeckRow] = []
    seen: set[int] = set()
    for row in tree.css("div.hover_tr, div.chosen_tr"):
        if "deck_line" in (row.attributes.get("class") or ""):
            continue
        rank_div = next(
            (d for d in row.css("div") if "width:42px" in (d.attributes.get("style") or "")), None
        )
        link = next((a for a in row.css("a") if "&d=" in (a.attributes.get("href") or "") and _text(a)), None)
        player = row.css_first("a.player")
        if rank_div is None or link is None:
            continue
        deck_id = query_int(link.attributes["href"], "d")
        if deck_id is None or deck_id in seen:
            continue
        seen.add(deck_id)
        rank_raw = _text(rank_div)
        rmin, rmax = parse_rank(rank_raw)
        rows.append(DeckRow(deck_id, rank_raw, rmin, rmax, _text(link), _text(player)))
    return rows


def parse_deck_detail(html: str, deck_id: int) -> DeckDetail:
    """Detail (archetype + list) of the deck shown on the page."""
    tree = HTMLParser(html)
    arch_id = arch_name = None
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if href.startswith("archetype?a="):
            arch_id = query_int(href, "a")
            arch_name = re.sub(r"\s+decks$", "", _text(a)) or None
            break

    cards: list[CardLine] = []
    section: Section | None = None
    for node in tree.css("div.O14, div.deck_line"):
        cls = node.attributes.get("class") or ""
        if "deck_line" in cls:
            if section is None:
                continue
            name_node = node.css_first("span.L14")
            m = re.match(r"\s*(\d+)", node.text(deep=False) or "")
            if name_node is None or m is None:
                continue
            cards.append(CardLine(section, _text(name_node), int(m.group(1))))
            continue
        header = _text(node)
        if header == "COMMANDER":
            section = "commander"
        elif header == "SIDEBOARD":
            section = "side"
        elif _MAIN_HEADER.match(header):
            section = "main"
    return DeckDetail(deck_id, arch_id, arch_name, tuple(cards))
