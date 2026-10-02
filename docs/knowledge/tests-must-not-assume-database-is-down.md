# テストは「PostgreSQLが動いていない」ことを前提にしない

## 何が起きたか

`tests/test_logging_integration.py::test_error_response_status_code_is_still_logged` は、
`/api/v1/health/db` が 503 を返すことを、テストのプロセスから PostgreSQL に接続できないことに
頼って起こしていた。CI の backend ジョブには DB がないので通るが、ペーパートレード用に
ローカルの PostgreSQL を常時動かしている開発機では 200 が返り、失敗していた
(2026-10-02、Unit 5 の PR 前の確認で発覚)。

## 守ること

- 開発機ではペーパートレードの Worker のためにローカル PostgreSQL が動いている前提で考える。
  「DB に繋がらない」ことで起きる経路をテストするときは、`app.dependency_overrides[get_db]` を
  `execute` が `SQLAlchemyError`(例: `OperationalError`)を投げるスタブに差し替える
  (`tests/test_health.py` と同じ書き方)。
- 差し替えは `try/finally` で `app.dependency_overrides.clear()` し、他のテストに漏らさない。
- 実 DB が必要なテストは `WORKER_TEST_DATABASE_URL` で専用 DB を指定する既存の方式に従い、
  開発用 DB を使わない。
