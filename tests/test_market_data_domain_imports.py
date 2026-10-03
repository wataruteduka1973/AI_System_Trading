"""`app/market_data/domain/` holds calculations only (docs/architecture/current-and-target.md:
"Domain owns business rules and values without FastAPI, SQLAlchemy, SDK, or filesystem
imports"). Checked statically so a database or SDK dependency cannot slip in unnoticed."""

import ast
from pathlib import Path

import pytest

DOMAIN = Path(__file__).resolve().parents[1] / "app" / "market_data" / "domain"
FORBIDDEN_PREFIXES = (
    "sqlalchemy",
    "fastapi",
    "binance",
    "oandapyV20",
    "httpx",
    "app.models",
    "app.security",
    "app.db",
    "app.api",
    "app.market_data.application",
    "app.market_data.infrastructure",
    "app.market_data.worker",
)


def _imported_modules(path: Path) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


@pytest.mark.parametrize("path", sorted(DOMAIN.glob("*.py")), ids=lambda path: path.name)
def test_domain_modules_import_no_persistence_framework_or_sdk(path: Path) -> None:
    forbidden = [
        module
        for module in _imported_modules(path)
        if any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)
    ]
    assert forbidden == []


def test_the_domain_package_is_not_empty() -> None:
    # Guards the parametrized test above against silently checking nothing.
    assert len(list(DOMAIN.glob("*.py"))) >= 3
