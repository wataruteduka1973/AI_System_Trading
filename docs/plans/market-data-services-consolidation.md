# 市場データの旧サービス層を app/market_data/ に統合する

- 状態: `[x]` 完了(2026-10-03)。Unit 1 は PR #78、Unit 2・3 は PR #82(#79・#80 の内容)、Unit 4 は
  この文書の更新と同じPR。Unit 3 の挙動変更は2026-10-03に利用者が推奨案で承認(「決めたこと」を参照)。
  結果と残った課題は末尾の「実施結果」を参照
- 作成: 2026-10-03(技術的負債リスト #10)
- 正とする設計: `docs/architecture/current-and-target.md` の「Target modules」と依存方向
  (`API or Worker -> Application -> Domain`、`Infrastructure` は Application から使う)

## 目的

市場データのコードが `app/services/market_data.py`(520行)と `app/market_data/` の2か所に分かれている。
目標構造では `app/market_data/` が「candles, coverage, subscriptions, backfill」を持ち、`app/services/` は
過渡的な依存(`market-data-application-boundary.md`)とされている。Durable Worker と Application 境界の
移行が終わった今、旧サービス層を目標の場所へ移し、次の問題を解消する。

## 調査で分かったこと

1. **`CandleIngestionService` はほぼ空になっている。** 取り込みは Worker に移り、残っているのは
   資格情報の事前確認(`validate_configuration`)と、`upsert_candle_points` を呼ぶだけの
   `_upsert_points` だけ。Worker(`infrastructure/pages.py`)はこの private メソッドを外から呼んでいる。
2. **「ワークスペースが選んだ口座・検証済み接続」を引くクエリが3つある。**
   `CandleIngestionService._resolve_access`、`PageAccess.resolve`(Worker)、
   `stream_connection_access._fetch_row`(リアルタイム配信)。条件が揃っていない。

   | 確認する条件 | API の事前確認(services) | Worker / 配信 |
   |---|---|---|
   | 接続が verified、口座が active | ○ | ○ |
   | 銘柄・取引所・ワークスペースが active | × | ○ |
   | 口座と接続の環境(practice/testnet)が一致 | × | ○ |
   | 接続先が Practice / Testnet のURLか | × | ○ |
   | 判断を commit まで固定する共有ロック | × | ○ |

   その結果、**API は受け付けたのに Worker が `access_unavailable` で失敗するバックフィル**がありうる。
   安全性の穴ではない(実際に取引所へ接続する Worker 側が拒否する)が、利用者には遅れて失敗が見える。
3. **事前確認のラッパーが2つのルートに重複している**(`api/routes/market_data.py` と `instruments.py` の
   `_validate_collection_configuration`)。どちらも、アクセスできない理由がなんであっても
   「保存済み資格情報を読み込めません」という1つの文言で 409 を返す。一方で
   `application/stream_tickets.py` は、同じエラーをコード別(`access_unavailable` / `credentials_missing` /
   `credentials_unreadable`)に変換していて、ルートの対応表(`_application_errors`)にもそのコードがある。
4. **層をまたいで private 名を import している**: `application/use_cases.py` が `_advisory_lock_key` を、
   `pages.py` が `CandleIngestionService._upsert_points` を使っている。
5. 残りの関数は性質で分けられる。
   - DBを使わない計算: `find_internal_gaps`、`classify_candle_coverage`、`_is_expected_market_time`、
     `GapWindow`、`IngestionReport`
   - DBの読み書き: `upsert_candle_points`、`build_candle_coverage`、`persist_internal_gaps`、
     `_gap_is_filled`、`ensure_no_overlapping_backfill`(advisory lock を取る)、`_advisory_lock_key`

## 範囲

- 対象: `app/services/market_data.py` の中身すべてと、その import 元(app 10ファイル、tests 8ファイル)。
- 対象外: `app/services/secrets.py`(`LocalEncryptedSecretStore`)。接続管理など22ファイルが使う共通の
  基盤で、目標構造では `shared/` に属する。市場データとは別の判断なので、この計画では動かさない。
  この計画を終えると `app/services/` には `secrets.py` だけが残る。
- APIのパス・レスポンスの形・DBスキーマは変えない。Unit 3 だけ、エラーの返り方が変わる(後述)。

## 移動先

再エクスポート用の互換モジュールは作らない。import 元はそのUnitの中ですべて書き換える。

| 現在(`app/services/market_data.py`) | 移動先 | 層 |
|---|---|---|
| `GapWindow`、`find_internal_gaps`、`_is_expected_market_time`、`classify_candle_coverage` | `app/market_data/domain/coverage.py` | Domain(新設。DBを使わない計算だけ) |
| `IngestionReport` | `app/market_data/domain/ingestion_report.py` | Domain |
| `upsert_candle_points`、`build_candle_coverage`、`persist_internal_gaps`、`_gap_is_filled` | `app/market_data/infrastructure/candle_store.py` | Infrastructure |
| `ensure_no_overlapping_backfill`、`DuplicateBackfillError`、`_advisory_lock_key`(→ 公開名 `advisory_lock_key`) | `app/market_data/infrastructure/backfill_locks.py` | Infrastructure |
| `MarketDataAccessError`、3つのアクセス解決クエリ | `app/market_data/infrastructure/access.py`(1つのクエリに統合) | Infrastructure |
| `CandleIngestionService` | 削除。事前確認は `access.check_collection_access`、Worker は `upsert_candle_points` を直接呼ぶ | — |

`domain/` は、中に置くものができたので作る(`current-and-target.md`「Empty layers are not created in advance」に沿う)。

## データの流れ(変更後)

```text
API (market_data / instruments routes)
  -> application/use_cases.enqueue_backfill / update_subscriptions
       -> infrastructure/access.check_collection_access   # Worker と同じ条件で事前確認
       -> infrastructure/backfill_locks.ensure_no_overlapping_backfill
       -> MarketDataAccessError をアプリのエラーコードに変換(stream_tickets と同じ)
  -> route は既存の対応表(_application_errors)でHTTPに変換

Worker (infrastructure/pages.py)
  -> infrastructure/access.resolve_page_access          # PageAccess.resolve の中身
  -> infrastructure/candle_store.upsert_candle_points / persist_internal_gaps / build_candle_coverage
  -> domain/coverage, domain/ingestion_report

Realtime stream
  -> infrastructure/access(stream 用の資格情報取り出しは stream_connection_access に残す)
```

依存の向き: `domain` は SQLAlchemy・取引所SDK・`infrastructure` を import しない。`infrastructure` は
`domain` を使ってよい。

## 実装の順序

挙動を変えない移動(Unit 1・2・4)と、挙動を変える統合(Unit 3)を別のPRに分ける。

### Unit 1: DBを使わない計算を domain/ に移す(挙動の変更なし)

- `domain/coverage.py`、`domain/ingestion_report.py` を作り、上の表の関数・クラスを移す。
- import 元を書き換える: `pages.py`、`tests/test_market_data.py`。
- テスト: 既存の `test_market_data.py`(ギャップ検出・カバレッジ分類)がそのまま通ること。
  `domain` が SQLAlchemy を import していないことを確かめるテストを1本足す(import の静的検査)。

### Unit 2: DBの読み書きを infrastructure/ に移す(挙動の変更なし)

- `candle_store.py`、`backfill_locks.py` を作って移す。`_advisory_lock_key` を公開名にする。
- `pages.py` は `CandleIngestionService(...)._upsert_points(...)` をやめて `upsert_candle_points` を
  直接呼ぶ。
- `tests/test_worker_leases_postgres.py` は `CandleIngestionService._upsert_points` を差し替えて
  「書き込み途中の中断」を再現している。差し替え先を `pages` モジュールの `upsert_candle_points` に
  変える(同じ場面を再現できることを確認する)。
- import 元を書き換える: `use_cases.py`、`public_research.py`、`pages.py`、
  `tests/test_worker_runner_postgres.py` ほか。
- テスト: 既存のテストすべて。PostgreSQL のテスト(`-m postgres`、CIの worker-storage ジョブ)が
  通ることが必須(advisory lock と upsert は PostgreSQL 固有のため)。

### Unit 3: アクセス確認を1つにまとめ、APIの事前確認を Worker と同じ条件にする(挙動の変更あり)

- `infrastructure/access.py` に、選択済み口座・検証済み接続を引くクエリを1つだけ置き、
  `PageAccess.resolve`・配信・APIの事前確認の3つから使う。`MarketDataAccessError` もここへ移す。
- `check_collection_access(db, secrets, workspace_id, instrument_id)`: Worker と同じ条件と、
  資格情報が読めること・必要なキーが揃っていることを確かめる(取引所へは接続しない)。
- `use_cases.py` は `MarketDataAccessError` を受けて、`stream_tickets.py` と同じくコード別の
  `MarketDataApplicationError` に変換する。2つのルートの `_validate_collection_configuration` は削除する。
- `CandleIngestionService` を削除する。
- テスト:
  - 回帰テスト(先に書いて失敗を確認): 銘柄が inactive、環境が不一致、URLが Practice/Testnet 以外の
    とき、APIのバックフィル登録・収集開始が 409 になること(今は通ってしまう)。
  - 資格情報がない・読めないとき、これまでどおり 409 になること(文言はコード別になる)。
  - 3つの呼び出し元が同じクエリを使うこと(条件の差が再発しないこと)。

### Unit 4: 旧モジュールを削除し、文書を更新する

- `app/services/market_data.py` を削除する。
- `docs/architecture/current-and-target.md` の「Market-data Application boundary」と、README の
  ディレクトリ構成(`services/` の説明)を更新する。`docs/plans/README.md` の状態も更新する。
- 確認: `grep -r "app.services.market_data"` が0件。

## 決めたこと(2026-10-03 利用者承認、推奨案)

Unit 3 は、APIが返すエラーが変わる。

1. **APIの事前確認を Worker と同じ厳しさにする。** これまで「APIは受け付けたが、あとで Worker が
   失敗する」だったものが、登録時点で 409 として返るようになる。ステータスコードは今と同じ 409 で、
   変わるのは返る時点と文言。(採らなかった案: Unit 1・2・4 の移動だけを行い、3つのクエリの重複と
   条件の差を残す)
2. **409 の文言をコード別にする**(アクセス不可/資格情報なし/資格情報が読めない)。いまは理由に
   関係なく「保存済み資格情報を読み込めません」。画面は `detail` をそのまま表示するだけ
   (`frontend/src/lib/api.ts` の `apiErrorMessage`)で、文言で分岐している箇所はないため、
   フロントエンドの変更は不要。

## テストと検証(各Unit共通)

- `ruff check` / `ruff format --check` / `mypy` / `mypy --platform win32` / `pytest`
- PostgreSQL を使うテスト(`-m postgres`)はCIの worker-storage ジョブで確認する
- Unit 3 は、手元の画面でバックフィル登録と収集開始が通常どおり動くことを確認する
  (開発DBへの書き込みを伴うので、実施前に利用者に確認する)

## 想定リスク

- **稼働中の Worker への影響**: 市場データ Worker とトレーディング Worker は main のコードで動いている。
  Unit 2・3 は Worker が使うコードを変えるので、マージ後に Worker の再起動が必要。挙動は Unit 3 の
  事前確認以外は変わらない。再起動はペーパートレードの評価の合間(4h足の確定直後を避ける)に行う。
- **PostgreSQL 固有の挙動**: advisory lock、`ON CONFLICT` の upsert、共有ロックは SQLite では
  再現できない。`-m postgres` のテストが通ることを各PRのマージ条件にする。
- **ロックの範囲**: Unit 3 で API の事前確認にも共有ロック(`FOR SHARE`)が付く。登録のトランザクションは
  短いので影響は小さい見込みだが、Worker の lease 取得と競合しないことをテストで確認する。
- **import の循環**: `domain` を最下層にし、`infrastructure` から `application` を import しない。
  mypy と import の静的検査で確認する。
- **並行するPRとの衝突**: 開いているPR(#72〜#76)はこの計画の対象ファイルを変更していない
  (#76 は `current-and-target.md` 内のパスを変更する。Unit 4 は #76 のマージ後に行う)。

## 実施結果(2026-10-03)

- `app/services/market_data.py` を削除した。`app/services/` に残るのは `secrets.py` だけ。
- 移した定義は、名前の変更(`_is_expected_market_time` → `is_expected_market_time`、
  `_advisory_lock_key` → `advisory_lock_key`)を除いて元とASTが同一であることを確認した(Unit 1・2)。
- Unit 3 で計画との違いが2つあった。
  - 接続先が Practice/Testnet 以外のとき、取引所クライアントの例外(`BinanceApiError` /
    `OandaApiError`)が出る。Worker はこれを従来どおり分類するので変えず、APIの事前確認
    (`check_collection_access`)の中だけで `access_unavailable` に変換した。変換しないと
    APIは500を返す。
  - 銘柄同期の自動収集開始(`instruments._auto_start_collection`)で、重複以外のアプリケーション
    エラーがHTTPに変換されず500になっていた既存の不具合を、市場データのルートと同じ対応表
    (`application_errors`)で返すように直した。
- 空になった旧モジュールは Unit 3 で削除した(計画では Unit 4)。
- **残った課題(→ 2026-10-03 に解消)**: `use_cases._require_instrument_access` も口座選択を引く
  クエリを持ち、`access.py` より条件が緩かった。閲覧系(ローソク足一覧・カバレッジ・バックテスト)の
  認可で、厳しくすると挙動が変わるためこの計画では扱わず、利用者の判断(推奨案で承認)のあと
  `access.instrument_is_readable` に揃えた(ロックなし。公開名 `require_instrument_access` に変更)。
- **運用上の注意**: 積み上げたPR(#79・#80)が中間ブランチにマージされ main に届かなかったため、
  PR #82 で入れ直した。以後、PRは main 向けだけにする。
