# アーキテクチャ整合性調査と長期開発ロードマップ

- 最終確認日: 2026-09-15
- 対象: `AI_System_Trading`
- 計画期間: 約 12〜18 か月を見通す段階計画
- 現在地: 市場データ観測基盤の構築段階（実資金を扱わない）
- 安全境界: OANDA Practice / Binance Spot Testnet / 内部 Paper Trading のみ

## 1. この文書の目的

初期設計資料 `docs/concept/FXtrading_rebuild/` と、現在のアーキテクチャ、実装、既存計画、テスト、CI、DBマイグレーションを照合し、次を明確にする。

1. 初期構想のうち、現在も維持する設計原則
2. 現在の実装との乖離が、意図的な段階導入か、解消すべき設計負債か
3. 今後の開発順序、開始条件、完了条件
4. 将来機能を安全境界の外へ無意識に拡大しないための承認ゲート

本書は長期の実行順序を扱う。個別機能の詳細仕様と実装チェックリストは、各機能の専用計画を正とする。

## 2. 調査対象と判定上の注意

### 初期設計

- `docs/concept/FXtrading_rebuild/00_概要と文書一覧.md` から `10_技術選定と開発構成.md`
- 要件、ER図、API、モジュール境界、移行計画、AI、リスク初期値、着手前ゲートを含む

### 現行設計・計画

- `docs/architecture/current-and-target.md`
- `docs/plans/candle-chart-and-coverage.md`
- `docs/plans/candle-ingestion.md`
- `docs/plans/instrument-sync.md`
- 接続管理に関する修正計画

### 実装証拠

- FastAPIルート、SQLAlchemyモデル、Alembic 4リビジョン
- OANDA Practice / Binance Spot Testnetアダプター
- 接続検証Application Use Case
- 市場データ取得・ギャップ・カバレッジ・購読処理
- React画面、ローソク足チャート、段階追加読込
- backend / frontendテスト、GitHub Actions CI、release workflow

計画書のチェック状態だけでは実装済みと判定せず、コード、DB変更履歴、テストの存在を併せて確認した。反対に、コードが存在しても運用耐久性や安全性が不足するものは「暫定実装」とした。

## 3. 結論

### 3.1 全体方向は一致している

次の基本方針は初期設計と現在で整合している。

- Python 3.13、FastAPI、React/TypeScript、PostgreSQL、SQLAlchemy/Alembic
- Workspaceを分離単位とする設計
- モジュラーモノリスから開始し、長時間処理を独立Workerへ分離する方針
- 取引所資格情報を平文でDBやレスポンスへ出さない
- 金額・数量に浮動小数点を使わない
- OANDA PracticeとBinance Spot Testnetに限定する
- まず市場観測とデータ品質を構築し、Paper Tradingへ進む
- 実取引は自動的な後続フェーズに含めない

### 3.2 最大の乖離は「設計思想」ではなく「現在の到達範囲」である

初期資料のMVPは、認証、接続、市場データ、戦略、リスク、注文、ポジション、Bot、監査、通知、バックテストまでを広く含む。一方、現在の実装は接続管理、銘柄同期、市場データ蓄積、カバレッジ、ローソク足表示を中心とする「観測基盤」である。

これは失敗した乖離ではなく、安全性とデータ品質を優先して実装単位を小さくした結果である。ただし、初期資料の広いMVPを現状説明として読むと進捗を誤認するため、初期資料は「製品構想・設計候補」、本書と現行機能計画は「実行計画」として扱う。

### 3.3 解消が必要な構造上の乖離がある

現在の主要な暫定構造は次のとおり。

- 開発用Owner tokenのみで、OIDCとOwner/Operator/ViewerのRBACは未実装
- Secret Managerではなくローカル暗号化ファイルを使用
- APIルート、catalogモデル、市場データサービス、`frontend/src/App.tsx`への責務集中
- Event Log、Outbox、通知、再開可能なリアルタイム配信の未実装

これらは機能追加より先、または同時に段階解消する必要がある。

## 4. 現在の実装到達点

| 領域 | 状態 | 現在の証拠 | 判定 |
|---|---|---|---|
| Workspace | 基本APIあり | workspace作成・一覧・詳細 | 基礎実装済み |
| 接続管理 | 主要操作あり | 登録、検証、資格情報置換、無効化、削除、監査 | 実装済み、認証は暫定 |
| 外部口座 | 同期・選択あり | マスク表示、暗号化参照、選択状態 | 実装済み |
| 銘柄 | 対応銘柄同期あり | USD_JPY、BTCJPY | 初期範囲で実装済み |
| 履歴ローソク足 | 取得・保存あり | upsert、before cursor、BackfillJob | 実装済み |
| データ品質 | 基礎あり | IngestionReport、MarketDataGap、coverage、重複抑止 | 実装済み、境界試験が残る |
| ローソク足UI | 操作可能 | クロスヘア、パン、ズーム、追加読込、状態表示 | 実装済み |
| リアルタイム配信 | 未実装 | WebSocket/stream ticketなし | 未着手 |
| OIDC/RBAC | 未実装 | 開発用Owner token | 移行必須 |
| Durable Worker | 実装済み、運用DB未切替 | `app/market_data/worker/`、専用PostgreSQLで検証 | 運用DB適用が残る |
| 戦略・リスク・Bot | 未実装 | 対応モデル/APIなし | 後続 |
| Paper注文・約定・台帳 | 未実装 | 対応モデル/APIなし | 後続 |
| バックテスト | 未実装 | 対応モデル/APIなし | 後続 |
| Event/通知 | 未実装 | Outbox/Gmail通知なし | 後続 |
| AI Model Lab | 未実装 | 学習・registryなし | 任意の後期段階 |
| 実取引 | 対象外 | 外部注文を呼ばない安全境界 | 維持 |

## 5. 初期設計との差分と扱い

