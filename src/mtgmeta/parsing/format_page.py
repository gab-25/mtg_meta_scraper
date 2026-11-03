"""Parser della pagina formato (`format?f=<code>[&meta=<id>]`) e della home."""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from mtgmeta.models import MetaShare
from mtgmeta.parsing.common import query_int

_MACRO = re.compile(r"^(AGGRO|CONTROL|COMBO)\b")
_PCT = re.compile(r"^([\d.]+)\s*%$")


def parse_metagame(html: str) -> list[MetaShare]:
    """Metagame breakdown: archetipi in ordine di pagina con quota e macro-categoria."""
    tree = HTMLParser(html)
    shares: list[MetaShare] = []
    macro: str | None = None
    for node in tree.css("div.meta_arch, div.hover_tr"):
        cls = node.attributes.get("class") or ""
        if "meta_arch" in cls:
            m = _MACRO.match(node.text(strip=True))
            if m:
                macro = m.group(1).capitalize()
            continue
        link = next(
            (a for a in node.css("a") if (a.attributes.get("href") or "").startswith("archetype?a=")), None
        )
        if link is None or "meta=" not in link.attributes["href"]:
            continue
        pct = next(
            (float(m.group(1)) for d in node.css("div.S14") if (m := _PCT.match(d.text(strip=True)))), None
        )
        arch_id = query_int(link.attributes["href"], "a")
        if pct is None or arch_id is None:
            continue
        shares.append(MetaShare(arch_id, link.text(strip=True), macro, pct))
    return shares


def parse_meta_periods(html: str) -> dict[str, int]:
    """Selettore dei periodi: testo -> id `meta`."""
    tree = HTMLParser(html)
    periods: dict[str, int] = {}
    for a in tree.css("#metas_list a"):
        meta_id = query_int(a.attributes.get("href") or "", "meta")
        label = a.text(strip=True)
        if meta_id is not None and label:
            periods.setdefault(label, meta_id)
    return periods


def parse_latest_event_id(html: str) -> int:
    ids = [int(x) for x in re.findall(r"event\?e=(\d+)", html)]
    if not ids:
        raise ValueError("nessun link evento trovato")
    return max(ids)
