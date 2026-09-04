import pathlib
from datetime import date, datetime, timedelta, timezone

import pytest

import main as main_module
from config import FeatureConfig
from data import MarketClock, StockQuote, StockTrade
from features import Feature, FeatureSet
from main import format_feature_set

NOW = datetime(2026, 9, 3, 15, 36, tzinfo=timezone.utc)


def a_feature_set():
    return FeatureSet(
        spot=Feature.ok(766.46, NOW, "one-sided quote, used last trade"),
        rv20=Feature.ok(0.0738, NOW),
        spot_over_sma20=Feature.ok(0.9967, NOW, "sma20=768.98 over closes to 2026-09-02"),
        spot_over_sma50=Feature.ok(1.0147, NOW),
        atm_iv=Feature.missing("no implied volatility on the 772.0 call"),
        market_open=True,
        next_open=NOW + timedelta(days=1),
        next_close=NOW + timedelta(hours=4),
        expiry=date(2026, 9, 4),
        as_of=NOW,
    )


def test_output_shows_every_feature_with_its_status():
    text = format_feature_set(a_feature_set(), "SPY")
    for name in ("spot", "rv20", "spot/sma20", "spot/sma50", "atm_iv"):
        assert name in text
    assert "766.46" in text
    assert "ok" in text


def test_missing_feature_shows_its_reason_and_no_value():
    text = format_feature_set(a_feature_set(), "SPY")
    line = next(l for l in text.splitlines() if l.startswith("atm_iv"))
    assert "missing" in line
    assert "no implied volatility" in line


def test_market_context_is_reported_separately_from_features():
    text = format_feature_set(a_feature_set(), "SPY")
    assert "market_open=True" in text
    assert "expiry" in text


def test_no_module_contains_order_placing_code():
    # Scope guard: this session builds data and signal only.
    # Tokens are specific to order placement. "submit" is deliberately not
    # among them: it collides with the module docstring's "submits nothing".
    root = pathlib.Path(__file__).parent.parent
    for module in ("config.py", "data.py", "features.py", "main.py"):
        source = (root / module).read_text()
        for forbidden in ("/v2/orders", "order_class", "mleg", "position_intent"):
            assert forbidden not in source, f"{module} mentions {forbidden}"


class StubClient:
    """Records what main() asks for. Performs no I/O."""

    def __init__(self, quote, trade):
        self._quote = quote
        self._trade = trade
        self.chain_calls = []

    def get_clock(self):
        return MarketClock(
            is_open=False, timestamp=NOW,
            next_open=NOW + timedelta(days=1), next_close=NOW + timedelta(days=1, hours=6),
        )

    def get_daily_bars(self, symbol, today):
        return []

    def get_latest_quote(self, symbol):
        return self._quote

    def get_latest_trade(self, symbol):
        return self._trade

    def resolve_expiry(self, symbol, today):
        return date(2026, 9, 4)

    def get_option_chain(self, symbol, expiry, strike_lo, strike_hi):
        self.chain_calls.append((symbol, expiry, strike_lo, strike_hi))
        return []


def run_main_with(monkeypatch, quote, trade):
    client = StubClient(quote, trade)
    monkeypatch.setattr(main_module.AlpacaClient, "from_env", classmethod(lambda cls, cfg: client))
    assert main_module.main(["--dry-run"]) == 0
    return client


def test_chain_band_is_centred_on_the_resolved_spot(monkeypatch):
    client = run_main_with(
        monkeypatch,
        quote=StockQuote(bid=766.0, ask=767.0, bid_size=1, ask_size=1, timestamp=NOW),
        trade=None,
    )
    band = FeatureConfig().strike_band_dollars
    assert client.chain_calls == [("SPY", date(2026, 9, 4), 766 - band, 766 + band)]


@pytest.mark.parametrize(
    "quote",
    [None, StockQuote(bid=766.0, ask=0.0, bid_size=1, ask_size=0, timestamp=NOW)],
    ids=["no quote", "one-sided quote"],
)
def test_no_chain_is_requested_without_a_usable_spot(monkeypatch, quote):
    # Neither a two-sided quote nor a trade leaves spot missing. Requesting a
    # band around a fabricated reference price would ask Alpaca for strikes
    # near zero; asking for nothing is the honest response.
    client = run_main_with(monkeypatch, quote=quote, trade=None)
    assert client.chain_calls == []


def test_chain_band_falls_back_to_the_last_trade(monkeypatch):
    client = run_main_with(
        monkeypatch,
        quote=None,
        trade=StockTrade(price=772.4, size=10, timestamp=NOW),
    )
    band = FeatureConfig().strike_band_dollars
    assert client.chain_calls == [("SPY", date(2026, 9, 4), 772 - band, 772 + band)]