| 論点 | 初期設計 | 現在 | 分類 | 方針 |
|---|---|---|---|---|
| MVP範囲 | Paper Tradingまでを広く含む | 観測基盤に集中 | 意図的な段階化 | 現在の順序を維持する |
| 認証 | OIDC + RBAC | Dev Owner token | 暫定差分 | 外部配布・複数利用者より前に置換する |
| 秘密管理 | Deployment Secret Manager | ローカル暗号化Store | 環境別差分 | ローカルは維持、配布環境ではSecret Manager必須 |
| Worker | 独立Market Data/Bot/Notification Worker | Market Dataは独立プロセス化済み（運用DB未切替）、Bot/Notificationは未着手 | 部分解消 | Bot/Notification Workerは各機能着手時に追加する |
| ジョブ基盤 | Queue/Workerを想定 | PostgreSQLジョブとプロセス内処理 | 適応的変更 | まずPostgreSQL lease方式。負荷根拠が出るまでRedis等を増やさない |
| 市場データ | WebSocket主体、REST補完 | REST履歴・polling主体 | 順序変更 | 履歴品質を閉じてからrealtimeへ進む |
| イベント配信 | 汎用 `/api/v1/stream` | 未実装 | 未到達 | 短命one-time ticketと再開可能sequenceを採用候補とする |
| ドメイン分割 | 10前後の境界を定義 | catalog/serviceへの集中 | 設計負債 | Use Case単位で段階分割する |
| Paper Trading | 注文・約定・台帳・Botを定義 | 未実装 | 未到達 | 観測基盤とWorker完成後に着手する |
| リスク初期値 | conservative-v1候補値 | 未承認・未実装 | 承認待ち | 実装前に値、単位、停止規則を承認する |
| AI/外部モデル | Model Labと外部探索を設計 | 未実装 | 意図的延期 | 非AI baselineと再現可能なBacktestの後に限定する |
| 実取引 | 後期に承認検討 | 対象外 | 安全上の固定境界 | 本ロードマップから除外する |
| フロント構造 | 機能別画面を想定 | Router/component分割中、App集中が残る | 移行途中 | 機能追加に合わせてfeature単位へ分割する |
| パッケージ構造 | 単一責務を想定 | `app`と`src`が併存 | 設計負債 | import/build影響を試験して一本化する |

## 6. 長期アーキテクチャ方針

### 6.1 採用を継続する方針

- モジュラーモノリスを基本とする。
- API、Application、Domain、Infrastructureの依存方向を守る。
- 外部SDK、DB、暗号化Store、通知プロバイダーをInfrastructure境界に閉じ込める。
- Web APIと長時間Workerは同じApplication Use Caseを呼び出す。
- Workspace境界をDB query、API、監査、ジョブの全経路で強制する。
- 変更可能な戦略・リスク設定はversioned immutable recordとして扱う。
- 注文と資金移動を模擬する段階でも、append-onlyの台帳と監査証跡を優先する。

### 6.2 当面採用しないもの

- 根拠のないマイクロサービス化
- 初期からのRedis/Kafka導入
- 外部取引所への注文送信
- Public BinanceデータとTestnetデータの無表示混在
- AIモデルを直接売買判断へ接続すること
- 承認されていないリスク初期値の固定実装

## 7. 長期ロードマップ

期間は少人数開発を前提とする目安であり、完了条件を満たさないまま日付だけで次へ進めない。各期間は一部並行可能だが、安全性とデータ整合性のゲートを優先する。

### Horizon 0: 観測基盤の基準線を閉じる（0〜1か月）

状態: `[~]` コードと自動試験は完了。OANDA Practiceの認証済み実データによる価格・時刻表示確認のみ `NOT VERIFIED`。

#### 開始条件

- 現行DBがAlembic headまで適用可能
- OANDA PracticeまたはBinance Spot Testnetの読取専用検証環境がある
- 既存テストが再現可能

#### 実装・整備

- `candle-chart-and-coverage.md` のPhase A/B残件を完了する
- requested rangeを明示できるcoverage APIを確定する
- complete、source-limited、empty、duplicate、internal gap、Workspace分離の試験を追加する
- OANDAの価格桁・時刻・週末閉場境界を実データで検証する
- ローソク足のprice scale、初期追加読込抑止、連続追加読込の回帰試験を固定する
- 古い計画文中の「最新500件のみ」など、実装と食い違う記述を更新する
- OpenAPI snapshot、DB migration確認、依存関係監査をCIの基準線に追加するか判断する

#### 2026-08-29 実装結果

- coverage APIにtimezone-awareな`requested_from` / `requested_to`を追加し、両方未指定時の最新backfill参照を維持した
- complete、source-limited、empty、duplicate job、internal gap、Workspace分離をservice/APIテストで固定した
- OANDA midpointの小数精度と、New Yorkの夏時間・冬時間を考慮した週末閉場境界を自動試験で固定した
- チャートの初期追加読込抑止、重複なしのページ結合、価格桁、状態表示は既存component/browser回帰試験で確認対象となっている
- OpenAPI snapshotは現時点では導入せず、FastAPIのschema生成とAPI回帰試験を基準線とする。専用snapshotはHorizon 1のルート分割時に再判断する
- OANDA Practiceの認証済み実データ表示確認は、外部接続環境が必要なため `NOT VERIFIED` として残す

#### 完了条件

- 対応する全timeframeで価格、時刻、並び順、重複、ギャップの期待値が自動試験される
- 初期表示だけで履歴追加が走らず、利用者操作後のみ追加読込される
- ソース制限と内部欠損がAPIと画面で区別される
- 現行機能・既知制限・次工程が文書と一致する

### Horizon 1: Application境界とDurable Worker（1〜3か月）

状態: `[~]` Application境界抽出と独立Worker（①〜⑤）を実装し、利用者環境の運用DBへ
切替済み。詳細は `market-data-application-boundary.md` と `durable-market-data-worker.md`。
`app`/`src`統一・`catalog.py`のモデル/スキーマ/ルート分割は完了（2026-09-16）。
frontend（`App.tsx`）の機能別分割が残るためHorizon 1全体は引き続き `[~]`。

2026-08-31: ①Worker詳細設計・DB変更計画を作成。
`durable-market-data-worker.md` と、そこから参照する `docs/design/` の2文書を正とする。
続いて②の追加Alembic/Worker専用ORMとlease取得・更新・失効処理を実装し、専用PostgreSQLで試験した。
③区間実行Applicationも追加し、区間保存・利用資格再確認・中断後の再開を専用DBで検証。

2026-09-15: ④独立プロセスと切替を実装。候補探索、公平な巡回、heartbeat、signals、
API内実行（lifespanポーラー、BackgroundTasks即時実行）の除去、旧jobの正規化を実装し、
専用PostgreSQLで検証した。実装中に`ensure_no_overlapping_backfill`の既存バグ
（leaseで稼働中のjobを5分後に誤ってfailed化する）を発見・修正した。同日中に利用者環境の
運用DBへ`20260831_0005`を適用し、Workerの実際の起動・稼働を確認した。

