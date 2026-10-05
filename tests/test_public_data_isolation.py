"""Public Binance data can feed research and paper signals, never orders
(docs/plans/candle-chart-and-coverage.md Phase C completion criteria)."""

import ast
import inspect

from app.exchanges import binance_public
from app.exchanges.binance_public import BinancePublicClient
from app.trading.application import order_flow


def test_the_public_client_has_no_credentials_and_no_order_surface() -> None:
    parameters = set(inspect.signature(BinancePublicClient.__init__).parameters) - {"self"}
    assert parameters == {"timeout_seconds", "base_url"}
    methods = {
        name
        for name, member in inspect.getmembers(BinancePublicClient, inspect.isfunction)
        if not name.startswith("_")
    }
    assert methods == {"get_candles", "get_instrument_rules"}
    tree = ast.parse(inspect.getsource(binance_public))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)}
    assert not {"post", "put", "delete", "patch"} & names
    assert not [n for n in names if any(w in n.lower() for w in ("secret", "api_key", "sign"))]


def test_order_flow_never_imports_an_exchange_client() -> None:
    """Fills are simulated; no module on the order path may reach an exchange."""
    tree = ast.parse(inspect.getsource(order_flow))
    modules = [
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert not [m for m in modules if m.startswith("app.exchanges")]
