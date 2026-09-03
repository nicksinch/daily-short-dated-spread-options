import math
import pytest
from datetime import date, datetime, timedelta, timezone

from config import FeatureConfig
from data import StockQuote, StockTrade
from features import annualized_vol, log_returns, simple_moving_average, Feature, Status, bar_freshness, quote_freshness, spot_feature

R = math.log(1.01)


def test_log_returns_length_is_one_less_than_closes():
    assert len(log_returns([100.0] * 21)) == 20


def test_log_returns_of_flat_series_are_zero():
    assert log_returns([100.0] * 21) == [0.0] * 20


def test_flat_series_has_zero_volatility():
    assert annualized_vol(log_returns([100.0] * 21), 252) == 0.0


def test_constant_growth_has_zero_volatility():
    # Every return is identical (ln 1.01), so the dispersion is zero even
    # though the returns themselves are not. Catches an implementation that
    # measures deviation from zero instead of from the mean.
    closes = [100 * 1.01**i for i in range(21)]
    assert annualized_vol(log_returns(closes), 252) == pytest.approx(0.0, abs=1e-12)


def test_alternating_returns_match_hand_computation():
    # 21 closes alternating 100, 100e^r -> 10 returns of +r and 10 of -r.
    # Mean is 0, sample stdev is r*sqrt(20/19), annualised by sqrt(252).
    closes = [100.0 if i % 2 == 0 else 100 * math.exp(R) for i in range(21)]
    expected = R * math.sqrt(20 / 19) * math.sqrt(252)
    assert annualized_vol(log_returns(closes), 252) == pytest.approx(expected)
    assert annualized_vol(log_returns(closes), 252) == pytest.approx(0.16206006, abs=1e-8)


def test_annualized_vol_uses_sample_stdev_not_population():
    # Sample (n-1) runs 2.60% above population (n) at n=20. Pinned so the
    # convention cannot drift silently.
    closes = [100.0 if i % 2 == 0 else 100 * math.exp(R) for i in range(21)]
    rets = log_returns(closes)
    population = math.sqrt(sum(x * x for x in rets) / len(rets)) * math.sqrt(252)
    assert annualized_vol(rets, 252) / population == pytest.approx(1.0260, abs=1e-4)


def test_sma20_of_1_through_20():
    assert simple_moving_average(list(range(1, 21)), 20) == 10.5


def test_sma50_of_1_through_50():
    assert simple_moving_average(list(range(1, 51)), 50) == 25.5


def test_sma_uses_only_the_newest_window():
    # 50 closes but a 20-wide window -> mean of 31..50.
    assert simple_moving_average(list(range(1, 51)), 20) == 40.5


NOW = datetime(2026, 9, 3, 15, 30, tzinfo=timezone.utc)


def test_ok_feature_is_usable():
    f = Feature.ok(1.5, NOW)
    assert f.status is Status.OK
    assert f.value == 1.5
    assert f.usable


def test_missing_feature_has_no_value_and_is_not_usable():
    f = Feature.missing("no quote")
    assert f.status is Status.MISSING
    assert f.value is None
    assert f.timestamp is None
    assert f.detail == "no quote"
    assert not f.usable


def test_stale_feature_keeps_its_value_but_is_not_usable():
    # The caller must be able to see the number and decide for itself.
    f = Feature.stale(1.5, NOW, "300s old")
    assert f.status is Status.STALE
    assert f.value == 1.5
    assert not f.usable


def test_fresh_quote_is_ok_while_market_open():
    cfg = FeatureConfig()
    status, detail = quote_freshness(NOW - timedelta(seconds=3), NOW, cfg, market_open=True)
    assert status is Status.OK
    assert detail is None


def test_old_quote_is_stale_while_market_open():
    cfg = FeatureConfig()
    status, detail = quote_freshness(NOW - timedelta(minutes=5), NOW, cfg, market_open=True)
    assert status is Status.STALE
    assert "300s old" in detail


def test_old_quote_is_ok_while_market_closed():
    # Last session's data is the freshest that exists. "Market is closed" is
    # a trading decision, not a data-quality verdict.
    cfg = FeatureConfig()
    status, detail = quote_freshness(NOW - timedelta(hours=11), NOW, cfg, market_open=False)
    assert status is Status.OK
    assert detail is None


def test_yesterdays_bar_is_fresh():
    # The critical case: during a session the newest settled bar is always
    # ~18h old, and must not be marked stale.
    cfg = FeatureConfig()
    status, _ = bar_freshness(date(2026, 9, 2), date(2026, 9, 3), cfg)
    assert status is Status.OK


def test_bar_older_than_the_limit_is_stale():
    cfg = FeatureConfig()
    status, detail = bar_freshness(date(2026, 8, 20), date(2026, 9, 3), cfg)
    assert status is Status.STALE
    assert "14 days old" in detail


