import pathlib
from datetime import date, datetime, timedelta, timezone

import pytest

import main as main_module
from config import FeatureConfig
from data import Account, DailyBar, MarketClock, OptionQuote, StockQuote, StockTrade
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


def test_only_broker_reaches_an_order_endpoint():
    # This replaces the old blanket wall. Now that the order layer exists
    # the property worth guarding is narrower: exactly one module can place
    # an order, and the mleg vocabulary lives in the pure module that builds
    # the payload.
    root = pathlib.Path(__file__).parent.parent
    modules = (
        "config.py", "data.py", "features.py", "strategy.py",
        "orders.py", "broker.py", "journal.py", "main.py",
    )
    sources = {name: (root / name).read_text() for name in modules}

    assert "/v2/orders" in sources["broker.py"]
    for name, source in sources.items():
        if name != "broker.py":
            assert "/v2/orders" not in source, f"{name} reaches an order endpoint"
        if name != "orders.py":
            for forbidden in ("order_class", "mleg", "position_intent"):
                assert forbidden not in source, f"{name} mentions {forbidden}"


def test_exactly_one_mode_flag_is_required(monkeypatch):
    with pytest.raises(SystemExit):
        main_module.main(["--stance", "bullish"])


def test_the_two_mode_flags_are_mutually_exclusive(monkeypatch):
    with pytest.raises(SystemExit):
        main_module.main(["--dry-run", "--submit", "--stance", "bullish"])


def test_the_submit_flag_is_accepted(monkeypatch):
    # The one gate test that distinguishes the new parser from the old:
    # before the mutually exclusive group existed, --submit was an
    # unrecognised argument and this raised SystemExit.
    client = StubClient(quote=None, trade=None)
    monkeypatch.setattr(
        main_module.AlpacaClient, "from_env", classmethod(lambda cls, cfg: client)
    )
    assert main_module.main(["--submit", "--stance", "neutral"]) == 0


class StubClient:
    """Records what main() asks for. Performs no I/O.

    `bars` and `chain` default to empty, matching the previous fixed
    behaviour, so the tests that only inspect `chain_calls` are unaffected.
    """

    def __init__(self, quote, trade, bars=None, chain=None):
        self._quote = quote
        self._trade = trade
        self._bars = [] if bars is None else bars
        self._chain = [] if chain is None else chain
        self.chain_calls = []

    def get_clock(self):
        return MarketClock(
            is_open=False, timestamp=NOW,
            next_open=NOW + timedelta(days=1), next_close=NOW + timedelta(days=1, hours=6),
        )

    def get_daily_bars(self, symbol, today):
        return self._bars

    def get_latest_quote(self, symbol):
        return self._quote

    def get_latest_trade(self, symbol):
        return self._trade

    def resolve_expiry(self, symbol, today):
        return date(2026, 9, 4)

    def get_option_chain(self, symbol, expiry, strike_lo, strike_hi):
        self.chain_calls.append((symbol, expiry, strike_lo, strike_hi))
        return self._chain

    def get_account(self):
        return Account(equity=100_000.0)


def run_main_with(monkeypatch, quote, trade, stance="neutral", bars=None, chain=None):
    client = StubClient(quote, trade, bars=bars, chain=chain)
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


def daily_bars(count, newest):
    """`count` ascending settled bars, the newest dated `newest`.

    Only the newest date and the count matter to the gates that consume
    these (bar_freshness and the rv20/sma window lengths); the prices just
    need to vary enough to produce a real (non-zero) realised volatility.
    """
    return [
        DailyBar(
            date=newest - timedelta(days=count - 1 - i),
            open=750.0, high=751.0, low=749.0, close=750.0 + i * 0.1, volume=1_000_000,
        )
        for i in range(count)
    ]


def a_full_chain():
    """Enough of a chain to price atm_iv and fill a bullish put credit
    spread -- the worked example from the design spec, spot near 765.25."""
    return [
        OptionQuote(
            "SPY260905C00765000", 765.0, "C",
            bid=1.50, ask=1.58, iv=0.1650, delta=0.50, timestamp=NOW,
        ),
        OptionQuote(
            "SPY260905P00765000", 765.0, "P",
            bid=1.45, ask=1.53, iv=0.1620, delta=-0.50, timestamp=NOW,
        ),
        OptionQuote(
            "SPY260905P00761000", 761.0, "P",
            bid=1.60, ask=1.68, iv=0.1700, delta=-0.3020, timestamp=NOW,
        ),
        OptionQuote(
            "SPY260905P00756000", 756.0, "P",
            bid=0.88, ask=0.95, iv=0.1500, delta=-0.1810, timestamp=NOW,
        ),
    ]


def a_chain_with_a_one_sided_wing():
    """Same trade as `a_full_chain`, but the far wing -- the contract most
    likely to come back thin on the free indicative feed -- is quoted
    one-sided and without a delta, and the short leg has no ask. None of
    those three sides is demanded by the credit, so the trade should still
    go through."""
    return [
        OptionQuote(
            "SPY260905C00765000", 765.0, "C",
            bid=1.50, ask=1.58, iv=0.1650, delta=0.50, timestamp=NOW,
        ),
        OptionQuote(
            "SPY260905P00765000", 765.0, "P",
            bid=1.45, ask=1.53, iv=0.1620, delta=-0.50, timestamp=NOW,
        ),
        OptionQuote(
            "SPY260905P00761000", 761.0, "P",
            bid=1.60, ask=None, iv=0.1700, delta=-0.3020, timestamp=NOW,
        ),
        OptionQuote(
            "SPY260905P00756000", 756.0, "P",
            bid=None, ask=0.95, iv=0.1500, delta=None, timestamp=NOW,
        ),
    ]


def run_main_to_a_trade(monkeypatch, chain):
    today = datetime.now(main_module.EASTERN).date()
    bars = daily_bars(FeatureConfig().sma_long_window + 1, today - timedelta(days=1))
    return run_main_with(
        monkeypatch,
        quote=StockQuote(bid=765.00, ask=765.50, bid_size=1, ask_size=1, timestamp=NOW),
        trade=None,
        stance="bullish",
        bars=bars,
        chain=chain,
    )


def test_main_prints_a_real_trade_end_to_end(monkeypatch, capsys):
    # The three existing chain-band tests only ever reach the stand-aside
    # branch (StubClient's default chain is empty); this drives the whole
    # pipeline through a genuine proposal.
    run_main_to_a_trade(monkeypatch, a_full_chain())
    text = capsys.readouterr().out
    assert "decision: trade put_credit" in text
    assert "SPY260905P00761000" in text and "SPY260905P00756000" in text
    assert "credit 0.65" in text
    assert "quantity 2" in text


def test_a_one_sided_wing_still_prints_a_decision(monkeypatch, capsys):
    # Regression for the finding that format_decision raised TypeError on a
    # VALID proposal: short.ask, long.bid and long.delta are never demanded
    # by the credit calculation and so are the fields most likely to be
    # absent on the far-OTM wing on the free indicative feed.
    run_main_to_a_trade(monkeypatch, a_chain_with_a_one_sided_wing())
    text = capsys.readouterr().out
    assert "decision: trade put_credit" in text
    short_line = next(l for l in text.splitlines() if "SPY260905P00761000" in l)
    long_line = next(l for l in text.splitlines() if "SPY260905P00756000" in l)
    assert "ask -" in short_line and "bid 1.60" in short_line
    assert "delta -" in long_line and "bid -" in long_line and "ask 0.95" in long_line
