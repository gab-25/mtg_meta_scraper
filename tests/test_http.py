import httpx
import pytest

from mtg_meta_scraper.config import Settings
from mtg_meta_scraper.http import USER_AGENT, PoliteClient


class FakeTime:
    def __init__(self):
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def make_client(handler, ft: FakeTime, interval: float = 1.5) -> PoliteClient:
    return PoliteClient(
        "https://example.test/",
        interval,
        transport=httpx.MockTransport(handler),
        sleep=ft.sleep,
        clock=ft.clock,
    )


def test_rate_limit_waits_between_requests():
    ft = FakeTime()
    c = make_client(lambda r: httpx.Response(200, content=b"ok"), ft)
    c.get_text("a")
    c.get_text("b")
    assert ft.sleeps == [pytest.approx(1.5)]


def test_retries_on_503_then_succeeds():
    ft = FakeTime()
    codes = iter([503, 503, 200])
    c = make_client(lambda r: httpx.Response(next(codes), content=b"ok"), ft, interval=0)
    assert c.get_text("a") == "ok"
    assert [s for s in ft.sleeps if s > 0] == [2, 4]


def test_gives_up_after_three_retries():
    ft = FakeTime()
    c = make_client(lambda r: httpx.Response(503), ft, interval=0)
    with pytest.raises(httpx.HTTPStatusError):
        c.get_text("a")


def test_returns_none_on_404():
    ft = FakeTime()
    c = make_client(lambda r: httpx.Response(404), ft)
    assert c.get_text("a") is None


def test_decodes_cp1252():
    ft = FakeTime()
    c = make_client(lambda r: httpx.Response(200, content=b"Lab\xe8ge \x93x\x94"), ft)
    assert c.get_text("a") == "Labège “x”"


def test_sends_user_agent():
    ft = FakeTime()
    seen = {}

    def handler(r: httpx.Request):
        seen["ua"] = r.headers["user-agent"]
        return httpx.Response(200, content=b"")

    make_client(handler, ft).get_text("a")
    assert seen["ua"] == "mtg-meta-scraper/0.1 (+personal metagame analysis)" == USER_AGENT


def test_settings_from_env_parses_formats(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("MTGMETA_FORMATS", "MO,cEDH")
    s = Settings.from_env()
    assert s.formats == ("MO", "cEDH")
    assert s.request_interval == 1.5
    assert s.backfill_days == 183


def test_honours_retry_after_on_429():
    ft = FakeTime()
    codes = iter([429, 200])
    c = make_client(
        lambda r: httpx.Response(next(codes), headers={"Retry-After": "30"}, content=b"ok"), ft, interval=0
    )
    assert c.get_text("a") == "ok"
    assert 30 in ft.sleeps
