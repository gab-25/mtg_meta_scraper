"""Client HTTP "educato": rate limit, retry con backoff, decodifica cp1252."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

USER_AGENT = "mtg-meta-scraper/0.1 (+personal metagame analysis)"
BACKOFFS = (2, 4, 8)

log = logging.getLogger("mtg_meta_scraper")


class PoliteClient:
    def __init__(
        self,
        base_url: str,
        interval: float,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            follow_redirects=True,
            transport=transport,
        )
        self._interval = interval
        self._sleep = sleep
        self._clock = clock
        self._last_start: float | None = None

    def _wait_turn(self) -> None:
        if self._last_start is not None:
            remaining = self._interval - (self._clock() - self._last_start)
            if remaining > 0:
                self._sleep(remaining)
        self._last_start = self._clock()

    def get_text(self, path: str) -> str | None:
        """Scarica `path`; None su 404. Solleva dopo i retry esauriti."""
        for attempt in range(len(BACKOFFS) + 1):
            self._wait_turn()
            try:
                resp = self._client.get(path)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.content.decode("cp1252", errors="replace")
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                    exc.response.status_code == 429 or exc.response.status_code >= 500
                )
                if not retryable or attempt == len(BACKOFFS):
                    raise
                delay = max(BACKOFFS[attempt], _retry_after(exc))
                log.warning("GET %s fallita (%s), nuovo tentativo tra %ss", path, exc, delay)
                self._sleep(delay)
        raise AssertionError("unreachable")

    def close(self) -> None:
        self._client.close()


def _retry_after(exc: Exception) -> float:
    """Secondi indicati da Retry-After su una risposta 429/503 (0 se assente o non numerico)."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return min(float(exc.response.headers.get("Retry-After", 0)), 600)
        except ValueError:
            return 0
    return 0
