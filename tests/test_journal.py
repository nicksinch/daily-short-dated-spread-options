import json
from datetime import date, datetime, timedelta, timezone

from features import Feature, FeatureSet
from journal import DRY_RUN, SUBMIT, append, build_record
from orders import OrderRecord, OrderState
from strategy import Decision, SpreadLeg, SpreadProposal, Stance

NOW = datetime(2026, 9, 4, 13, 40, 2, tzinfo=timezone.utc)
EXPIRY = date(2026, 9, 5)

SHORT = SpreadLeg("SPY260905P00761000", 761.0, "P", "sell", -0.3020, 1.60, 1.68)
LONG = SpreadLeg("SPY260905P00756000", 756.0, "P", "buy", -0.1810, 0.88, 0.95)


def a_feature_set():
    return FeatureSet(
        spot=Feature.ok(765.12, NOW),
        rv20=Feature.ok(0.1483, NOW),
        spot_over_sma20=Feature.ok(1.0121, NOW),
        spot_over_sma50=Feature.ok(1.0388, NOW),
        atm_iv=Feature.missing("no implied volatility on the 765.0 call"),
        market_open=True,
        next_open=NOW + timedelta(days=1),
        next_close=NOW + timedelta(hours=4),
        expiry=EXPIRY,
        as_of=NOW,
    )


def a_proposal():
    return SpreadProposal(
        structure="put_credit", short_leg=SHORT, long_leg=LONG, credit=0.65,
        max_loss_per_contract=435.0, quantity=2, total_risk=870.0,
        risk_budget=1000.0,
    )


def a_trade():
    return Decision(Stance.BULLISH, a_proposal(), "sell 761.0P / buy 756.0P")


def a_stand_aside():
    return Decision(Stance.NEUTRAL, None, "neutral stance: no directional edge")


def make(decision, mode=SUBMIT, payload=None, record=None):
    return build_record(
        now=NOW, mode=mode, underlying="SPY", expiry=EXPIRY,
        features=a_feature_set(), decision=decision,
        payload=payload, record=record,
    )


def test_the_record_is_json_native():
    # No custom encoder: build_record converts datetimes itself, so the
    # shape can be tested without touching a file.
    assert json.loads(json.dumps(make(a_trade()))) == make(a_trade())


def test_a_stand_aside_records_its_reason_and_no_order():
    record = make(a_stand_aside())
    assert record["decision"]["will_trade"] is False
    assert "neutral stance" in record["decision"]["reason"]
    assert record["order"] is None
    assert record["stance"] == "neutral"


def test_a_dry_run_records_no_order():
    assert make(a_trade(), mode=DRY_RUN)["order"] is None


def test_every_feature_is_recorded_with_its_status():
    features = make(a_trade())["features"]
    assert set(features) == {
        "spot", "rv20", "spot_over_sma20", "spot_over_sma50", "atm_iv"
    }
    assert features["spot"]["value"] == 765.12
    assert features["spot"]["status"] == "ok"
    assert features["atm_iv"]["status"] == "missing"
    assert features["atm_iv"]["value"] is None
    assert "no implied volatility" in features["atm_iv"]["detail"]


def test_a_trade_records_the_sizing_and_both_legs():
    decision = make(a_trade())["decision"]
    assert decision["will_trade"] is True
    assert decision["structure"] == "put_credit"
    assert decision["credit"] == 0.65
    assert decision["quantity"] == 2
    assert decision["total_risk"] == 870.0
    assert [leg["symbol"] for leg in decision["legs"]] == [
        "SPY260905P00761000", "SPY260905P00756000"
    ]


def test_a_submitted_order_records_the_payload_and_the_outcome():
    payload = {"order_class": "mleg", "limit_price": "-0.65"}
    record = OrderRecord(
        id="b889185b", status="filled", state=OrderState.FILLED,
        filled_qty=2.0, filled_avg_price=-0.65, submitted_at=NOW,
    )
    order = make(a_trade(), payload=payload, record=record)["order"]
    assert order["payload"] == payload
    assert order["id"] == "b889185b"
    assert order["state"] == "filled"
    assert order["filled_qty"] == 2.0


def test_the_timestamp_and_expiry_are_iso_strings():
    record = make(a_trade())
    assert record["timestamp"] == NOW.isoformat()
    assert record["expiry"] == "2026-09-05"
    assert record["mode"] == "submit"


def test_append_writes_one_line_per_run(tmp_path):
    path = str(tmp_path / "decisions.jsonl")
    append(make(a_trade()), path)
    append(make(a_stand_aside()), path)
    lines = (tmp_path / "decisions.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["decision"]["will_trade"] is True
    assert json.loads(lines[1])["decision"]["will_trade"] is False


def test_a_write_failure_warns_and_preserves_the_record(tmp_path, capsys):
    # The one place an I/O error must not raise: by the time this runs an
    # order may already be live, and dying on a full disk would leave a
    # position with no record anywhere. stderr keeps the record.
    unwritable = str(tmp_path / "no-such-directory" / "decisions.jsonl")
    append(make(a_trade()), unwritable)
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "SPY260905P00761000" in err