2026-09-16: ⑤起動/表示/実地試験を実装。`scripts/start_local.py`をAPI/画面（`R`個別再起動）と
Worker（`A`全体再起動、45秒停止猶予）の2グループに分離し、`next_run_at`/`consecutive_failures`/
`blocked_reason`をAPI・画面に追加した。実装の動機は、運用DB切替直後に利用者が実際に
「取得の進捗が画面から分からない」状況に遭遇したこと。詳細は`durable-market-data-worker.md`の
「⑤の実装と検証範囲」。同日、利用者が`start-local.bat`でR/A/Qキー操作を実地確認済み。

2026-09-16: Horizon 1の残る設計負債のうち、`app`/`src`パッケージ統一（`src/ai_system_trading`は
未使用の空パッケージだったため削除）と`catalog.py`の分割を実施。モデル(`app/models/`)を
`workspace.py`/`audit.py`/`connections.py`/`instruments.py`/`market_data.py`へ、
スキーマ(`app/schemas/`)を`base.py`/`workspace.py`/`connections.py`/`instruments.py`/
`market_data.py`へ、ルート(`app/api/routes/catalog.py`)を`workspaces.py`/`connections.py`へ
分割した。API契約・DBスキーマは無変更。非DBテスト153件・専用PostgreSQLでのWorker試験89件が
分割後も成功。frontendの機能別分割のみHorizon 1の残件として残す。

2026-08-30の利用者判断: OANDA APIキーを生成できないため、Horizon 0のOANDA実データ確認は
延期（NOT VERIFIED）。Binance取得は利用者から成功報告あり。この残件を明示して、
Application抽出を先行する。OANDA確認やHorizon 1全体を完了扱いにはしない。

#### 開始条件

- Horizon 0のデータ品質試験が安定している
- BackfillJobとSubscriptionの状態遷移が文書化されている

#### 実装・整備

- `[x]` backfill受付、coverage範囲解決、subscription変更をApplication Use Caseへ切り出す。
  取得実行・coverageの低水準計算は既存serviceへ委譲し、Worker移行時の共通入口整備は次単位で行う。
- `[x]` `catalog.py` をWorkspace、Connection、Instrument、Market Data単位へ段階分割する
  （モデル・スキーマ・ルートの3つとも分割済み。API契約は無変更）
- `[x]` Web lifespan内Workerを独立プロセスへ移す（`app/market_data/worker/`、専用PostgreSQLと
  利用者環境の運用DBで検証済み。`start-local.bat`のR/A/Qキー操作も利用者が実地確認済み）
- `[x]` PostgreSQL lease、heartbeat、retry、stale recovery、graceful shutdownを実装する
  （Worker停止猶予45秒を`scripts/start_local.py`で個別化済み）
- `[x]` 同一Workspace・銘柄・timeframeの多重処理をDB境界で抑止する（②のfeed lease機構）
- `[x]` APIプロセス再起動時にもジョブの状態と再開判断が失われないようにする
  （Worker・APIが完全に独立プロセス化されたことで達成。専用PostgreSQLと運用DBで検証済み）
- frontendのAPI client、feature state、画面componentを機能単位へ分割する
- `[x]` `app`と`src/ai_system_trading`の役割を確定し、単一runtime packageへ移行する
  （`src/ai_system_trading`は未使用の空パッケージだったため削除。`app`が唯一のパッケージ）

#### 完了条件

- APIを停止してもWorkerの所有権とジョブ状態が曖昧にならない
- Workerの二重起動、異常終了、再起動を含む統合試験が通る
- HTTPルートから外部SDKや複雑なDB処理を直接呼ばない
- import path、migration、package build、local startupが一本化後も通る

### Horizon 2: リアルタイム観測と運用可視性（3〜5か月）

状態: `[~]` 開始条件は充足済み。着手済み。詳細計画・受入試験は
`docs/plans/realtime-market-data-stream.md`、module設計は
`docs/design/modules/realtime-market-data-stream.md` を正とする。

2026-09-17: ①設計を作成。取引所へのストリーム接続（OANDA `PricingStream` / Binance
`kline_socket`）はAPIプロセス内でasyncio background taskとして保持し、ブラウザ向けWebSocket
終端と同一プロセス内のin-memory pub-subで完結させる方針を利用者が承認した（Worker↔API間の
新しいIPCやRedis/Kafka等の新規infraは導入しない）。実装（②以降）は進行中
（2026-09-17時点で②③④⑤⑥⑦が完了。耐障害性・運用可視性の試験⑧は実環境接続が前提のため
未着手。詳細な進捗は`docs/plans/realtime-market-data-stream.md`のstatus logを正とする）。

2026-09-26: 銘柄ルール同期成功時に、過去1年分のbackfillと全7時間足の自動取得を
自動的に開始するよう変更した(利用者判断: 認証・同期さえ済んでいれば手動でボタンを
押さなくてもデータ取得が始まるようにしたい、という要望)。従来は「銘柄ルールを同期」
→「過去1年を取得」→「この銘柄の全時間足を開始」の3手動操作が必要だったが、
「過去1年を取得」ボタンをフロントエンドから廃止し、`app/api/routes/instruments.py`
の`sync_workspace_instruments`が成功した時点で`_auto_start_collection`
(新規)が両方を自動的に起動するようにした。バックエンドの
`enqueue_backfill`は`trigger_type`引数を新設し(`backfill_job.trigger_type`の
CHECK制約は元々`'automatic'`/`'manual'`を許容していたが、これまで`'manual'`しか
使われていなかった)、自動起動時は`"automatic"`を記録する。「この銘柄の全時間足を
開始/停止」ボタン自体は残し、利用者が後から止める/再開する操作は引き続き手動。

この変更は、利用者が開発優先順位を戦略検証優先に見直した際の調査
(BTCJPYには実データが約6週間分蓄積済みだがUSD_JPYは実データ皆無であることが判明)
を受けて着手した。OANDA側の実データ取得自体は本変更の対象外で、引き続き未解決。

#### 開始条件

- Durable Workerが安定稼働する
- 履歴RESTによるギャップ補完が信頼できる

#### 実装・整備

