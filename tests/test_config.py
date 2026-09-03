from datetime import timedelta

from config import FeatureConfig


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


def test_config_is_frozen():
    import dataclasses
    import pytest

    cfg = FeatureConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.rv_window = 5
