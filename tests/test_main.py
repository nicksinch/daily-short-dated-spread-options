import pathlib
from datetime import date, datetime, timedelta, timezone

import pytest

import main as main_module
from config import FeatureConfig
from data import Account, MarketClock, StockQuote, StockTrade
from features import Feature, FeatureSet
from main import format_decision, format_feature_set
from strategy import Decision, SpreadLeg, SpreadProposal, Stance

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
    for module in ("config.py", "data.py", "features.py", "main.py", "strategy.py"):
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

    def get_account(self):
        return Account(equity=100_000.0)


def run_main_with(monkeypatch, quote, trade, stance="neutral"):
    client = StubClient(quote, trade)
    monkeypatch.setattr(main_module.AlpacaClient, "from_env", classmethod(lambda cls, cfg: client))
    assert main_module.main(["--dry-run", "--stance", stance]) == 0
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


def a_proposal():
    return SpreadProposal(
        structure="put_credit",
        short_leg=SpreadLeg("SPY260905P00761000", 761.0, "P", "sell", -0.3020, 1.60, 1.68),
        long_leg=SpreadLeg("SPY260905P00756000", 756.0, "P", "buy", -0.1810, 0.88, 0.95),
        credit=0.65, max_loss_per_contract=435.0, quantity=2,
        total_risk=870.0, risk_budget=1000.0,
    )


def test_a_trade_prints_both_legs_and_the_sizing():
    text = format_decision(Decision(Stance.BULLISH, a_proposal(), "sell 761.0P / buy 756.0P"))
    assert "put_credit" in text
    assert "SPY260905P00761000" in text and "SPY260905P00756000" in text
    assert "sell" in text and "buy" in text
    assert "0.65" in text and "435.00" in text and "870.00" in text
    assert "quantity 2" in text


def test_standing_aside_prints_the_reason_in_place_of_legs():
    text = format_decision(
        Decision(Stance.NEUTRAL, None, "neutral stance: no directional edge")
    )
    assert "stand aside" in text
    assert "neutral stance" in text
    assert "SPY26" not in text


def test_the_stance_flag_is_required(monkeypatch):
    monkeypatch.setattr(
        main_module.AlpacaClient, "from_env",
        classmethod(lambda cls, cfg: StubClient(None, None)),
    )
    with pytest.raises(SystemExit):
        main_module.main(["--dry-run"])


def test_an_unknown_stance_is_rejected(monkeypatch):
    monkeypatch.setattr(
        main_module.AlpacaClient, "from_env",
        classmethod(lambda cls, cfg: StubClient(None, None)),
    )
    with pytest.raises(SystemExit):
        main_module.main(["--dry-run", "--stance", "sideways"])


def test_the_dry_run_reaches_the_decision(monkeypatch, capsys):
    run_main_with(
        monkeypatch,
        quote=StockQuote(bid=766.0, ask=767.0, bid_size=1, ask_size=1, timestamp=NOW),
        trade=None,
        stance="bullish",
    )
    assert "decision:" in capsys.readouterr().out