- OANDA Practice / Binance Spot Testnetのstream adapterを追加する
- Owner tokenをURLへ渡さない短命one-time stream ticketを設計する
- `event_id`、Workspace、instrument、timeframe、event type、sequenceを持つ配信形式を確定する
- 再接続後はlast sequenceから再開し、不足分をREST履歴で補完する
- 接続状態、遅延、直近データ時刻、gap件数、Worker heartbeatを運用画面に表示する
- backpressure、切断、順不同、重複、clock skewを試験する

#### 完了条件

- 切断と再接続でローソク足の重複・欠損を残さない
- 画面更新とDB確定値が一定時間内に収束する
- 24時間以上のsoak testでメモリ増大、再接続ループ、ジョブ滞留が許容範囲内
  （実施時期は`decisions/0001-defer-realtime-stream-soak-test.md`を参照）
- stream ticketが短命・一回限りで、ログやURLに長期資格情報を残さない

### Horizon 3: Paper Tradingコア（5〜8か月）

状態: `[~]` 実装・整備の主要項目（PaperAccount等のORM、注文受付/risk check/約定/取消の
Application Use Case、StrategyVersion/RiskProfileVersion/Signal/RiskDecisionの監査可能な
保存、Botのstart/pause/resume/stop、halt理由）はコードとして既に実装済み
（`app/trading/application/order_flow.py`・`risk_gate.py`・`trading_halt.py`・
`bot_lifecycle.py`等）だが、本節にはこれまで状態行が無かった（本ロードマップ更新漏れ）。
HTTPからの呼び出し経路（Bot管理API）は2026-09-25まで存在せず、これらのUse Caseは
Pythonから直接呼ぶ以外に到達手段が無かった。

2026-09-25: 上記の既存Application層を呼び出すBot管理API
（`app/api/routes/trading.py`、`app/schemas/trading.py`）を追加した。
`POST /workspaces/{id}/trading-accounts`（paper口座作成）・
`POST .../trading-accounts/{id}/deposits`（`account_funding.seed_paper_deposit`の呼び出し）・
`POST /workspaces/{id}/bots`（Bot作成、`start`しない）・
`POST .../bots/{id}/{start,pause,resume,stop}`（`bot_lifecycle.py`の4コマンドをそのまま
公開）を実装した。`app/trading/application/dummy_pipeline.py`の`ensure_dummy_bot`は
従来`bot_lifecycle.start_bot`まで内部で呼んでいた（呼び出し元ゼロ・試験ゼロの
フィクスチャヘルパーだったため気づかれていなかった）が、これをHTTPの`POST .../bots`
から直接呼ぶにあたり「作成」と「起動」を分離し、`bot_lifecycle.py`の4コマンドが
既に独立している設計と揃えた。`docs/concept/FXtrading_rebuild/04_API再設計.md`は
`POST /bots/{id}/commands`という単一エンドポイント案を示しているが、同ドキュメントは
`AGENTS.md`のディレクトリ規約上「現在のスコープではない」候補設計であるため、本APIは
代わりにこのコードベースの既存規約（`trading_halts.py`の動詞ごとの個別エンドポイント）に
揃えた。まだ未実装: 実行ループ/Worker（`run_dummy_pipeline_once`を定期実行する経路が無い）、
最低限のUI。次のUnitで着手予定（利用者指示「Bot管理API→実行ループ→最低限のUI」の順）。

2026-09-25: 上記の実行ループ/Workerを実装した。`app/trading/worker/`
（`python -m app.trading.worker`で起動）は、`actual_state`が`running`/`paused`の
全Botを一定間隔（既定5秒、`BOT_EXECUTION_POLL_INTERVAL_SECONDS`）でポーリングし、
`app/trading/application/bot_execution_loop.py`の`run_active_bots_once`経由で
`dummy_pipeline.run_dummy_pipeline_once`を呼び出す。Notification Worker
（Horizon5 Group D）と同じ単純ポーリング形状を採用し、市場データWorkerのlease/
heartbeat機構は使っていない（1回の評価が短いDBアクセスのみで外部ネットワークI/Oを
伴わないため、長時間実行ジョブ向けのstale-recoveryが不要という理由も同じ）。

実装前の調査で、`run_dummy_pipeline_once`自体に潜在バグを発見し修正した:
この関数は呼び出し元ゼロ・試験ゼロのまま実装されており、同じ最新確定バーに対して
2回呼ばれると2回目の`db.commit()`が`uq_signal_idempotency`制約違反の未捕捉
`IntegrityError`で失敗する状態だった。Workerはポーリング間隔と実際の新規バー到着
間隔が一致する保証が無いため、この経路は確実に踏まれる。`(bot_run_id, candle_id)`
単位で既存Signalを確認し、有れば`{"action": "already_processed"}`を返して早期
returnする冪等性ガードを追加して解消した（Worker側で per-bot 状態を追跡する必要が
無くなる、より単純な設計）。

Bot管理API（`POST .../bots/{id}/start`等）と合わせて、Botをstartすると実際に
シグナル評価・注文が進行する状態になった。次のUnit: 最低限のUI（利用者指示の順序
どおり）。

2026-09-26: 上記の最低限のUIを実装した。着手前に利用者とデザインを協議し
（更新方式は自動ポーリング、実行状況はdesired/actual stateに加え直近のBotRun/
シグナル情報も表示、との決定）、決定に沿ってバックエンドへ
`GET .../bots/{id}/latest-run`（直近BotRunと直近Signalを1回の呼び出しで返す、
`app/schemas/trading.py`の`BotRunSummaryRead`）を追加してから着手した。
フロントエンドは既存の`features/connections/`と同じ構成規約
（`useXxx`フック + Panelコンポーネント + Pageラッパー、App.tsxへ集約配線する
既存の(やや集中的な)構造）に揃え、`features/trading/`（`useTrading.ts`、
`TradingPanel.tsx`）・`pages/TradingPage.tsx`・ルート`/workspaces/:id/trading`・
AppShellへの「Bot管理」ナビリンクを追加した。取引口座の作成・入金、Botの作成・
start/pause/resume/stop、状態バッジと直近シグナル表示を5秒間隔でポーリングする
（`useMarketData.ts`と同じ形状）。ロール別のボタン出し分けは既存UIが一切行って
いない規約に合わせて実装せず、APIの403に委ねている。

