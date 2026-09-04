"""The only module in this repository that can place an order.

Everything that reaches an order endpoint lives here, so "can this program
trade when I did not mean it to?" is a question you answer by reading one
short file. `data.py` stays what its docstring says: market data in,
nothing out.

Mirrors AlpacaClient's discipline -- an injectable session, raise_for_status
on every call, transport errors raise -- and adds one poll loop. It never
cancels and never replaces: this layer opens positions only.
"""

import os
import time
from datetime import datetime

import requests

from config import OrderConfig
from orders import OptionPosition, OrderRecord, OrderState, classify

# Transport mechanics, not strategy tunables, matching data.py's convention.
_HTTP_TIMEOUT_SECONDS = 10
_US_OPTION = "us_option"


class Broker:
    TRADING_URL = "https://paper-api.alpaca.markets"

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        cfg: OrderConfig,
        session: object | None = None,
    ):
        self._cfg = cfg
        self._session = session if session is not None else requests.Session()
        self._session.headers.update(
            {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        )

    @classmethod
    def from_env(cls, cfg: OrderConfig) -> "Broker":
        try:
            key_id = os.environ["ALPACA_API_KEY_ID"]
            secret_key = os.environ["ALPACA_API_SECRET_KEY"]
        except KeyError as exc:
            raise RuntimeError(
                "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in the environment."
            ) from exc
        return cls(key_id, secret_key, cfg)

    def _get(self, path: str, params: dict | None = None):
        response = self._session.get(
            f"{self.TRADING_URL}{path}", params=params, timeout=_HTTP_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.json()

    def open_option_positions(self) -> list[OptionPosition]:
        """Open option positions only.

        The paper account may hold unrelated equity, as it did during the
        smoke test, so the asset class is filtered rather than assumed.
        """
        payload = self._get("/v2/positions")
        return [
            OptionPosition(symbol=p["symbol"], qty=float(p["qty"]))
            for p in (payload or [])
            if p.get("asset_class") == _US_OPTION
        ]

    def open_orders(self) -> list[list[str]]:
        """Leg symbols for every open order, one list per order.

        `nested=true` is required: an mleg parent order's own `symbol` is the
        empty string and its legs are not returned without the flag, so a
        guard reading the parent symbol would silently never match.
        """
        payload = self._get("/v2/orders", {"status": "open", "nested": "true"})
        orders = []
        for order in payload or []:
            legs = order.get("legs") or []
            symbols = [leg["symbol"] for leg in legs]
            if not symbols and order.get("symbol"):
                symbols = [order["symbol"]]
            orders.append(symbols)
        return orders

    def _post(self, path: str, payload: dict) -> dict:
        response = self._session.post(
            f"{self.TRADING_URL}{path}", json=payload, timeout=_HTTP_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.json()

    def submit(self, payload: dict) -> OrderRecord:
        """Place the order. The one call in this repository that trades."""
        return _record(self._post("/v2/orders", payload))

    def get_order(self, order_id: str) -> OrderRecord:
        return _record(self._get(f"/v2/orders/{order_id}"))

    def await_fill(self, order_id: str) -> OrderRecord:
        """Poll until the order is no longer working, or until the timeout.

        Read-only: it never cancels and never replaces, which is what keeps
        this layer to opening positions. A timeout is a recorded outcome,
        not an exception -- the caller journals whatever was last seen.
        """
        deadline = time.monotonic() + self._cfg.fill_timeout_seconds
        record = self.get_order(order_id)
        while record.state is OrderState.WORKING and time.monotonic() < deadline:
            time.sleep(self._cfg.fill_poll_seconds)
            record = self.get_order(order_id)
        return record


def _record(payload: dict) -> OrderRecord:
    """Alpaca's order JSON as the three things this program acts on.

    The status string and fill price are preserved verbatim rather than
    normalised; the sign Alpaca reports on a credit fill is not something
    this layer depends on.
    """
    status = payload["status"]
    price = payload.get("filled_avg_price")
    submitted = payload.get("submitted_at")
    return OrderRecord(
        id=payload["id"],
        status=status,
        state=classify(status),
        filled_qty=float(payload.get("filled_qty") or 0),
        filled_avg_price=None if price is None else float(price),
        submitted_at=datetime.fromisoformat(submitted) if submitted else None,
    )
