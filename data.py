"""Alpaca REST access.

Returns plain records and `None`. This module deliberately does not import
`Status` or `Feature` -- classifying data quality is the feature layer's job,
which keeps the seam between fetching and calculating thin enough to test
either side without the other.

Network and HTTP problems raise. Absent market data returns `None` or an
empty list for the feature layer to classify.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from config import FeatureConfig


@dataclass(frozen=True)
class MarketClock:
    is_open: bool
    timestamp: datetime
    next_open: datetime
    next_close: datetime


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class StockQuote:
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    timestamp: datetime


@dataclass(frozen=True)
class StockTrade:
    price: float
    size: float
    timestamp: datetime


def _timestamp(raw: str) -> datetime:
    """Parse Alpaca's RFC-3339 timestamps.

    Alpaca emits nanosecond precision; fromisoformat truncates to
    microseconds, which is far finer than anything here needs.
    """
    return datetime.fromisoformat(raw)


class AlpacaClient:
    DATA_URL = "https://data.alpaca.markets"
    TRADING_URL = "https://paper-api.alpaca.markets"

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        cfg: FeatureConfig,
        session: object | None = None,
    ):
        self._cfg = cfg
        self._session = session if session is not None else requests.Session()
        self._session.headers.update(
            {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        )

    @classmethod
    def from_env(cls, cfg: FeatureConfig) -> "AlpacaClient":
        try:
            key_id = os.environ["ALPACA_API_KEY_ID"]
            secret_key = os.environ["ALPACA_API_SECRET_KEY"]
        except KeyError as exc:
            raise RuntimeError(
                "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in the environment."
            ) from exc
        return cls(key_id, secret_key, cfg)

    def _get(self, base: str, path: str, params: dict | None = None) -> dict:
        response = self._session.get(f"{base}{path}", params=params, timeout=10)
        response.raise_for_status()
        return response.json()

    def get_clock(self) -> MarketClock:
        payload = self._get(self.TRADING_URL, "/v2/clock")
        return MarketClock(
            is_open=payload["is_open"],
            timestamp=_timestamp(payload["timestamp"]),
            next_open=_timestamp(payload["next_open"]),
            next_close=_timestamp(payload["next_close"]),
        )

    def get_daily_bars(self, symbol: str, today: date) -> list[DailyBar]:
        """Settled daily bars only.

        Any bar dated today is still forming -- its close is just the last
        trade so far -- so it is dropped. Including it would make every
        feature drift through the session.
        """
        start = today - timedelta(days=self._cfg.bar_lookback_days)
        payload = self._get(
            self.DATA_URL,
            f"/v2/stocks/{symbol}/bars",
            {
                "timeframe": "1Day",
                "start": start.isoformat(),
                "adjustment": "split",
                "feed": self._cfg.bar_feed,
                "limit": 10000,
            },
        )
        bars = [
            DailyBar(
                date=_timestamp(b["t"]).date(),
                open=b["o"],
                high=b["h"],
                low=b["l"],
                close=b["c"],
                volume=b["v"],
            )
            for b in (payload.get("bars") or [])
        ]
        return [b for b in bars if b.date < today]

    def get_latest_quote(self, symbol: str) -> StockQuote | None:
        payload = self._get(
            self.DATA_URL,
            f"/v2/stocks/{symbol}/quotes/latest",
            {"feed": self._cfg.stock_feed},
        )
        quote = payload.get("quote")
        if not quote:
            return None
        return StockQuote(
            bid=quote["bp"],
            ask=quote["ap"],
            bid_size=quote["bs"],
            ask_size=quote["as"],
            timestamp=_timestamp(quote["t"]),
        )

    def get_latest_trade(self, symbol: str) -> StockTrade | None:
        payload = self._get(
            self.DATA_URL,
            f"/v2/stocks/{symbol}/trades/latest",
            {"feed": self._cfg.stock_feed},
        )
        trade = payload.get("trade")
        if not trade:
            return None
        return StockTrade(
            price=trade["p"], size=trade["s"], timestamp=_timestamp(trade["t"])
        )
