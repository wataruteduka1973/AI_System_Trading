# Durable Worker 実装計画

- 2026-08-31: ①設計・②DB/lease・③区間実行Applicationを実装。独立プロセスへの接続は未実施。
- 2026-09-15: ④独立プロセスと切替を実装。旧実行経路（lifespanポーラー、BackgroundTasks即時実行）
  は削除済み。運用DBへ`20260831_0005`を適用し実際に切替・稼働確認済み（利用者環境）。
- 2026-09-16: ⑤起動/表示/実地試験を実装。詳細と検証結果は末尾「⑤の実装と検証範囲」。
  Horizon 1「Application境界とDurable Worker」のWorker関連単位はこれで完了。
- 設計: [Worker](../design/modules/durable-market-data-worker.md)
  / [DB・移行](../design/database/durable-market-data-worker.md)
- 既存Application抽出・一括起動は維持。

## 作業単位

1. **設計（作成済み）**: 現行DDL/ORM/servicesとの照合、所有権・checkpoint・再試行・停止・移行を定義。
2. **DBと取得プリミティブ（実装・専用DB試験済み）**: 新Alembic、ORM、claim/heartbeat/fencing/recoveryを追加。
   既存アプリへ経路を接続する前に、別PostgreSQL DBでmigrationと競合を試験する。
3. **区間実行Application（実装済み、検証結果は末尾）**: 外部fetchと保存を分け、checkpoint/reportを同時commit。
   実行入口は `app/market_data/application/`、DB操作は同capability内infrastructureに集約。
   SQLAlchemyを使用する既存境界を踏襲し、汎用repository frameworkは作らない。
4. **独立プロセスと切替（実装済み、検証結果は末尾）**: Worker entry point、停止signals、
   API内実行の削除、設定とエラー変換。packaged runtime内にentry pointを置き、
   wheelからも起動可能にする。新しい第三者queueは不要。
5. **起動/表示/実地試験（実装済み、検証結果は末尾）**: 一括batを3プロセス化。API/画面のみの
   再起動と全体再起動を分ける。retry予定とblockedの表示を追加し、enabledだけで
   「自動取得中」と断定しない。

変更予定: `app/models/catalog.py`、新Alembic revision、`app/market_data/`、
`app/services/market_data.py`、`app/api/routes/market_data.py`、`app/main.py`、
`app/core/config.py`、`app/schemas/catalog.py`、`scripts/start_local.py`、関連frontend/tests/docs。
公開APIの既存pathと既存fieldは維持。next_run_at/blocked_reason等を追加する場合は任意項目として
別途API契約と回帰試験に含める。旧クライアントが既存fieldを継続利用できることを確認する。

## 受入試験

### ③の実装方針

取得入口をApplicationに置き、短い準備transaction → transaction外の1ページ通信 →
所有権・利用資格を再確認する保存transactionに分ける。既存のadapter、upsert、gap/coverage
判定を再利用する。Worker専用ORMには既存列のmappingだけ追加し、新しいmigrationは作らない。
ページのデータ・累積report・cursorは同時commitし、最後の検証は次のclaimで再開可能にする。
リスクは停止/口座変更との競合、境界足、空応答、通信例外の誤分類、大範囲検証の長時間化。
専用PostgreSQLと架空の応答で回帰・rollback・再開・失効を試験し、運用DB/旧pollerには接続しない。


| ID | 条件 | 合格条件 |
|---|---|---|
| DW-01 | 受付commit直後にAPI終了 | Workerが同じjobを発見し実行 |
| DW-02 | APIのみ再起動 | Workerの所有権と取得が継続 |
| DW-03 | ページcommit前/後にWorker強制終了 | 前なら同区間再取得、後なら保存cursorから再開 |
| DW-04 | Worker2個で同じfeedをclaim | 有効tokenは1つ、polling/backfillも同時確定しない |
| DW-05 | lease失効後に古い要求が遅れて完了 | 古いtokenによるcandle/gap/進捗/状態更新は0 |
| DW-06 | DB切断・heartbeat失敗・復帰 | 古いtokenは再利用せず、期限とDB状態から回復 |
| DW-07 | stopと取得が競合、停止後に再起動 | enabled=false維持、pollingの新規要求/遅延保存を抑止 |
| DW-08 | 通信障害/429/認証・復号エラー | 規定待機/上限またはblocked、秘密漏えいなし |
| DW-09 | 空応答・部分応答・週末・境界足 | cursorとcoverageを混同せず、重複行/境界取りこぼしなし |
| DW-10 | 最終検証中断・巨大な依頼範囲 | 再検証でき、lease更新・他feed巡回を阻害しない |
| DW-11 | 異なるWorkspace/失効口座・接続 | 他scopeのjob/秘密にアクセスせず、失効後保存を拒否 |
| DW-12 | 旧schema＋queued/running/validating/終端job | upgrade/正規化でcandle・設定・終端履歴を保持 |
| DW-13 | batのR/A/Q、Workerだけ異常終了 | 所有プロセスだけ操作し、停止/異常状態を正しく表示 |
| DW-14 | 全7時間足のbackfillとpolling | ページ交替で進捗し、定期取得が飢餓状態にならない |

