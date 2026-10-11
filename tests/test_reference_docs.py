import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_reference_docs", ROOT / "scripts" / "generate_reference_docs.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_reference_is_up_to_date() -> None:
    generator = _load_generator()
    current = generator.API_DOC.read_text(encoding="utf-8")
    assert current == generator.api_reference(), (
        "docs/design/api/reference.md is out of date: "
        "run `python scripts/generate_reference_docs.py`"
    )


def test_database_reference_is_up_to_date() -> None:
    generator = _load_generator()
    current = generator.DB_DOC.read_text(encoding="utf-8")
    assert current == generator.database_reference(), (
        "docs/design/database/schema-reference.md is out of date: "
        "run `python scripts/generate_reference_docs.py`"
    )