def quote(bid, ask, ts=NOW):
    return StockQuote(bid=bid, ask=ask, bid_size=10, ask_size=10, timestamp=ts)


def trade(price, ts=NOW):
    return StockTrade(price=price, size=10, timestamp=ts)


def test_spot_is_the_mid_of_a_two_sided_quote():
    f = spot_feature(quote(772.73, 772.76), trade(772.40), FeatureConfig(), NOW, True)
    assert f.value == pytest.approx(772.745)
    assert f.status is Status.OK


def test_one_sided_quote_falls_back_to_the_last_trade():
    # Real case: bid 764.34 / ask 0 would give a mid of 382.17.
    f = spot_feature(quote(764.34, 0.0), trade(766.46), FeatureConfig(), NOW, True)
    assert f.value == 766.46
    assert f.status is Status.OK
    assert "one-sided" in f.detail


def test_missing_quote_falls_back_to_the_last_trade():
    f = spot_feature(None, trade(766.46), FeatureConfig(), NOW, True)
    assert f.value == 766.46


def test_no_quote_and_no_trade_is_missing():
    f = spot_feature(None, None, FeatureConfig(), NOW, True)
    assert f.status is Status.MISSING
    assert f.value is None


def test_stale_spot_keeps_its_value():
    old = NOW - timedelta(minutes=5)
    f = spot_feature(quote(772.73, 772.76, old), None, FeatureConfig(), NOW, True)
    assert f.status is Status.STALE
    assert f.value == pytest.approx(772.745)


def test_spot_is_not_stale_when_the_market_is_closed():
    old = NOW - timedelta(hours=11)
    f = spot_feature(quote(772.73, 772.76, old), None, FeatureConfig(), NOW, False)
    assert f.status is Status.OK


from data import DailyBar
from features import realized_vol_feature, sma_ratio_feature

TODAY = date(2026, 9, 3)


def bars_from(closes, end=date(2026, 9, 2)):
    """Daily bars ending on `end`, one calendar day apart."""
    return [
        DailyBar(
            date=end - timedelta(days=len(closes) - 1 - i),
            open=c, high=c, low=c, close=c, volume=1,
        )
        for i, c in enumerate(closes)
    ]


def test_rv20_of_a_flat_series_is_zero():
    f = realized_vol_feature(bars_from([100.0] * 21), FeatureConfig(), TODAY)
    assert f.value == 0.0
    assert f.status is Status.OK


def test_rv20_timestamp_is_the_newest_bar_used():
    f = realized_vol_feature(bars_from([100.0] * 21), FeatureConfig(), TODAY)
    assert f.timestamp.date() == date(2026, 9, 2)


def test_rv20_needs_21_closes_for_20_returns():
    # The off-by-one: 20 returns require 21 prices.
    f = realized_vol_feature(bars_from([100.0] * 20), FeatureConfig(), TODAY)
    assert f.status is Status.MISSING
    assert "21" in f.detail


def test_rv20_uses_only_the_newest_window():
    closes = [1.0] * 30 + [100.0 if i % 2 == 0 else 100 * math.exp(R) for i in range(21)]
    f = realized_vol_feature(bars_from(closes), FeatureConfig(), TODAY)
    expected = R * math.sqrt(20 / 19) * math.sqrt(252)
    assert f.value == pytest.approx(expected)


def test_rv20_is_stale_when_bars_are_old():
    f = realized_vol_feature(
        bars_from([100.0] * 21, end=date(2026, 8, 20)), FeatureConfig(), TODAY
    )
    assert f.status is Status.STALE
    assert f.value == 0.0


def test_sma_ratio_divides_spot_by_the_average():
    bars = bars_from([float(c) for c in range(1, 21)])
    f = sma_ratio_feature(bars, Feature.ok(21.0, NOW), 20, FeatureConfig(), TODAY)
    assert f.value == pytest.approx(2.0)


def test_sma_ratio_carries_the_spot_timestamp():
    # The ratio moves with spot, and spot is the input that can go stale.
    bars = bars_from([float(c) for c in range(1, 21)])
    f = sma_ratio_feature(bars, Feature.ok(21.0, NOW), 20, FeatureConfig(), TODAY)
    assert f.timestamp == NOW
    assert "2026-09-02" in f.detail


def test_sma_ratio_is_missing_when_spot_is_missing():
    bars = bars_from([float(c) for c in range(1, 21)])
    f = sma_ratio_feature(bars, Feature.missing("no quote"), 20, FeatureConfig(), TODAY)
    assert f.status is Status.MISSING
    assert "spot" in f.detail


def test_sma_ratio_is_missing_with_too_few_bars():
    bars = bars_from([float(c) for c in range(1, 11)])
    f = sma_ratio_feature(bars, Feature.ok(21.0, NOW), 20, FeatureConfig(), TODAY)
    assert f.status is Status.MISSING