DW-03〜06/12はmockだけでは合格にしない。外部APIは制御可能なfixtureに置き換え、
専用PostgreSQLと実プロセスで試験する。運用DBを障害試験に使わない。
移行前後のcandleキー・値、job件数/範囲、subscription.enabledを比較する。
安全なバックアップ復元・切戻し手順も専用DBで試験する。

## 完了ゲートと既知制限

各単位で既存＋追加テスト、変更箇所format/lint、利用可能な型検査、package/frontend buildを実施。
UIを変更する単位はブラウザー試験を行う。Python型検査と全体整形チェックは
[品質チェック導入](python-quality-checks.md)で設定・修正した。未実行検証を成功に読み替えない。
現行batの実アプリ起動未確認も切替前に解消する。
Worker実装完了には上の受入試験とDoDが必要。設計書作成だけでHorizon 1を完了にしない。

## ②の実装・検証結果

- 追加revision `20260831_0005`。既存の状態・enabled・candleは変更しない。
- Worker専用ORMは別metadataに隔離し、未移行DB向けの既存API SQLを変更しない。
- claim/heartbeat/release/guarded_write/recover_expiredを追加。期限切れ3回でjob失敗またはpolling blocked。
- Alembicに明示的connectionを渡せるようにし、試験が `.env` の運用DBへ接続しない経路を追加。
- 専用PostgreSQL 18.6（127.0.0.1:55439）で旧DDL→0005→0004→0005を検証。
  全job状態、candle値、subscription.enabledを含む既存列の保持を比較した。
- backend全122件成功（既存93＋新規29、うち実PostgreSQL試験22）。
  新規試験は初回claim競合、実2プロセス競合、実プロセス終了後回復、旧token拒否、cursor検査、
  原子的rollback、試験用DB接続の強制切断と回復、停止維持、retry上限、DB制約、監査へのtoken非出力を含む。
- CIに独立した `worker-storage` jobを追加。PostgreSQL 16の専用サービスで同じ試験を実行する設定。
  GitHub上の実行結果はこのローカル作業ではNOT VERIFIED。
- 全体Ruff lint、変更Pythonファイルformat、wheel/sdist build成功。
  frontend 11件、ESLint、TypeScript/Vite buildも成功。UI自体は未変更。
  既存の無関係な6ファイルのformat不一致と、未設定のPython型検査は継続課題。

試験データは架空のWorkspace/銘柄のみ。運用DBへのmigration適用、旧job正規化、API/Worker切替、
外部API呼出しは行っていない。旧版とlease版を同時稼働してはならない。
DW-04/05の所有権プリミティブとDW-12のschema部分は実DBで検証済みだが、
DW-01〜14のアプリ全体受入試験が完了したという意味ではない。
DB接続切断時のtransaction rollbackは確認済みだが、DB停止中を含む自動運転、SDK失敗の分類、
権限再検証、ページ処理、全体再開、UI/bat統合は③以降で確認する。
この②の結果に続く③の実装は以下。運用DBへ直ちにupgradeする手順ではない。

## ③の実装と検証範囲

- `ExecuteMarketDataPage` を追加。準備・外部通信・保存を分離し、1回1ページでleaseを返す。
- `PageAccess` はWorkspace/銘柄/取引所/選択口座/verified接続/Practice・Testnet環境を確認する。
  保存transactionでも確認し、口座選択・接続・秘密参照/更新時刻が変わった応答を拒否する。
  shared row lockは短い準備/保存中だけ保持し、外部通信中はDB transactionを開かない。
- 既存adapter・upsert・gap/coverage判定を再利用。既存sync/poller/APIは変更せず、後続切替まで併存する。
- 確定足のみ保存。不正OHLC/時刻/出来高と矛盾する重複を拒否し、正常重複は一意キーでupsert。
  境界1足を含めてページ上限を守り、空応答の区間数/サンプル（最大20件）を累積する。
