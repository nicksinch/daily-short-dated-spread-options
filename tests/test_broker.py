import dataclasses

import pytest

import broker as broker_module
from broker import Broker
from config import OrderConfig
from orders import OptionPosition, OrderRecord, OrderState, classify


class StubSession:
    """Stands in for requests.Session. Returns a canned payload per URL suffix."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.gets = []
        self.posts = []

    def get(self, url, params=None, timeout=None):
        self.gets.append((url, params))
        return StubResponse(self._route(url))

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return StubResponse(self._route(url))

    def _route(self, url):
        for suffix, payload in self.routes.items():
            if url.endswith(suffix):
                return payload
        raise AssertionError(f"unexpected URL {url}")


class StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def make_broker(routes, cfg=None):
    session = StubSession(routes)
    broker = Broker("key", "secret", cfg or OrderConfig(), session=session)
    return broker, session


def test_option_positions_drop_the_accounts_equity_holdings():
    broker, _ = make_broker({
        "/v2/positions": [
            {"symbol": "SPY", "qty": "1", "asset_class": "us_equity"},
            {"symbol": "SPY260905P00761000", "qty": "-2", "asset_class": "us_option"},
        ]
    })
    assert broker.open_option_positions() == [
        OptionPosition(symbol="SPY260905P00761000", qty=-2.0)
    ]


def test_no_positions_is_an_empty_list():
    broker, _ = make_broker({"/v2/positions": []})
    assert broker.open_option_positions() == []


def test_a_position_missing_asset_class_is_still_returned():
    # The guard must fail closed: if Alpaca ever omits or renames
    # asset_class, dropping the position silently would empty out
    # existing_exposure and let a second spread go on the same expiry.
    broker, _ = make_broker({
        "/v2/positions": [{"symbol": "SPY260905P00761000", "qty": "-2"}]
    })
    assert broker.open_option_positions() == [
        OptionPosition(symbol="SPY260905P00761000", qty=-2.0)
    ]


def test_open_orders_asks_for_nested_legs_and_an_explicit_page_limit():
    # Without nested=true an mleg order's legs are not returned at all.
    # Without an explicit limit, Alpaca defaults to 50 -- on an account
    # with more open orders than that, the guard could miss today's
    # working spread and resubmit.
    broker, session = make_broker({"/v2/orders": []})
    broker.open_orders()
    _, params = session.gets[0]
    assert params == {
        "status": "open", "nested": "true",
        "limit": broker_module._MAX_OPEN_ORDERS_PER_PAGE,
    }


def test_open_orders_reads_leg_symbols_not_the_empty_parent_symbol():
    broker, _ = make_broker({
        "/v2/orders": [{
            "id": "abc", "symbol": "", "order_class": "mleg",
            "legs": [
                {"symbol": "SPY260905P00761000"},
                {"symbol": "SPY260905P00756000"},
            ],
        }]
    })
    assert broker.open_orders() == [
        ["SPY260905P00761000", "SPY260905P00756000"]
    ]


def test_a_single_leg_order_falls_back_to_its_own_symbol():
    broker, _ = make_broker({
        "/v2/orders": [{"id": "abc", "symbol": "SPY260905P00761000", "legs": None}]
    })
    assert broker.open_orders() == [["SPY260905P00761000"]]


FILLED_ORDER = {
    "id": "b889185b", "status": "filled", "filled_qty": "2",
    "filled_avg_price": "-0.65",
    "submitted_at": "2026-09-04T14:06:14.723228947Z",
}
WORKING_ORDER = {
    "id": "b889185b", "status": "pending_new", "filled_qty": "0",
    "filled_avg_price": None, "submitted_at": "2026-09-04T14:06:14.723228947Z",
}


class SequencedSession(StubSession):
    """Returns each queued payload once, then repeats the last one."""

    def __init__(self, routes, sequence):
        super().__init__(routes)
        self.sequence = list(sequence)

    def _route(self, url):
        if url.rstrip("/").endswith("/v2/orders/b889185b"):
            return self.sequence.pop(0) if len(self.sequence) > 1 else self.sequence[0]
        return super()._route(url)


def a_payload():
    return {
        "order_class": "mleg", "qty": "2", "type": "limit",
        "limit_price": "-0.65", "time_in_force": "day",
        "legs": [
            {"symbol": "SPY260905P00761000", "ratio_qty": "1",
             "side": "sell", "position_intent": "sell_to_open"},
            {"symbol": "SPY260905P00756000", "ratio_qty": "1",
             "side": "buy", "position_intent": "buy_to_open"},
        ],
    }


def test_submit_posts_the_payload_unmodified():
    broker, session = make_broker({"/v2/orders": WORKING_ORDER})
    payload = a_payload()
    broker.submit(payload)
    url, sent = session.posts[0]
    assert url.endswith("/v2/orders")
    assert sent == payload


def test_submit_returns_the_parsed_record():
    broker, _ = make_broker({"/v2/orders": FILLED_ORDER})
    record = broker.submit(a_payload())
    assert record.id == "b889185b"
    assert record.status == "filled"
    assert record.state is OrderState.FILLED
    assert record.filled_qty == 2.0
    assert record.filled_avg_price == -0.65
    assert record.submitted_at.year == 2026


def test_an_absent_fill_price_stays_none():
    broker, _ = make_broker({"/v2/orders": WORKING_ORDER})
    record = broker.submit(a_payload())
    assert record.filled_avg_price is None
    assert record.filled_qty == 0.0


def test_await_fill_stops_once_the_order_is_terminal():
    session = SequencedSession({}, [WORKING_ORDER, FILLED_ORDER])
    cfg = dataclasses.replace(
        OrderConfig(), fill_poll_seconds=0.0, fill_timeout_seconds=5.0
    )
    broker = Broker("key", "secret", cfg, session=session)
    record = broker.await_fill("b889185b")
    assert record.state is OrderState.FILLED
    assert len(session.gets) == 2


def test_await_fill_returns_its_last_observation_on_timeout():
    # A timeout is a recorded outcome, not an exception: the caller
    # journals whatever was last seen and exits non-zero.
    session = SequencedSession({}, [WORKING_ORDER])
    cfg = dataclasses.replace(
        OrderConfig(), fill_poll_seconds=0.0, fill_timeout_seconds=0.0
    )
    broker = Broker("key", "secret", cfg, session=session)
    record = broker.await_fill("b889185b")
    assert record.state is OrderState.WORKING
    assert record.status == "pending_new"


def test_await_fill_never_cancels_or_replaces():
    session = SequencedSession({}, [FILLED_ORDER])
    cfg = dataclasses.replace(OrderConfig(), fill_poll_seconds=0.0)
    broker = Broker("key", "secret", cfg, session=session)
    broker.await_fill("b889185b")
    assert session.posts == []
