import math
import pytest

from features import annualized_vol, log_returns, simple_moving_average

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