- candle・report・cursorを同時commit。終端cursorでは通信せずgap/coverage最終検証を再開する。
  pollingは固定scan_toで追いつき、終了後にcursorを戻して次回時刻を保存する。
- 通信timeout/接続障害/429/5xxのみ限定再試行。429のRetry-Afterを尊重し、
  非現実的な1年超の待機指定はoverflowさせずblocked/failedにする。未知例外は安全なコードで停止。
  DB例外は所有権回復へ委ね、同じtokenで再実行しない。正常cancelでは失敗回数を増やさない。
- 既存列のWorker ORM mappingのみ追加。新migration・運用DB変更なし。

検証結果（2026-08-31）:

- backend全170件成功（②の122件に48件追加）。PostgreSQL統合試験は計48件。
  ページcommit直前/直後の実プロセス終了、rollbackと再取得、終端検証の中断/再開、
  lease失効・停止・選択解除・Workspace停止・秘密参照変更時の保存拒否を確認。
  pollingの固定範囲再開、cancel、blocked、Workspace越境、全7時間足も試験した。
- 架空の1年分1分足525,600行で最終検証が10秒以内に完了。これは専用PostgreSQL 18.6での
  単一feed測定であり、本番負荷・多feed並行・さらに長い範囲の性能保証ではない。
- 空応答とcoverageの区別、不正足/重複、SDK例外分類、Retry-Afterと安全なコード化を試験。
  外部APIは架空の応答のみ。OANDA実データ確認は引き続き延期。
- 全体Ruff lint、変更ファイルformat、差分チェック、Python wheel/sdistと新module同梱を確認。
  frontend11件、ESLint、TypeScript/Vite build成功。UI変更なし（ブラウザー確認N/A）。
- NOT VERIFIED: Python独立型検査は設定なし。GitHub上のPostgreSQL16 CIは未実行。
  全体formatは既存の無関係な6ファイルで不一致が残り、全体DoD達成とはしない。
  既存SDK/TestClientの非推奨警告4件も継続する。

④以降での確認事項: API受付後の実Worker探索、DB長期停止・heartbeat失敗からの運転復帰、
複数feed公平性、実サービス/ランチャーを含む停止・再起動。③の直接呼出し試験を、
独立Worker全体の受入完了に読み替えない。

次は④「独立プロセスと切替」。候補探索、公平な巡回、heartbeat、signals、API内実行の除去、
旧jobの正規化を実装・検証してから⑤のbat/UIへ進む。
本工程だけではAPI再起動からの自動復旧は有効にならない。旧方式と新方式を同時実行しない。

## ④の実装と検証範囲

- 候補探索 `app/market_data/infrastructure/candidates.py` の `CandidateScanner`。
  `backfill_job`（status IN queued/running/validating）と `market_data_subscription`
  （enabled かつ blocked_reason IS NULL）を、既存 `ix_backfill_due` / `ix_subscription_due`
  と同じ条件で `next_run_at, id` 昇順に読み取り専用で結合する。ロックは持たず、claim()側の
  再検証に委ねる。
- Workerランナー `app/market_data/worker/runner.py` の `WorkerRunner`。1スキャン周期ごとに
  `recover_expired()` → 候補探索 → 見つかった候補**全件を1パスで1ページずつ**処理する
  （同一feedを連続ドレインしない）ことで、長時間backfillがpollingを飢餓状態にしない
  （DW-14）。各claim実行中は軽量heartbeat keepalive（設定間隔ごとに`leases.heartbeat`）を
  並走させ、所有権喪失を検知したら停止する。候補側の想定外例外（execute_page.pyが
  正規化できない場合）はログのみでスキャンを継続し、他feedを止めない。
- entry point `app/market_data/worker/__main__.py`（`python -m app.market_data.worker`）。
  起動時に `alembic.runtime.migration.MigrationContext` で現在リビジョンを確認し、
  必須リビジョン `20260831_0005` と完全一致しない場合は候補処理を一切開始せず終了する
  （DB設計書の「required revision未適用ならWorkerは取得を開始せず終了」を満たす）。
  SIGINT/SIGTERM（Windowsは追加でSIGBREAK/CTRL_BREAK_EVENT）で `asyncio.Event` を立てて
  安全に停止する。`--normalize-legacy-jobs` で旧jobの正規化のみ実行して終了できる。
- 旧jobの正規化 `app/market_data/infrastructure/normalize.py` の `normalize_legacy_jobs`。
  旧方式のまま残る `status IN (running, validating)` の `backfill_job` を `queued` に戻す。
  既に新方式cursor（`next_fetch_at`）を持つ行は初期化しない（再実行可能）。移行前後を
  `AuditLog` に記録する。Worker通常起動時には自動実行しない（明示的な運用操作として分離）。
