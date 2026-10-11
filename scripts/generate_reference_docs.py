"""Writes the two generated reference documents from the code itself, so that they cannot drift:

- docs/design/api/reference.md: every HTTP endpoint (path, role, parameters, response) and schema
- docs/design/database/schema-reference.md: every table of the ORM (columns, keys, constraints)

Run `python scripts/generate_reference_docs.py` after changing a route, a schema or a model;
`tests/test_reference_docs.py` fails when the files are out of date. `--check` only compares.

The database reference is read from the SQLAlchemy models, not from a live database, so it needs no
connection and is the same everywhere. CHECK constraints and some indexes exist only in the Alembic
migrations (`alembic/versions/`), which remain the source of truth for the schema; the models are
what the application reads and writes.
"""

import argparse
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.routing import APIRoute  # noqa: E402
from sqlalchemy import Table, UniqueConstraint  # noqa: E402

API_DOC = ROOT / "docs/design/api/reference.md"
DB_DOC = ROOT / "docs/design/database/schema-reference.md"

ROLE_LABEL = {
    0: "ログイン不要",
    1: "ログイン済み",
    2: "Viewer 以上",
    3: "Operator 以上",
    4: "Owner",
    5: "Owner(いずれかのワークスペース)",
}

# What the router modules are about, for the section headings.
MODULE_TITLE = {
    "auth": "認証・セッション",
    "workspaces": "ワークスペース",
    "connections": "取引所の接続・口座",
    "instruments": "銘柄",
    "market_data": "市場データ(ローソク足・自動取得・過去取得)",
    "market_data_gaps": "欠損データ",
    "trading": "取引口座・Bot・緊急停止",
    "trade_history": "取引履歴(約定・建玉)",
    "decision_history": "判断の履歴",
    "trading_halts": "取引停止(halt)",
    "backtests": "バックテスト",
    "notifications": "通知",
    "events": "イベントログ",
    "system_status": "システム状態",
    "client_logs": "画面のエラー報告",
}


def _first_sentence(text: str | None, limit: int = 110) -> str:
    if not text:
        return ""
    body = inspect.cleandoc(text).split("\n\n")[0].replace("\n", " ").strip()
    sentence = re.split(r"(?<=[.。])\s", body, maxsplit=1)[0]
    sentence = sentence.replace("|", "\\|")
    return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"


def _role_of(route: APIRoute) -> int:
    """The strictest role a route's dependencies demand (the closures `rbac` builds are told apart
    by identity, not by name)."""
    from app.security import rbac

    levels = {
        rbac.require_authenticated_user: 1,
        rbac.require_viewer_role: 2,
        rbac.require_operator_role: 3,
        rbac.require_owner_role: 4,
        rbac.require_any_workspace_owner: 5,
    }
    best = 0

    def walk(dependant) -> None:  # noqa: ANN001
        nonlocal best
        best = max(best, levels.get(dependant.call, 0))
        for child in dependant.dependencies:
            walk(child)

    walk(route.dependant)
    return best


def _type_of(schema: dict) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in schema:
        parts = [_type_of(part) for part in schema["anyOf"]]
        return " | ".join(part for part in parts if part != "null") + (
            " | null" if "null" in parts else ""
        )
    kind = schema.get("type", "any")
    if kind == "array":
        return f"{_type_of(schema.get('items', {}))}[]"
    if "enum" in schema:
        return " / ".join(f"`{value}`" for value in schema["enum"])
    if kind == "string" and schema.get("format"):
        return f"string({schema['format']})"
    return kind


