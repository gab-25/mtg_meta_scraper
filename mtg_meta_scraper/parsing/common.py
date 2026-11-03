"""Utility di parsing condivise."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import parse_qs, urlparse

FORMAT_LABELS: dict[str, str] = {
    "Standard": "ST",
    "Pioneer": "PI",
    "Modern": "MO",
    "Legacy": "LE",
    "Vintage": "VI",
    "Pauper": "PAU",
    "Duel Commander": "EDH",
    "cEDH": "cEDH",
}


def parse_rank(raw: str) -> tuple[int, int]:
    """'1' -> (1, 1); '3-4' -> (3, 4)."""
    parts = [int(p) for p in re.findall(r"\d+", raw)]
    if not parts:
        raise ValueError(f"rank non valido: {raw!r}")
    return parts[0], parts[-1]


def parse_site_date(raw: str) -> date:
    """'DD/MM/YY' -> date(20YY, MM, DD)."""
    m = re.fullmatch(r"\s*(\d{2})/(\d{2})/(\d{2})\s*", raw)
    if not m:
        raise ValueError(f"data non valida: {raw!r}")
    d, mo, y = (int(x) for x in m.groups())
    return date(2000 + y, mo, d)


def query_int(href: str, key: str) -> int | None:
    """Valore intero di un parametro di query in un href relativo."""
    vals = parse_qs(urlparse(href).query).get(key)
    if not vals:
        return None
    try:
        return int(vals[0])
    except ValueError:
        return None