検証: `ruff`/`mypy`/`pytest`（510件）、フロントエンド`eslint`/`tsc --noEmit`/
`vitest`（33件）/本番buildは全て成功。バックエンドとフロントエンドを実際に起動し、
新規10エンドポイントが`/openapi.json`に登録されていること、`/health/db`が
`postgresql`で成功すること、フロントエンドが未認証画面までコンソールエラー無く
到達することを確認した。

2026-09-26: 上記で未検証のまま残した「実際のログイン後の画面操作」を、利用者の
依頼を受けて追加検証した。`scripts/mock_oidc_server.py`（新規、Authorization
Code + PKCE + Discoveryを実装するローカル専用の簡易OIDCプロバイダ、外部IdP
アカウント不要）を作成し、`.env`にOIDC/セッション署名設定を追加、ローカルDBが
未適用だったmigration`20260921_0008`（`fx.session_revocation`追加、追加のみで
データ損失なし）を適用した上で、実際にバックエンド・フロントエンドを起動して
ブラウザから確認した。ログイン→Workspace作成→取引所接続登録→取引口座作成→
入金→Bot作成→start/pause/resume/stop（4遷移すべて）→
`run_active_bots_once`を1回手動実行してSignal生成→5秒ポーリングで
「直近シグナル: hold (時刻)」が画面に反映されるまでを一通り確認した。
未検証のまま接続を検証しようとした際の422エラー（`Exchange connection is
missing...`）がエラーメッセージ欄に正しく表示されることも確認した。コンソール
エラーは意図した4xx/一部無関係なmarket-data機能の409のみで、JS例外・React
crashは無し。今回作成したWorkspace「Local Test Workspace」・Bot・Paper口座・
接続・USD_JPY銘柄等のテストデータは、継続検証に使えるよう利用者判断でDBに
残している。`scripts/mock_oidc_server.py`も同様に利用者判断でリポジトリに残し、
README「ローカルでの確認」節に開発者向け手順として追記した。

#### 開始条件

- 観測基盤が安定し、履歴とリアルタイムの整合性が確認済み
- Paper Tradingの詳細仕様、状態遷移、手数料・スリッページ規則が承認済み
- conservative-v1を含むリスク値は「候補」から承認済みversionへ移されている

#### 実装・整備

- PaperAccount、OrderIntent、PaperOrder、PaperFill、Position、LedgerEntryを追加する
- 注文受付、risk check、約定、取消、期限切れをApplication Use Caseで実装する
- idempotency keyと状態遷移の一意制約を設ける
- StrategyVersion、RiskProfileVersion、Signal、RiskDecisionを監査可能な形で保存する
- 最初の戦略は単純で説明可能なnon-AI baselineに限定する
- Botのstart/pause/resume/stopとhalt理由を実装する
- 外部注文adapterは作らず、Paper Executionだけを依存先とする

#### 完了条件

- 同じOrderIntentを再送しても重複注文・重複台帳が生じない
- 約定・ポジション・残高・損益がappend-only ledgerから再構成できる
- stale data、日次損失、drawdown、連敗、手動停止で新規注文を拒否できる
- すべての売買判断にstrategy/risk/data versionと理由が残る
- Workspaceを跨いだ閲覧・操作・約定が不可能である

### Horizon 4: 再現可能なBacktestと戦略ガバナンス（8〜11か月）

Horizon 6着手前の縮小スコープ(Horizon4-lite)としての先行着手方針は
`decisions/0003-horizon4-lite-backtest-before-chronos.md`を参照。

状態: `[~]` `docs/plans/horizon4-lite-backtest.md`のUnit 1〜6(DatasetSnapshot/
BacktestRun/BacktestTradeのORM、リプレイハーネス、約定シミュレーション、評価指標、
walk-forward分割、look-ahead bias検知テスト)はコードとして実装・試験済みだったが、
本節にはこれまで状態行が無かった(Horizon 3と同じ、本ロードマップ更新漏れ)。HTTPから
到達する経路も無く、Pythonから直接呼ぶ以外に利用できなかった。

2026-09-26: 上記の既存Application層を呼び出すBacktest API
(`app/api/routes/backtests.py`、`app/schemas/backtests.py`)を追加した。
`POST /workspaces/{id}/backtests`(同期実行、`mode: "single"|"walk_forward"`)・
`GET /workspaces/{id}/backtests`(履歴一覧)・
`GET /workspaces/{id}/backtests/{id}/trades`(取引一覧)を実装し、最低限のUI
(`features/backtests/`、ルート`/workspaces/:id/backtests`)も合わせて追加した。
Units 1〜6のうち`dataset_snapshotの作成`(対象期間のcandle件数・欠損チェック・
checksum算出)だけは実装が存在しておらず、新規`app/trading/application
/backtest_provisioning.py`の`ensure_dataset_snapshot`で埋めた。StrategyVersionは
Bot管理APIと同じ理由(戦略実装が`dummy_signal.py`の1つのみ)でAPIから選択不可とし、
`dummy_pipeline.ensure_dummy_bot`と共有する`ensure_dummy_strategy_and_risk_profile`
(既存コードからの抽出、挙動不変)を再利用した。着手前に利用者へスコープを確認し、
ADR 0003が先送りにしたStrategyVersionのDraft/Validated/Approved/Retired承認
ワークフローは対象外(Bot管理API・実行ループ・最低限のUIのみ)と決定した。

実行はこのリポジトリの他のパイプライン(order_flow、dummy_pipeline)と同じく
POSTリクエスト内で同期的に行われる(ジョブキューは無い)。大きな期間を指定すると
応答が遅くなりうるが、進捗報告の仕組みは無い(意図した制約)。

検証: `ruff`/`mypy`/`pytest`(526件)、フロントエンド`eslint`/`tsc --noEmit`/
`vitest`(33件)/本番buildは全て成功。バックエンドとフロントエンドを実際に起動し、
既存のテスト用Workspace(OIDC検証時に作成したもの)でsingle/walk-forward両方の
バックテストを実際に実行し、一覧・取引一覧の表示・成功バッジ表示までブラウザで
確認した。

#### 開始条件

- Paper Tradingの注文・約定・台帳モデルが安定している
- 過去データのcoverageとdataset versionを固定できる

#### 実装・整備

