# Alembic の env.py が、アプリのログを黙らせる

## 何が起きたか

`pytest` を、DB を使うテストと使わないテストを**同じセッション**で流すと(Docker での全テスト実行)、
ログを検査するテスト 4 件(`test_logging_integration.py`、`test_client_logs_api.py`、`test_public_research.py`)が、
単独では通るのに、失敗した。ログファイルが空だった。

## 原因

`alembic/env.py` の先頭が `logging.config.fileConfig(alembic.ini)` を、既定の
`disable_existing_loggers=True` で呼んでいた。DB のテストがマイグレーションをプロセス内で実行するたびに、
その時点で存在するロガー(`app.main` など、import 時に作られるもの)がすべて無効になり、
以降のテストでは、`configure_logging` でハンドラーを付け直しても、何も出力されない。

CI では、DB を使うテスト(`worker-storage`)と、使わないテスト(`backend`)を別のジョブで流すため、
同じセッションで混ざることがなく、気づけなかった。

## 守ること

- プロセス内で Alembic を動かすコードは、`fileConfig(..., disable_existing_loggers=False)` で呼ぶ
  (`tests/test_alembic_logging.py` が、`env.py` の呼び出しを検査する)。
- テストの順序や組み合わせに依存した失敗は、`python scripts/test_in_docker.py` で、全テストを 1 セッションで流すと見つかる。
