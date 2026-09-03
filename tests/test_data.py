from datetime import date, datetime, timezone

import pytest

from config import FeatureConfig
from data import AlpacaClient


class StubSession:
    """Stands in for requests.Session. Returns a canned payload per URL suffix."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        for suffix, payload in self.routes.items():
            if url.endswith(suffix):
                return StubResponse(payload)
        raise AssertionError(f"unexpected URL {url}")


class StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def make_client(routes):
    return AlpacaClient("key", "secret", FeatureConfig(), session=StubSession(routes))


def test_get_clock_parses_offsets():
    client = make_client({
        "/v2/clock": {
            "is_open": True,
            "next_close": "2026-09-03T16:00:00-04:00",
            "next_open": "2026-09-04T09:30:00-04:00",
            "timestamp": "2026-09-03T11:27:12.614966328-04:00",
        }
    })
    clock = client.get_clock()
    assert clock.is_open is True
    assert clock.next_close.hour == 16


def test_daily_bars_drop_the_forming_bar_for_today():
    # The whole point: a bar dated today is still forming, and its "close"
    # is merely the last trade so far.
    client = make_client({
        "/v2/stocks/SPY/bars": {
            "bars": [
                {"t": "2026-09-01T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 761.78, "v": 10},
                {"t": "2026-09-02T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 765.16, "v": 10},
                {"t": "2026-09-03T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 772.40, "v": 3},
            ],
            "next_page_token": None,
            "symbol": "SPY",
        }
    })
    bars = client.get_daily_bars("SPY", date(2026, 9, 3))
    assert [b.date for b in bars] == [date(2026, 9, 1), date(2026, 9, 2)]
    assert bars[-1].close == 765.16


def test_daily_bars_parse_nanosecond_timestamps():
    client = make_client({
        "/v2/stocks/SPY/bars": {
            "bars": [{"t": "2026-09-02T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 765.16, "v": 10}],
            "symbol": "SPY",
        }
    })
    assert client.get_daily_bars("SPY", date(2026, 9, 3))[0].date == date(2026, 9, 2)


def test_empty_bars_return_empty_list_not_an_error():
    client = make_client({"/v2/stocks/SPY/bars": {"bars": None, "symbol": "SPY"}})
    assert client.get_daily_bars("SPY", date(2026, 9, 3)) == []


def test_latest_quote_parses_both_sides():
    client = make_client({
        "/v2/stocks/SPY/quotes/latest": {
            "quote": {"ap": 772.76, "as": 280, "bp": 772.73, "bs": 40,
                      "t": "2026-09-03T15:36:04.545901081Z"},
            "symbol": "SPY",
        }
    })
    quote = client.get_latest_quote("SPY")
    assert quote.bid == 772.73
    assert quote.ask == 772.76
    assert quote.timestamp == datetime(2026, 9, 3, 15, 36, 4, 545901, tzinfo=timezone.utc)


def test_latest_quote_returns_none_when_absent():
    client = make_client({"/v2/stocks/SPY/quotes/latest": {"symbol": "SPY"}})
    assert client.get_latest_quote("SPY") is None


def test_latest_trade_parses():
    client = make_client({
        "/v2/stocks/SPY/trades/latest": {
            "trade": {"p": 772.4, "s": 40, "t": "2026-09-03T15:27:07.910075029Z"},
            "symbol": "SPY",
        }
    })
    trade = client.get_latest_trade("SPY")
    assert trade.price == 772.4
    assert trade.size == 40


def test_credentials_go_in_headers_and_never_in_params():
    session = StubSession({"/v2/clock": {"is_open": False, "timestamp": "2026-09-03T07:00:00-04:00",
                                         "next_open": "2026-09-03T09:30:00-04:00",
                                         "next_close": "2026-09-03T16:00:00-04:00"}})
    client = AlpacaClient("key-id", "secret-key", FeatureConfig(), session=session)
    client.get_clock()
    assert session.headers["APCA-API-KEY-ID"] == "key-id"
    _, params = session.calls[0]
    assert "secret-key" not in str(params)