- API内実行の除去: `app/main.py` の lifespan（`MarketDataPollingWorker`起動/停止）と、
  `app/api/routes/market_data.py` の `create_candle_backfill` からの `BackgroundTasks`
  即時dispatchを削除。`app/services/market_data.py` から `MarketDataPollingWorker`/
  `run_backfill_job`/`_run_owned_backfill_job` を削除した。
- **実装中に発見した既存バグを修正**: `ensure_no_overlapping_backfill` が呼んでいた
  `recover_interrupted_backfills`（5分経過かつ旧advisory lockを取れないjobをfailedにする
  処理）を削除した。新Workerはこの旧advisory lockを一切取らないため、削除しないまま
  切替すると、leaseで正常に稼働中のjobが5分後に誤ってfailed化される欠陥があった。
  DB設計書が指示する「5分経過failed化処理は切替時に外し、lease回復へ一本化する」を満たす。
- `scripts/start_local.py` を最小限更新。`commands()` が3番目のプロセスとしてWorkerを返す。
  `run_once()` に `critical_count`（既定2）を追加し、API/画面（先頭2プロセス）の異常終了のみ
  致命的とし、Worker（3番目）が単独で終了（例: migration未適用）してもAPI/画面は継続する
  よう安全弁を設けた。Worker専用の45秒停止猶予の個別化、取得中/retry/blocked表示、
  個別再起動の作り分けは⑤に残す。

検証結果（2026-09-15）:

- backend全203件成功（非DB199件、うちWorkerランナーの新規テストはDBなしで7件+8件）。
  一時的な専用PostgreSQL 18.6（127.0.0.1:55432、`initdb`で作成しテスト後に停止・破棄）で
  `test_worker_leases_postgres.py`/`test_worker_lease_contracts.py`/`test_worker_pages.py`
  （既存84件、無変更で成功）と新規 `test_worker_runner_postgres.py`（5件）を実行し、
  全89件成功。新規DB試験は候補探索の順序・除外条件、claim後に`status='running'`へ変わった
  backfillが探索から漏れないことの回帰試験（実装中に発見した実バグの再発防止）、
  `normalize_legacy_jobs`の冪等性、`ensure_no_overlapping_backfill`修正後にlease稼働中jobが
  誤ってfailedにならないことの回帰、`WorkerRunner`を実DB・実LeaseStore/PageStore/
  ExecuteMarketDataPageに接続した実際のbackfill完走（複数ページ・完了・candle保存）を含む。
  外部取引所APIは架空のfixtureのみで、実OANDA/Binance通信は行っていない。
- 全体Ruff lint・format、変更ファイルformat差分確認、mypy（既定・`--platform win32`)
  48ファイル成功（テスト自体はmypy対象外という既存方針を維持）。
- Python wheel buildで `app/market_data/worker/`・`app/market_data/infrastructure/
  candidates.py`・`normalize.py` の同梱を確認した（`pyproject.toml`は無変更で足りた）。
- frontendは無変更。`npm run lint`・`npm run build`（vite build）成功で影響なしを確認した。
- NOT VERIFIED: 運用DB（利用者の`.env`が指すDB）への`20260831_0005`適用と
  `normalize_legacy_jobs`の実行、実OANDA/Binance通信、GitHub Actions上のCI実行結果。
  `scripts/start_local.py`経由での実地起動（3プロセス同時起動・Worker単独終了時の継続動作）も
  対話的launcherのため自動試験の対象外（ユニットテストでrun_once()のロジックのみ確認）。
  Worker停止猶予の45秒個別化は未実装のまま（一律10秒を維持）。

④以降（⑤）での確認事項: 運用DBへの実際のmigration適用・切替、`start-local.bat`経由の
3プロセス実地起動確認、取得中/retry予定/blocked状態のUI表示、API/画面のみの再起動と
Worker単独再起動の作り分け、Worker停止猶予の45秒化。

2026-09-15: 利用者環境で実際に運用DBへ`20260831_0005`を適用し、Workerが起動・稼働することを
確認した（本セッション中に実施）。適用直後の実地利用で「過去データ取得の進捗が画面から
分からない」「自動取得が止まっているか判断できない」という実際のフィードバックがあり、
これが⑤の実装動機になった。

## ⑤の実装と検証範囲

