# アーキテクチャ整合性調査と長期開発ロードマップ

- 最終確認日: 2026-08-30
- 対象: `AI_System_Trading`
- 計画期間: 約 12〜18 か月を見通す段階計画
- 現在地: 市場データ観測基盤の構築段階（実資金を扱わない）
- 安全境界: OANDA Practice / Binance Spot Testnet / 内部 Paper Trading のみ

## 1. この文書の目的

初期設計資料 `docs/FXtrading_rebuild/` と、現在のアーキテクチャ、実装、既存計画、テスト、CI、DBマイグレーションを照合し、次を明確にする。

1. 初期構想のうち、現在も維持する設計原則
2. 現在の実装との乖離が、意図的な段階導入か、解消すべき設計負債か
3. 今後の開発順序、開始条件、完了条件
4. 将来機能を安全境界の外へ無意識に拡大しないための承認ゲート

本書は長期の実行順序を扱う。個別機能の詳細仕様と実装チェックリストは、各機能の専用計画を正とする。

## 2. 調査対象と判定上の注意

### 初期設計

- `docs/FXtrading_rebuild/00_概要と文書一覧.md` から `10_技術選定と開発構成.md`
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
- 市場データWorkerがWebプロセス内で動作し、再起動耐性と多重起動制御が限定的
- バックグラウンド状態がプロセスローカルで、永続lease、retry、stale recoveryが未完成
- APIルート、catalogモデル、市場データサービス、`frontend/src/App.tsx`への責務集中
- `app`と`src/ai_system_trading`の二重パッケージ構造
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
| Durable Worker | 未実装 | Web lifespan内polling | 移行必須 |
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
| Worker | 独立Market Data/Bot/Notification Worker | Webプロセス内poller | 設計負債 | 次の基盤フェーズで独立化する |
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

状態: `[~]` 最初のApplication境界抽出を実装。詳細は
`market-data-application-boundary.md`。独立Worker・再起動耐性は未実装。

2026-08-30の利用者判断: OANDA APIキーを生成できないため、Horizon 0のOANDA実データ確認は
延期（NOT VERIFIED）。Binance取得は利用者から成功報告あり。この残件を明示して、
Application抽出を先行する。OANDA確認やHorizon 1全体を完了扱いにはしない。

#### 開始条件

- Horizon 0のデータ品質試験が安定している
- BackfillJobとSubscriptionの状態遷移が文書化されている

#### 実装・整備

- `[x]` backfill受付、coverage範囲解決、subscription変更をApplication Use Caseへ切り出す。
  取得実行・coverageの低水準計算は既存serviceへ委譲し、Worker移行時の共通入口整備は次単位で行う。
- `catalog.py` をWorkspace、Connection、Instrument、Market Data単位へ段階分割する
- Web lifespan内Workerを独立プロセスへ移す
- PostgreSQL advisory lockまたはlease、heartbeat、retry、stale recovery、graceful shutdownを実装する
- 同一Workspace・銘柄・timeframeの多重処理をDB境界で抑止する
- APIプロセス再起動時にもジョブの状態と再開判断が失われないようにする
- frontendのAPI client、feature state、画面componentを機能単位へ分割する
- `app`と`src/ai_system_trading`の役割を確定し、単一runtime packageへ移行する

#### 完了条件

- APIを停止してもWorkerの所有権とジョブ状態が曖昧にならない
- Workerの二重起動、異常終了、再起動を含む統合試験が通る
- HTTPルートから外部SDKや複雑なDB処理を直接呼ばない
- import path、migration、package build、local startupが一本化後も通る

### Horizon 2: リアルタイム観測と運用可視性（3〜5か月）

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
- stream ticketが短命・一回限りで、ログやURLに長期資格情報を残さない

### Horizon 3: Paper Tradingコア（5〜8か月）

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

#### 開始条件

- 配布対象、利用者、運用責任、データ・SDK・取引所規約の確認範囲が決まっている
- ローカル単独利用を超える必要性が承認されている

#### 実装・整備

- OIDC Authorization Code + PKCEへ移行する
- Owner/Operator/Viewer/System WorkerのRBACを全APIへ適用する
- 本番・検証・開発環境を分離する
- 配布環境ではSecret Manager/KMSを使用し、rotation/revocationを運用化する
- Event Log、Outbox、Notification Worker、通知設定を実装する
- Gmail API等の通知先はOAuth scopeと監査要件を確認後に導入する
- backup/restore、migration rollback方針、障害対応手順、SLOを整備する
- dependency、SBOM、secret scan、脆弱性対応をrelease gateへ組み込む

#### 完了条件

- Dev Owner tokenを無効化できる
- 権限境界、CSRF/CORS、session、secret rotation、監査ログのsecurity testが通る
- DB/secret/configの復旧訓練が成功する
- 通知の重複、欠落、再送をOutboxから追跡できる
- 配布物と利用するデータ・SDK・モデルの権利確認記録がある

### Horizon 6: AI Model Lab（任意、12〜18か月以降）

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

- backend lint/format/test、frontend lint/build/testをCIで維持する
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

3. **Durable Workerの導入**（次の実装単位）
   独立プロセス、DB lease、heartbeat、retry、stale recoveryを実装し、realtimeやPaper Tradingを載せられる運用基盤を作る。

Paper Tradingの詳細設計は2と3に並行して作成できるが、実装開始はDurable Workerとデータ品質の完了後とする。

## 11. 文書運用

- 初期設計 `docs/FXtrading_rebuild/` は製品構想と候補設計の記録として保持する。
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
