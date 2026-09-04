import pytest

from broker import Broker
from config import OrderConfig
from orders import OptionPosition


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


def test_open_orders_asks_for_nested_legs():
    # Without nested=true an mleg order's legs are not returned at all.
    broker, session = make_broker({"/v2/orders": []})
    broker.open_orders()
    _, params = session.gets[0]
    assert params == {"status": "open", "nested": "true"}


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
