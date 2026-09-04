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

import requests

from config import OrderConfig
from orders import OptionPosition

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
