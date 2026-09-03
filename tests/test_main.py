from datetime import date, datetime, timedelta, timezone

from config import FeatureConfig
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
    text = format_feature_set(a_feature_set())
    for name in ("spot", "rv20", "spot/sma20", "spot/sma50", "atm_iv"):
        assert name in text
    assert "766.46" in text
    assert "ok" in text


def test_missing_feature_shows_its_reason_and_no_value():
    text = format_feature_set(a_feature_set())
    line = next(l for l in text.splitlines() if l.startswith("atm_iv"))
    assert "missing" in line
    assert "no implied volatility" in line


def test_market_context_is_reported_separately_from_features():
    text = format_feature_set(a_feature_set())
    assert "market_open=True" in text
    assert "expiry" in text


def test_main_module_contains_no_order_placing_code():
    # Scope guard: this session builds data and signal only.
    # Tokens are specific to order placement. "submit" is deliberately not
    # among them: it collides with the module docstring's "submits nothing".
    source = (__import__("pathlib").Path(__file__).parent.parent / "main.py").read_text()
    for forbidden in ("/v2/orders", "order_class", "mleg", "position_intent"):
        assert forbidden not in source