- BacktestRun、dataset snapshot、parameter snapshot、result artifactを実装する
- Paper Tradingと同じStrategy/Risk判定コードを再利用する
- 手数料、spread、slippage、欠損、market hoursを再現する
- walk-forward、out-of-sample、benchmark比較を標準評価にする
- StrategyVersionのDraft/Validated/Approved/Retiredと承認履歴を実装する
- Paper実績とBacktest結果の乖離を比較する

#### 完了条件

- 同じ入力versionとseedから同じ結果を再現できる
- look-ahead bias、データ漏洩、欠損無視を検出する試験がある
- 承認されていないStrategy/Risk versionをBotへ割り当てられない
- 結果から使用データ、コードversion、パラメータ、費用モデルを追跡できる

### Horizon 5: 認証・監査・配布運用の完成（並行着手、9〜13か月）

状態: `[~]` グループA（Unit 1/3/4、認証基盤とRBAC）・グループD（Unit 8、Outbox/Notification
基盤、スケルトン）・グループE（Unit 9、リリースゲート強化）を実装済み。詳細仕様・実装順序は
`plans/horizon5-implementation-plan.md`を正とする。グループB・Cは未着手。

2026-09-21: OIDC Authorization Code + PKCE（外部IdP接続のみ。自己完結型IdPは
`plans/horizon5-implementation-plan.md` §0.3のスコープ外注記のとおり対象外）による
ログイン、セッションJWT（`app/security/session.py`）、`user_membership`を用いた
Owner/Operator/ViewerのRBACを全26APIエンドポイントへ適用した
（System Workerロールは未実装。本節末尾の完了条件注記のとおりHorizon5の完了条件には
含まれない）。`DEV_OWNER_TOKEN`固定トークン機構（`app/security/auth.py`）は削除し、
完全にOIDCセッションへ置き換えた。既存frontend（`frontend/src/features/`配下）は
旧`X-Owner-Token`ヘッダー方式のままで、この変更により実行時に認証が通らなくなる
（TypeScriptビルド自体は成功するため`npm run build`では検出できない）。frontend側の
OIDCログイン対応は本グループのUnitに含まれておらず、別タスクとして扱う必要がある。

2026-09-24: グループD（Unit 8、Outbox/Notification基盤）を実装した。
`OutboxEvent`/`Notification` ORM（`app/models/audit.py`/`app/models/notifications.py`、
既存DBスキーマへのマッピングのみで新規マイグレーション不要）、汎用SMTPアダプタ
（`app/notifications/adapters/smtp.py`、標準ライブラリのみ）、Outbox配信ループ
（`app/notifications/application/deliver_notifications.py`、`FOR UPDATE SKIP LOCKED`で
複数Worker安全）、単純ポーリング方式のNotification Worker
（`app/notifications/worker/`、`python -m app.notifications.worker`で起動、市場データ
Workerのlease機構は意図的に不使用）を実装した。計画の例示コードにあった不整合
（`Notification.event_id`に`outbox_event.id`を設定していたが実際は`system_event.id`への
外部キーで、両テーブル間に関係が無く外部キー違反になる設計上の誤り）は、両テーブル共通の
`correlation_id`で`SystemEvent`を解決する方式に変更して修正した。**本Unitはスケルトンで
あり、ドメインイベント発行元は1つも配線していない**（計画書Unit 8「想定リスク」節どおり）
ため、ロードマップの完了条件「通知の重複、欠落、再送をOutboxから追跡できる」は
引き続き**未達**。`scripts/start_local.py`へのWorker自動起動追加は見送った（SMTP未設定が
既定のため、追加すると通常のローカル起動のたびに`[WARN] Workerプロセスが終了しました`が
出て紛らわしくなるため）。

2026-09-24: グループE（Unit 9、リリースゲート強化）を実装した。`ci.yml`の`backend`
ジョブへ`pip-audit`（依存関係の既知脆弱性スキャン、PRごとに実行）、`release.yml`へ
`anchore/sbom-action`によるSPDX形式SBOM生成とGitHub Releaseへの添付を追加した。
計画の例示コードは`anchore/sbom-action@v0`（移動タグ）を使っていたが、実際にGitHub API
で確認したところ`v0`タグは2026年3月時点のコミットを指しており、直近の安定版
`v0.24.2`（2026年8月）から約5か月遅れていた。計画自身が「導入前に最新の固定タグを
確認すること」と明記していたため、`v0.24.2`へ明示的にピン留めした。**`pip-audit`導入時点で
既存依存`cryptography==46.0.7`に既知の脆弱性4件(PYSEC-2026-3552/3553/3554,
GHSA-537c-gmf6-5ccf。証明書チェーン検証・PKCS7復号・静的リンクOpenSSLに関するもの)が
検出され、CIの`backend`ジョブが失敗するようになった。これは計画書Unit 9「想定リスク」節が
明示的に許容している意図した挙動であり、本Unit自体のスコープには含めず、別タスクとしての
依存関係更新が必要**（本コードベースでの`cryptography`利用はSecret Store(Fernet対称暗号)
のみで、検出された脆弱性は主にX.509証明書検証・PKCS7復号に関するものだが、修正には
`>=48.0.1`への更新(現行ピン`>=46,<47`の範囲外)が必要）。

2026-09-25: 上記`cryptography`の脆弱性に対応した。`pyproject.toml`/`requirements.txt`の
ピンを`>=50.0.1,<51`へ更新した(4件のうちPYSEC-2026-3552の修正版が50.0.0のため、
`>=48.0.1`ではなく`>=50.0.1`が必要な下限)。`PyJWT[crypto]`(`cryptography>=3.4.0`)・
`psycopg`・`pwdlib[argon2]`はいずれも`cryptography`に独自の上限制約を持たないことを
確認済み。`python -m pip_audit`で脆弱性が解消したことを確認し、`ruff`/`mypy`/`pytest`
(479件)全て成功。Fernet(`app/services/secrets.py`)のみを使う狭い利用範囲のため、
API互換性への影響は無かった。

開始条件の充足状況、配布モデルの決定（セルフホスト型ソフトウェア販売への一本化、
マルチテナントSaaS仲介モデルは取引所ToS上のリスクにより不採用）、および
本節の実装・整備項目の再構成は`decisions/0005-horizon5-self-hosted-distribution.md`を、
実装着手できる粒度の詳細仕様は`plans/horizon5-distribution-and-auth.md`（ドラフト、
Horizon4-lite完了後に本格着手）を参照。

#### 開始条件