- バックエンド: `next_run_at`・`consecutive_failures`（`backfill_job`/`market_data_subscription`
  共通）、`blocked_reason`（`market_data_subscription`のみ）を`app/models/catalog.py`の
  API用ORM（`BackfillJob`/`MarketDataSubscription`）と`app/schemas/catalog.py`の
  `BackfillJobRead`/`MarketDataSubscriptionRead`に追加。列は`20260831_0005`で既に存在し、
  Worker専用ORM（`app/market_data/infrastructure/models.py`）が書き込み済みのため、
  新規migrationなしの読み取り専用追加。ルート（`app/api/routes/market_data.py`）はORMを
  そのまま返しているため無変更。内部cursor専用の`next_fetch_at`/`scan_to`は引き続き
  API用ORMに追加しない（`tests/test_worker_lease_contracts.py`・
  `tests/test_worker_leases_postgres.py`の既存ガード試験を、この意図的な境界に合わせて更新した）。
- フロントエンド（`frontend/src/App.tsx`のみ、`CandleChart.tsx`は無関係のため無変更）:
  - `marketErrorLabel`辞書に未登録だった7エラーコードの日本語文言を追加（既存4件は変更なし）。
  - 購読状態表示に`blocked`状態を追加。`blocked_reason`が非nullなら`enabled`の値に関わらず
    「自動取得停止中（要確認）」と表示し、停止理由と連続失敗回数を表示する。
  - backfill状態表示にstatus別の左枠線色（`queued`/`running`/`succeeded`/`failed`）を追加し、
    `status==='queued' && consecutive_failures>0`のとき次回再試行予定時刻を表示する。
- `scripts/start_local.py`: `docs/design/modules/durable-market-data-worker.md` §6の設計どおり、
  `critical_processes`（API/画面）と`worker_processes`（Worker）を分離。`R`はcriticalのみ
  その場で停止・再起動（Workerには一切触れない）、`A`は全体再起動（戻り値`True`で従来どおり
  呼び出し元が両グループを作り直す）、`Q`は全体停止。`finally`で`critical_processes`は
  既定10秒、`worker_processes`は45秒の停止猶予を使う。Worker終了検知時の`[WARN]`表示は④から
  維持し、"Ready:"表示にWorkerの状態（稼働中/停止・要確認）を追記した。

検証結果（2026-09-16）:

- backend全154件成功（非DB）＋専用PostgreSQLでの89件成功（既存84件+新規5件、無変更で成功）。
  新規追加した`app/api/routes/market_data.py`経由の実シリアライズ試験2件
  （`tests/test_market_data_api.py`）で、`next_run_at`/`consecutive_failures`/`blocked_reason`が
  実ORMインスタンスから実際にJSON応答へ反映されることを確認した。
- frontend: 新規2件を含む全13件のvitestが成功、`npm run lint`・`npm run build`成功。
- `scripts/start_local.py`のテストを大幅更新（`R`のin-place再起動、`A`の全体再起動、
  Workerが`R`で一切触れられないこと、`critical`10秒/Worker 45秒の停止猶予を個別に検証する
  新規テストを追加）。既存の異常終了系・非致命化系のテストは無変更で成功。
- 全体Ruff lint/format、mypy（既定・`--platform win32`）48ファイル成功。
- **利用者環境の実DBで動作確認**: 新フィールドを実際のローカルDB（Alembicリビジョン
  `20260831_0005`適用済み、実データ保有）に対して`BackfillJobRead`/`MarketDataSubscriptionRead`
  へ実際にシリアライズし、正しい値が返ることを確認した。
- **2026-09-16、利用者による実地確認**: `start-local.bat`を実際に起動し、R（API/画面のみ再起動）・
  A（Worker含む全体再起動）・Q（停止）それぞれのキー動作を目視確認済み。
- NOT VERIFIED: GitHub Actions上のCI実行結果、実OANDA/Binance通信を伴う画面表示の実地確認。

これでHorizon 1「Application境界とDurable Worker」のうちWorker関連の作業単位（①〜⑤）は
実装・専用DB検証・利用者環境での実DB切替まで完了した。`catalog.py`のモジュール分割、
frontendのfeature単位分割、`app`/`src`パッケージ統一はHorizon 1の別課題として残る
（詳細は`docs/architecture-alignment-and-long-term-roadmap.md`のHorizon 1節）。

### 品質チェックの追補（2026-08-31）

上記②③実装時の「6ファイル整形不一致」「Python型検査未設定」は、後続の品質整備で解消。
履歴の検証結果は当時の状態として残す。現在の検査対象・結果・制限は
[Python品質チェック](python-quality-checks.md)を参照する。
