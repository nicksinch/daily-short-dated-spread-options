"""Alpaca REST access.

Returns plain records and `None`. This module deliberately does not import
`Status` or `Feature` -- classifying data quality is the feature layer's job,
which keeps the seam between fetching and calculating thin enough to test
either side without the other.

Network and HTTP problems raise. Absent market data returns `None` or an
empty list for the feature layer to classify. Two conditions also raise
`RuntimeError` rather than returning a guess: `resolve_expiry` when no
contracts exist or the configured offset cannot be resolved from the first
page, and `get_option_chain` when the response is truncated.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from config import FeatureConfig

# Transport mechanics, not strategy tunables -- kept out of FeatureConfig so
# that config stays about feature calculation, not HTTP plumbing.
_HTTP_TIMEOUT_SECONDS = 10
_MAX_BARS_PER_PAGE = 10000  # Alpaca's per-page maximum for stock bars
_MAX_OPTION_CONTRACTS_PER_PAGE = 100  # plenty for one underlying's expiries
_MAX_OPTION_CHAIN_PER_PAGE = 1000  # counts contracts, not strikes -- see get_option_chain


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


@dataclass(frozen=True)
class Account:
    equity: float


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    strike: float
    right: str  # "C" or "P"
    bid: float | None
    ask: float | None
    iv: float | None
    delta: float | None
    timestamp: datetime | None


def _timestamp(raw: str) -> datetime:
    """Parse Alpaca's RFC-3339 timestamps.

    Alpaca emits nanosecond precision; fromisoformat truncates to
    microseconds, which is far finer than anything here needs.
    """
    return datetime.fromisoformat(raw)


def _parse_occ(symbol: str) -> tuple[float, str]:
    """Split an OCC symbol into strike and right.

    SPY260904C00772000 -> (772.0, "C"): the last eight digits are the strike
    in thousandths, and the character before them is the right.
    """
    return int(symbol[-8:]) / 1000, symbol[-9]


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
        response = self._session.get(
            f"{base}{path}", params=params, timeout=_HTTP_TIMEOUT_SECONDS
        )
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

    def get_account(self) -> Account:
        """Current account equity.

        No `None` path: the account always exists, so an HTTP failure raises
        like every other transport problem here. Alpaca returns this
        endpoint's numeric fields as JSON strings.
        """
        payload = self._get(self.TRADING_URL, "/v2/account")
        return Account(equity=float(payload["equity"]))

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
                "limit": _MAX_BARS_PER_PAGE,
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

    def resolve_expiry(self, symbol: str, today: date) -> date:
        """The expiry `expiry_offset_sessions` sessions ahead.

        Asks Alpaca which contracts exist rather than assuming tomorrow is a
        trading day -- SPY expires every trading day, but holidays leave gaps
        (2026-09-07 is Labor Day).
        """
        # A single expiry's strike list already exceeds one page, so this
        # response is routinely truncated -- unlike get_option_chain, no
        # blanket "raise on next_page_token" guard is possible here.
        # Picking the earliest expiry off a possibly-truncated page still
        # works because the live endpoint orders rows by
        # (expiration_date ASC, strike ASC): the nearest expiry's rows fill
        # the first page before any later expiry appears. That ordering,
        # not row position, is what selection actually depends on -- if the
        # API ever stopped ordering by expiration date first, this would
        # break.
        payload = self._get(
            self.TRADING_URL,
            "/v2/options/contracts",
            {
                "underlying_symbols": symbol,
                "expiration_date_gte": (today + timedelta(days=1)).isoformat(),
                "type": "call",
                "limit": _MAX_OPTION_CONTRACTS_PER_PAGE,
            },
        )
        expiries = sorted(
            {c["expiration_date"] for c in (payload.get("option_contracts") or [])}
        )
        if not expiries:
            raise RuntimeError(f"no option contracts for {symbol} after {today}")
        index = self._cfg.expiry_offset_sessions - 1
        if index >= len(expiries):
            if payload.get("next_page_token"):
                raise RuntimeError(
                    f"expiry list for {symbol} after {today} was truncated at "
                    f"the page limit ({len(expiries)} expiries visible); "
                    f"offset {self._cfg.expiry_offset_sessions} could not be "
                    f"resolved from the first page"
                )
            raise RuntimeError(
                f"only {len(expiries)} expiries available after {today}, "
                f"need offset {self._cfg.expiry_offset_sessions}"
            )
        return date.fromisoformat(expiries[index])

    def get_option_chain(
        self, symbol: str, expiry: date, strike_lo: float, strike_hi: float
    ) -> list[OptionQuote]:
        """Snapshots for one expiry across a strike band.

        `limit` counts contracts, not strikes, and defaults to 100 -- a wide
        band truncates mid-chain. An explicit limit is sent and a returned
        page token is treated as an error rather than computed upon.
        """
        payload = self._get(
            self.DATA_URL,
            f"/v1beta1/options/snapshots/{symbol}",
            {
                "expiration_date": expiry.isoformat(),
                "strike_price_gte": strike_lo,
                "strike_price_lte": strike_hi,
                "feed": self._cfg.option_feed,
                "limit": _MAX_OPTION_CHAIN_PER_PAGE,
            },
        )
        if payload.get("next_page_token"):
            raise RuntimeError(
                f"option chain for {symbol} {expiry} was truncated; "
                "narrow the strike band"
            )
        chain = []
        for occ, snapshot in (payload.get("snapshots") or {}).items():
            strike, right = _parse_occ(occ)
            quote = snapshot.get("latestQuote") or {}
            greeks = snapshot.get("greeks") or {}
            chain.append(
                OptionQuote(
                    symbol=occ,
                    strike=strike,
                    right=right,
                    bid=quote.get("bp"),
                    ask=quote.get("ap"),
                    iv=snapshot.get("impliedVolatility"),
                    delta=greeks.get("delta"),
                    timestamp=_timestamp(quote["t"]) if quote.get("t") else None,
                )
            )
        return chain
