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
    # DailyBar.date is a `date`, which can't carry microseconds, so this only
    # pins that a genuine 9-digit fractional second doesn't choke the parse
    # or shift the date. Exact microsecond truncation (9 digits -> 6) is
    # pinned precisely in test_latest_quote_parses_both_sides, whose
    # `timestamp` field is a full datetime.
    client = make_client({
        "/v2/stocks/SPY/bars": {
            "bars": [{"t": "2026-09-02T04:00:00.123456789Z", "o": 1, "h": 2, "l": 0.5,
                      "c": 765.16, "v": 10}],
            "symbol": "SPY",
        }
    })
    bar = client.get_daily_bars("SPY", date(2026, 9, 3))[0]
    assert bar.date == date(2026, 9, 2)


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


def test_resolve_expiry_takes_the_earliest_after_today():
    # Selecting the earliest date is order-independent, so it stays correct
    # whatever order the endpoint returns rows in. (Live 2026-09-03 the order
    # is expiration_date then strike; min() does not depend on that holding.)
    client = make_client({
        "/v2/options/contracts": {
            "option_contracts": [
                {"expiration_date": "2026-09-10"},
                {"expiration_date": "2026-09-04"},
                {"expiration_date": "2026-09-08"},
            ],
            "next_page_token": None,
        }
    })
    assert client.resolve_expiry("SPY", date(2026, 9, 3)) == date(2026, 9, 4)


def test_resolve_expiry_raises_when_no_contracts_exist():
    client = make_client({"/v2/options/contracts": {"option_contracts": []}})
    with pytest.raises(RuntimeError, match="no option contracts"):
        client.resolve_expiry("SPY", date(2026, 9, 3))


def test_resolve_expiry_names_truncation_when_offset_exceeds_the_first_page():
    # A single expiry's strike list already exceeds one page, so the
    # requested offset can fall outside the expiries visible on the first
    # page even though more exist beyond it. The error must name truncation
    # as the cause rather than the misleading "only N available" wording,
    # which is only accurate when there is no further page.
    session = StubSession({
        "/v2/options/contracts": {
            "option_contracts": [{"expiration_date": "2026-09-04"}],
            "next_page_token": "MTAw",
        }
    })
    client = AlpacaClient(
        "key", "secret", FeatureConfig(expiry_offset_sessions=2), session=session
    )
    with pytest.raises(RuntimeError, match="truncated"):
        client.resolve_expiry("SPY", date(2026, 9, 3))


def test_option_chain_parses_strike_and_right_from_the_occ_symbol():
    client = make_client({
        "/v1beta1/options/snapshots/SPY": {
            "snapshots": {
                "SPY260904C00772000": {
                    "impliedVolatility": 0.1534,
                    "greeks": {"delta": 0.5341},
                    "latestQuote": {"bp": 2.69, "ap": 2.78,
                                    "t": "2026-09-03T15:36:04.545901081Z"},
                },
                "SPY260904P00772000": {
                    "impliedVolatility": 0.2123,
                    "greeks": {"delta": -0.4597},
                    "latestQuote": {"bp": 2.10, "ap": 2.15,
                                    "t": "2026-09-03T15:36:04.545901081Z"},
                },
            },
            "next_page_token": "",
        }
    })
    chain = client.get_option_chain("SPY", date(2026, 9, 4), 771, 773)
    by_right = {q.right: q for q in chain}
    assert by_right["C"].strike == 772.0
    assert by_right["C"].iv == 0.1534
    assert by_right["P"].delta == -0.4597


def test_option_chain_treats_absent_greeks_as_none_not_zero():
    # At 0DTE the raw API omits both keys entirely. Reading them as zero
    # would fabricate data that the API never returned.
    client = make_client({
        "/v1beta1/options/snapshots/SPY": {
            "snapshots": {
                "SPY260903C00772000": {
                    "latestQuote": {"bp": 1.25, "ap": 1.30,
                                    "t": "2026-09-03T15:36:04.545901081Z"},
                },
            },
            "next_page_token": "",
        }
    })
    quote = client.get_option_chain("SPY", date(2026, 9, 3), 771, 773)[0]
    assert quote.iv is None
    assert quote.delta is None
    assert quote.bid == 1.25


def test_option_chain_raises_rather_than_silently_truncating():
    client = make_client({
        "/v1beta1/options/snapshots/SPY": {
            "snapshots": {
                "SPY260904C00772000": {
                    "latestQuote": {"bp": 2.69, "ap": 2.78,
                                    "t": "2026-09-03T15:36:04.545901081Z"},
                },
            },
            "next_page_token": "U1BZMjYwOTAzUDAwNzg4MDAw",
        }
    })
    with pytest.raises(RuntimeError, match="truncated"):
        client.get_option_chain("SPY", date(2026, 9, 4), 700, 900)


def test_option_chain_sends_an_explicit_limit():
    session = StubSession({
        "/v1beta1/options/snapshots/SPY": {"snapshots": {}, "next_page_token": ""}
    })
    client = AlpacaClient("k", "s", FeatureConfig(), session=session)
    client.get_option_chain("SPY", date(2026, 9, 4), 764, 780)
    _, params = session.calls[0]
    assert params["limit"] == 1000
    assert params["feed"] == "indicative"


def test_get_account_parses_equity_from_a_json_string():
    # Alpaca returns the account's numeric fields as JSON strings.
    client = make_client({"/v2/account": {"equity": "100000.42", "buying_power": "200000"}})
    assert client.get_account().equity == pytest.approx(100000.42)


def test_get_account_hits_the_trading_api():
    client = make_client({"/v2/account": {"equity": "100000"}})
    client.get_account()
    url, _ = client._session.calls[0]
    assert url == "https://paper-api.alpaca.markets/v2/account"
