import dataclasses
from datetime import timedelta

import pytest

from config import FeatureConfig, StrategyConfig, OrderConfig


def test_defaults_match_the_design():
    cfg = FeatureConfig()
    assert cfg.rv_window == 20
    assert cfg.sma_short_window == 20
    assert cfg.sma_long_window == 50
    assert cfg.trading_days_per_year == 252
    assert cfg.staleness_threshold == timedelta(seconds=60)
    assert cfg.max_bar_age_days == 5
    assert cfg.underlying == "SPY"
    assert cfg.expiry_offset_sessions == 1
    assert cfg.option_feed == "indicative"
    assert cfg.stock_feed == "iex"
    assert cfg.bar_feed == "sip"
    assert cfg.strike_band_dollars == 20
    assert cfg.bar_lookback_days == 120


def test_config_is_frozen():
    cfg = FeatureConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.rv_window = 5


def test_feature_config_no_longer_carries_the_delta_target():
    # It moved to StrategyConfig, which is the layer that uses it.
    assert not hasattr(FeatureConfig(), "delta_target")


def test_strategy_defaults_match_the_design():
    cfg = StrategyConfig()
    assert cfg.delta_target == 0.30
    assert cfg.spread_width_dollars == 5.0
    assert cfg.risk_fraction == 0.01
    assert cfg.min_credit_fraction == 0.10
    assert cfg.contract_multiplier == 100


def test_strategy_config_is_frozen():
    cfg = StrategyConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.delta_target = 0.5


def test_order_defaults_match_the_design():
    cfg = OrderConfig()
    assert cfg.time_in_force == "day"
    assert cfg.fill_poll_seconds == 1.0
    assert cfg.fill_timeout_seconds == 15.0
    assert cfg.journal_path == "decisions.jsonl"


def test_order_config_is_frozen():
    cfg = OrderConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.time_in_force = "gtc"