- ~~配布対象、利用者、運用責任、データ・SDK・取引所規約の確認範囲が決まっている~~
  → ADR 0005により充足（配布対象=クローズドβ、運用責任=利用者本人単独、取引所ToSは
  一次調査済み・残存リスクはADR 0005「残存リスク」節に記録）
- ~~ローカル単独利用を超える必要性が承認されている~~ → 承認済み（第三者への
  ソフトウェア販売、ADR 0005）

#### 実装・整備

ADR 0005によりroadmap原文の前提（運営者が本番環境を運用する）が変わったため、
以下は`plans/horizon5-distribution-and-auth.md`の内容で読み替える。

- `[~]` OIDC Authorization Code + PKCEへ移行する（自己完結型IdPと外部IdP接続の両対応）
  （外部IdP接続のみ実装済み。自己完結型IdPは意図的にスコープ外、
  `plans/horizon5-implementation-plan.md` §0.3参照）
- `[~]` Owner/Operator/Viewer/System WorkerのRBACを全APIへ適用する（`user_membership`は
  DBスキーマに既存、ORM未実装。System Workerは別テーブルのサービスアカウントとして新設）
  （Owner/Operator/Viewerは`app/security/rbac.py`で実装し全26エンドポイントへ適用済み。
  `user_membership`のORM化も完了。System Workerロールは未実装のまま）
- ~~本番・検証・開発環境を分離する~~ → 運営者自身のリリースエンジニアリング
  （ビルド・配布パイプライン）の話に限定。顧客の環境分離は顧客の運用判断とする
- ~~配布環境ではSecret Manager/KMSを使用し、rotation/revocationを運用化する~~ →
  Secret管理をプラガブル設計にし、顧客が自分の環境のSecret Manager/KMSを選べるようにする
- `[~]` Event Log、Outbox、Notification Worker、通知設定を実装する（通知は汎用SMTPを
  デフォルトとし、Gmail API等は任意アダプタとして後続タスクに回す）
  （Outbox/Notification ORM・汎用SMTPアダプタ・配信ループ・Workerのスケルトンは実装済み。
  ドメインイベント発行元(Event Log相当)は未配線で、完了条件は未達のまま）
- ~~backup/restore、migration rollback方針、障害対応手順、SLOを整備する~~ →
  運営者によるSLO保証ではなく、顧客向けの手順書提供に変更する
- `[x]` dependency、SBOM、secret scan、脆弱性対応をrelease gateへ組み込む
  （secret scanは既存のgitleaks(secret-scanジョブ)。`pip-audit`をbackendジョブへ、
  SBOM生成(anchore/sbom-action)をrelease.ymlへ追加済み。導入時に検出された既存依存
  `cryptography`の脆弱性4件は2026-09-25の別コミットで解消済み、上記参照）
- （新規）ライセンスキー機構（オフライン検証、署名済みライセンスファイル）を実装する

#### 完了条件

- Dev Owner tokenを無効化できる
- 権限境界、CSRF/CORS、session、secret rotation、監査ログのsecurity testが通る
- ~~DB/secret/configの復旧訓練が成功する~~ → 顧客向け復旧手順書の内容で実際に
  復旧できることを検証する（運営者側の復旧訓練ではない）
- 通知の重複、欠落、再送をOutboxから追跡できる
- 配布物と利用するデータ・SDK・モデルの権利確認記録がある（ADR 0005の一次調査済み。
  公開ベータ・有償販売開始前に残存リスクの最終確認が必要）

### Horizon 6: AI Model Lab（任意、12〜18か月以降）

開始条件のうち「Backtest」実績の充足方法は`decisions/0003-horizon4-lite-backtest-before-chronos.md`
（フルHorizon4ではなくHorizon4-liteの完了をもって充足とする）を参照。

#### 開始条件

- non-AI baseline、Backtest、Paper実績の比較基準がある
- AI導入で改善したい評価指標と許容リスクが明文化されている
- 学習データの権利、保存、再現性、計算資源の方針が承認済み

#### 実装・整備

- DatasetVersion、FeatureSetVersion、TrainingRun、ModelArtifact、EvaluationRunを追加する
- 学習・評価・昇格・rollbackを売買実行から分離する
- artifact checksum、provenance、quarantine、sandboxを設ける
- 外部モデルはrevision固定、`safetensors`優先、remote code無効を原則とする
- AI出力を直接注文せず、Signalから既存Risk Use Caseを必ず通す

#### 完了条件

- baselineを上回る効果をout-of-sampleとPaper期間の双方で説明できる
- 学習データ、feature、code、seed、artifactの系譜を再現できる
- model failureやdrift時に安全にbaselineへ戻せる
- 外部artifactを信頼境界の内側へ入れる審査記録がある

### 実取引について

実取引はHorizon 7ではない。本ロードマップの完了によって自動的に許可されない。将来検討する場合は、法務・規約・資金管理・権限分離・緊急停止・少額段階導入・外部監査を含む別プロジェクトとして、利用者の明示承認から開始する。

## 8. 横断的な品質目標

### データ正確性

- 金額、価格、数量はDecimal/NUMERICで扱う
- timestampはUTC保存、画面表示timezoneを明示する
- candleの一意性、source、is_closed、qualityを保持する
- 内部欠損とsource limitationを別状態にする

### セキュリティ

- 秘密値、口座原文、tokenをレスポンス・監査・例外・テストartifactへ出さない
- Workspace所有権をすべてのread/write/job/streamで検証する
- 外部作用を伴う操作はidempotencyと監査を持つ
- Practice/Testnet以外をadapter設定時と実行時の両方で拒否する

### 信頼性

- Workerはlease、heartbeat、retry、stale recoveryを持つ
- event/outboxはat-least-onceを前提にconsumerを冪等化する
- migrationはupgrade pathと既存データ保持を試験する
- 長時間稼働、再起動、接続断、API rate limitを継続試験する

### 開発品質

- backend lint/format/typecheck/test、frontend lint/build/testをCIで維持する
- 各フェーズでAPI契約、DB状態遷移、ブラウザ操作の回帰試験を追加する
- 設計文書の完了マークは、実装と検証証拠が揃った時だけ更新する
- 新しいインフラは、測定された制約を解く場合にのみ追加する

## 9. 承認ゲート

次は実装担当の判断だけで進めない。

