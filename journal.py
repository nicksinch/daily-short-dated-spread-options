"""One line per run, appended.

`build_record` is pure and returns only JSON-native values -- datetimes are
converted here rather than by a custom json encoder -- so the shape is
testable without touching a file. Every run writes a line, stand-asides
included: strategy.py was built so a quiet day is as explainable as a busy
one, and a log of trades only would discard half of that.
"""

import json
import sys
from datetime import date, datetime

from features import Feature, FeatureSet
from orders import OrderRecord
from strategy import Decision

DRY_RUN = "dry_run"
SUBMIT = "submit"

FEATURE_NAMES = ("spot", "rv20", "spot_over_sma20", "spot_over_sma50", "atm_iv")


def _feature(feature: Feature) -> dict:
    return {
        "value": feature.value,
        "status": feature.status.value,
        "timestamp": None if feature.timestamp is None else feature.timestamp.isoformat(),
        "detail": feature.detail,
    }


def _decision(decision: Decision) -> dict:
    body = {"will_trade": decision.will_trade, "reason": decision.reason}
    proposal = decision.proposal
    if proposal is None:
        return body
    body.update(
        {
            "structure": proposal.structure,
            "credit": proposal.credit,
            "quantity": proposal.quantity,
            "max_loss_per_contract": proposal.max_loss_per_contract,
            "total_risk": proposal.total_risk,
            "risk_budget": proposal.risk_budget,
            "legs": [
                {
                    "symbol": leg.symbol,
                    "side": leg.side,
                    "strike": leg.strike,
                    "right": leg.right,
                }
                for leg in (proposal.short_leg, proposal.long_leg)
            ],
        }
    )
    return body


def _order(payload: dict | None, record: OrderRecord | None) -> dict | None:
    """The submitted order, or None when nothing was sent.

    A dry run and a stand-aside both record None: nothing reached an order
    endpoint, and there is no outcome to describe.
    """
    if payload is None or record is None:
        return None
    return {
        "payload": payload,
        "id": record.id,
        "status": record.status,
        "state": record.state.value,
        "filled_qty": record.filled_qty,
        "filled_avg_price": record.filled_avg_price,
    }


def build_record(
    now: datetime,
    mode: str,
    underlying: str,
    expiry: date,
    features: FeatureSet,
    decision: Decision,
    payload: dict | None = None,
    record: OrderRecord | None = None,
) -> dict:
    """One run, as JSON-native values."""
    return {
        "timestamp": now.isoformat(),
        "mode": mode,
        "underlying": underlying,
        "expiry": expiry.isoformat(),
        "stance": decision.stance.value,
        "features": {name: _feature(getattr(features, name)) for name in FEATURE_NAMES},
        "decision": _decision(decision),
        "order": _order(payload, record),
    }


def append(record: dict, path: str) -> None:
    """Append one line. Never raises.

    The single deliberate exception to this codebase's raise-on-failure
    discipline. By the time this is called an order may already be live, so
    dying on a full disk would leave a position with no record anywhere.
    The warning carries the complete record, which makes stderr the fallback
    journal.
    """
    try:
        with open(path, "a") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"WARNING: could not write the journal to {path}: {exc}", file=sys.stderr)
        print(json.dumps(record), file=sys.stderr)