def api_reference() -> str:
    import importlib

    from app.core.config import settings
    from app.main import app

    prefix = settings.api_v1_prefix
    openapi = app.openapi()
    schemas = openapi.get("components", {}).get("schemas", {})
    rows: dict[str, list[tuple]] = {}
    found = {
        path.stem for path in (ROOT / "app/api/routes").glob("*.py") if path.stem != "__init__"
    }
    modules = [name for name in MODULE_TITLE if name in found] + sorted(found - set(MODULE_TITLE))
    for module_name in modules:
        module = importlib.import_module(f"app.api.routes.{module_name}")
        router = getattr(module, "router", None)
        if router is None:
            continue
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            full_path = prefix + route.path
            for method in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
                operation = openapi["paths"].get(full_path, {}).get(method.lower(), {})
                parameters = operation.get("parameters", [])
                query = [p["name"] for p in parameters if p["in"] == "query"]
                body = (
                    operation.get("requestBody", {}).get("content", {}).get("application/json", {})
                )
                body_name = _type_of(body.get("schema", {})) if body else ""
                response = ""
                ok = next(
                    (code for code in operation.get("responses", {}) if code.startswith("2")), None
                )
                if ok:
                    content = (
                        operation["responses"][ok].get("content", {}).get("application/json", {})
                    )
                    response = _type_of(content.get("schema", {})) if content else ""
                    response = f"{ok}: {response}" if response else ok
                rows.setdefault(module_name, []).append(
                    (
                        full_path,
                        method,
                        ROLE_LABEL[_role_of(route)],
                        query,
                        body_name,
                        response,
                        _first_sentence(route.description or (route.endpoint.__doc__ or "")),
                    )
                )
    lines = [
        "# API リファレンス(自動生成)",
        "",
        "> このファイルは `python scripts/generate_reference_docs.py` が、"
        "コードから生成します。直接編集しないでください。"
        "ルート・スキーマを変えたら再生成します"
        "(`tests/test_reference_docs.py` が、古くなると失敗します)。",
        "",
        f"ベースパス: `{prefix}`。対話式の仕様(OpenAPI)は、API サーバーの `/docs` でも見られます。",
        "",
        "## 認証と権限",
        "",
        "- ログインは OIDC(`/auth/login` → IdP → `/auth/callback`)。"
        "成功すると、`HttpOnly`・`SameSite=Strict` の `session` Cookie が付く。"
        "画面からの呼び出しは `credentials: include` で、この Cookie を送る。",
        "- ワークスペースの資源は、メンバーの役割で制限する: "
        "**Viewer**(読み取り)< **Operator**(Bot の操作・補完・実行)"
        "< **Owner**(取引停止の解除など)。"
        "下の「権限」列は、必要な最低の役割。",
        '- エラーは `{"detail": "…"}`。404 は、存在しない・見えない(他のワークスペースの)資源、'
        "409 は状態の衝突、422 は入力の誤り。",
        "- 金額・数量は、精度を保つため、**文字列**の小数で返す"
        '(例: `"166667.000000000000000000"`)。時刻は ISO 8601。',
        "",
    ]
    for module_name in modules:
        if module_name not in rows:
            continue
        lines += [f"## {MODULE_TITLE.get(module_name, module_name)}", ""]
        lines += [
            "| メソッド | パス | 権限 | クエリ | 本文 | 応答 | 説明 |",
            "|---|---|---|---|---|---|---|",
        ]
        for path, method, role, query, body, response, summary in rows[module_name]:
            query_text = ", ".join(f"`{name}`" for name in query) or "-"
            lines.append(
                f"| {method} | `{path}` | {role} | {query_text} | {f'`{body}`' if body else '-'} | "
                f"{f'`{response}`' if response else '-'} | {summary or '-'} |"
            )
        lines.append("")
    lines += ["## スキーマ", "", "リクエストとレスポンスの形(フィールド名・型・必須)。", ""]
    for name in sorted(schemas):
        schema = schemas[name]
        properties = schema.get("properties", {})
        if not properties:
            continue
        required = set(schema.get("required", []))
        lines += [f"### {name}", "", "| フィールド | 型 | 必須 | 説明 |", "|---|---|---|---|"]
        for field, definition in properties.items():
            description = _first_sentence(definition.get("description"), 90) or "-"
            mark = "○" if field in required else ""
            lines.append(f"| `{field}` | {_type_of(definition)} | {mark} | {description} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _table_classes() -> dict[str, type]:
    from app.db.session import Base

    classes = {}
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if isinstance(table, Table):
            classes[table.fullname] = mapper.class_
    return classes


MODEL_GROUP = {
    "workspace": "ワークスペース・ユーザー",
    "connections": "取引所の接続・口座",
    "instruments": "銘柄",
    "market_data": "市場データ(ローソク足・自動取得・欠損・spread)",
    "strategy": "戦略・Bot・リスク判定・取引停止",
    "trading": "取引(注文・約定・台帳・建玉)",
    "backtest": "バックテスト",
    "audit": "監査・イベント・通知の送信待ち(Outbox)",
    "notifications": "通知",
}


def database_reference() -> str:
    import app.models  # noqa: F401
    from app.db.session import Base
    from sqlalchemy.dialects import postgresql

    classes = _table_classes()
    groups: dict[str, list[Table]] = {}
    for table in Base.metadata.sorted_tables:
        module = (
            classes[table.fullname].__module__.rsplit(".", 1)[-1]
            if table.fullname in classes
            else "other"
        )
        groups.setdefault(module, []).append(table)
    lines = [
        "# データベース スキーマ リファレンス(自動生成)",
        "",
        "> このファイルは `python scripts/generate_reference_docs.py` が、"
        "ORM のモデルから生成します。直接編集しないでください。"
        "モデルを変えたら再生成します"
        "(`tests/test_reference_docs.py` が、古くなると失敗します)。",
        "",
        "- PostgreSQL。スキーマは `fx`(設定 `DATABASE_SCHEMA`)。変更は Alembic"
        "(`alembic/versions/`)で管理し、**CHECK 制約や一部の索引は、"
        "マイグレーション側にだけある**(この文書はモデルから読めるものだけ)。",
        "- 主キーは UUID(`gen_random_uuid()`)。金額・数量は `NUMERIC(38,18)`。"
        "時刻は `timestamptz`(**UTC ではなく、サーバーのローカルタイムで返ることがある**。"
        "`docs/knowledge/database-times-are-not-utc.md`)。",
        "",
    ]
    for module in [name for name in MODEL_GROUP if name in groups] + [
        n for n in groups if n not in MODEL_GROUP
    ]:
        lines += [f"## {MODEL_GROUP.get(module, module)}", ""]
        for table in groups[module]:
            cls = classes.get(table.fullname)
            doc = _first_sentence(cls.__doc__, 200) if cls is not None else ""
            lines += [f"### `{table.name}`", ""]
            if doc:
                lines += [doc, ""]
            lines += ["| 列 | 型 | NULL | 既定値 | 参照・備考 |", "|---|---|---|---|---|"]
            for column in table.columns:
                notes = []
                if column.primary_key:
                    notes.append("主キー")
                for fk in column.foreign_keys:
                    on_delete = f" (ON DELETE {fk.ondelete})" if fk.ondelete else ""
                    notes.append(f"→ `{fk.column.table.name}.{fk.column.name}`{on_delete}")
                default = ""
                if column.server_default is not None:
                    default = str(getattr(column.server_default, "arg", column.server_default))
                    default = default.replace("|", "\\|")
                column_type = column.type.compile(dialect=postgresql.dialect())
                required = "" if column.nullable else "必須"
                default_text = f"`{default}`" if default else ""
                lines.append(
                    f"| `{column.name}` | {column_type} | {required} | "
                    f"{default_text} | {'; '.join(notes)} |"
                )
            extras = []
            unique_constraints = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
            for constraint in sorted(
                unique_constraints, key=lambda c: (str(c.name), [col.name for col in c.columns])
            ):
                columns = ", ".join(f"`{c.name}`" for c in constraint.columns)
                extras.append(
                    f"一意: {columns}" + (f"(`{constraint.name}`)" if constraint.name else "")
                )
            for index in sorted(
                table.indexes, key=lambda i: (str(i.name), [col.name for col in i.columns])
            ):
                columns = ", ".join(f"`{c.name}`" for c in index.columns)
                extras.append(f"索引: {columns}" + (f"(`{index.name}`)" if index.name else ""))
            if extras:
                lines += ["", *[f"- {item}" for item in extras]]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Only compare; exit 1 when out of date"
    )
    args = parser.parse_args()
    targets = {API_DOC: api_reference(), DB_DOC: database_reference()}
    stale = []
    for path, content in targets.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != content:
            stale.append(path)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8", newline="\n")
    if args.check and stale:
        print("Out of date: " + ", ".join(str(p.relative_to(ROOT)) for p in stale))
        return 1
    print(
        "Reference docs are up to date."
        if not stale
        else "Updated: " + ", ".join(str(p.relative_to(ROOT)) for p in stale)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