| ゲート | 必要な承認・証拠 |
|---|---|
| Binance Public履歴の併用 | Testnetとの差、画面表示、利用規約、source識別 |
| conservative-v1の確定 | 各値、単位、計算窓、停止・解除規則 |
| OIDC provider選定 | 配布対象、運用責任、callback/session設計 |
| Deployment Secret Manager | 配備先、KMS、rotation、復旧手順 |
| Gmail等の通知 | OAuth scope、送信元、PII、retry/重複方針 |
| 外部AIモデル | license、revision、artifact形式、sandbox、評価基準 |
| 実取引 | 本ロードマップ外の独立承認と安全審査 |

## 10. 直近の推奨実装単位

長期計画の最初の3単位は次の順序を推奨する。

1. **市場データ基準線の完了**（`[~]` OANDA表示確認は利用者判断で延期）
   コードと自動試験は完了。OANDA実データ確認はNOT VERIFIEDとして残し、承認済みの構造改善を先行する。

2. **市場データApplication Use Caseの切り出し**（最初の単位を実装）
   backfill受付、coverage範囲解決、subscription変更を抽出。検証結果と既存整形不一致などの制限は
   `market-data-application-boundary.md` に記載。取得実行の共通入口とWorker移行は次単位。

3. **Durable Workerの導入**（①〜⑤完了）
   設計・DB/lease・ページ単位保存・独立プロセスと切替・起動bat/表示/実地試験まで実装し、
   利用者環境の運用DBへの切替とR/A/Qキー操作の実地確認も完了した。詳細は
   `durable-market-data-worker.md`。

次に着手する単位は次のいずれかを想定する（順序は利用者の優先度で決めてよい）。

4. **Horizon 1の残る設計負債の解消**（`app`/`src`統一・`catalog.py`分割は完了、frontendが残る）
   `app`と`src/ai_system_trading`の単一runtime package化、および`catalog.py`の
   Workspace/Connection/Instrument/Market Data単位への分割（モデル・スキーマ・ルート）は
   2026-09-16に完了した。残るのはfrontend（`App.tsx`集中）のfeature単位分割のみ。
   Horizon 1の完了条件（HTTPルートから複雑なDB処理を直接呼ばない、import path一本化）に
   直結するが、新機能ではないため緊急度は低い。Horizon 2着手前に片付けるほど後続の
   実装（stream adapter追加等）が複雑化しにくい。

5. **Horizon 2: リアルタイム観測と運用可視性**（開始条件は充足済み）
   OANDA Practice / Binance Spot Testnetのstream adapter追加、短命stream ticket設計、
   再接続時のREST補完。Durable Workerが安定稼働し履歴RESTのギャップ補完が信頼できる状態は
   達成済みのため、開始条件自体は満たしている。

Paper Tradingの詳細設計は3・4と並行して作成できるが、実装開始はHorizon 2完了後とする。

### 2026-09-26改訂: 現在の優先順位（利用者判断）

上記1〜5(Horizon 0〜2)は完了済み。Horizon 3(Paper Tradingコア)はBot管理API・
実行ループ/Worker・最低限のUIまで、Horizon 4はBacktest API・最低限のUIまで
実装済み(各節の状態行を参照)。この時点で、利用者は開発計画を見直し、
**サーバーへのデプロイ・課金・Horizon 5残タスク(ライセンス・顧客管理機構、
ADR 0006参照)を、実際に機能し利益を生むトレードロジックが検証できるまで
後回しにする**方針を示した。

理由: 調査の結果、現在全Botの売買判断ロジックは`app/trading/application
/dummy_signal.py`の`generate_dummy_signal`のみであることが判明した。中身は
「直近5本の終値のSMAと比較して上なら買い・下なら売り」という固定ルールで、
モジュール自身のdocstringに「do not tune this to chase performance --
意図的に単純な説明用ルールであり、開発中の戦略ではない」と明記されている。
つまりこれまで一度も「儲かるように作る/検証する」対象にされたことがない。
デプロイ・課金基盤がどれだけ整っていても、この核心部分が機能しなければ
無意味、との判断である。

次の推奨単位:

1. **技術指標ベースの手作り戦略を開発し、Backtest APIで検証する**
   (2026-09-26決定: AI/MLモデル(Chronos、Horizon 6)導入より先に着手する。
   Horizon 6は「non-AI baselineが既にある」ことを開始条件の一つとしており、
   現状の`dummy_signal.py`は収益性検証済みのbaselineとは言えないため、この
   単位はHorizon 6着手の前提を実質的に整備する意味も持つが、目的はあくまで
   実際に機能する戦略を作ることであり、Horizon 6着手そのものではない)。
   `dummy_signal.py`を置き換える/追加する形でRSI・MACD・ボリンジャーバンド等を
   組み合わせた具体的な戦略候補を実装し、`POST /workspaces/{id}/backtests`
   (Horizon4、2026-09-26実装)で過去データに対する収益性(net_pnl、
   Profit Factor、勝率、最大DD)をwalk-forward評価で確認する。既存の
   Strategy/StrategyVersionモデル・Backtest基盤をそのまま利用できる。
2. 検証結果が有望であれば、paper運用(既存のBot管理API・実行ループ)で
   実データに対する追加検証を行う。
3. デプロイ・課金・Horizon 5残タスクの再開は、利用者からの明示的な意思表示を
   待つ。

## 11. 文書運用

- 初期設計 `docs/concept/FXtrading_rebuild/` は製品構想と候補設計の記録として保持する。
- 現在構造の正は `docs/architecture/current-and-target.md` とする。
- 長期順序と承認ゲートの正は本書とする。
- 個別機能の実装状態は `docs/plans/` の各専用計画を正とする。
- 各Horizon開始時に、そのHorizonの詳細計画、DB/API契約、状態遷移、試験計画を作成する。
- 少なくとも月次、または大きなmigration/API変更時に、本書の現在地と差分表を更新する。

## 12. 次回レビュー時に確認する指標

- 自動試験数ではなく、要件・状態遷移・障害経路のカバレッジ
- 未解消gap、source-limited範囲、重複candle、stale jobの件数
- Workerの再試行数、処理遅延、heartbeat欠落、再起動からの復旧時間
- 秘密情報・Workspace境界・Practice/Testnet強制の回帰結果
- APIルートからApplication Use Caseへ移した処理の割合
- `catalog.py`、market data service、`App.tsx`の責務分割状況
- 計画書の完了表示と実装・テスト証拠の不一致件数
