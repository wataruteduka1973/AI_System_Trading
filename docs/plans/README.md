# 計画書の一覧

各計画書の状態を一か所で見るための索引。詳細と正の記録は各計画書にある。計画書を追加したとき、
または状態が変わったときは、この表も更新する(2026-10-03 時点、マージ済みPRと照合)。

長期の実行順序は [architecture/architecture-alignment-and-long-term-roadmap.md](../architecture/architecture-alignment-and-long-term-roadmap.md) を参照。

## 進行中

| 計画書 | 内容 | 状態 |
|---|---|---|
| [paper-trading-live-data.md](paper-trading-live-data.md) | 本番の公開価格でのペーパートレード | Unit 1〜6完了。2つのボット群(承認済み 4h 55/20 ×6、比較用 4h 110/40 ×6)が稼働中で、データを貯める段階。月1回 `scripts/report_paper_performance.py` で想定範囲と比べる |
| [bot-overview.md](bot-overview.md) | Bot管理の見やすさ(Botと口座の状況: 銘柄・純資産・損益・建玉) | 実装済み(2026-10-08) |
| [status-screens.md](status-screens.md) | システム状態・取引停止センター・通知の画面(フロントエンド) | 実装済み(2026-10-07) |
| [ledger-reconciliation.md](ledger-reconciliation.md) | 注文・約定・台帳・建玉の照合と、不整合での口座の緊急停止 | 実装済み(2026-10-07) |
| [backtest-batch.md](backtest-batch.md) | 複数銘柄のバックテストを1回で(同期、足の合計本数に上限) | 実装済み(2026-10-07)。画面は未 |
| [system-status.md](system-status.md) | ワークスペースのシステム状態API(Worker・通知・halt・接続を1回で) | 実装済み(2026-10-07)。画面は未 |
| [lock-halts.md](lock-halts.md) | 連敗・最大DDロックをhaltにしてOwnerが解除(静かに止まり続ける問題の解消) | 実装済み(2026-10-07) |
| [notification-sources.md](notification-sources.md) | 通知の発行元(市場データの停止、市場データWorkerの停止、通知の打ち切り) | 実装済み(2026-10-07) |
| [worker-failure-handling.md](worker-failure-handling.md) | トレーディングWorkerの失敗(Botを `failed` に、Worker停止の検知と通知) | 実装済み(2026-10-07) |
| [notification-wiring.md](notification-wiring.md) | 取引停止の通知(Outbox・再送・Owner/Operatorへ) | 実装済み(2026-10-07)。アプリ内通知のAPIあり、画面は未 |
| [emergency-stop.md](emergency-stop.md) | 利用者の緊急停止(Botまたはワークスペース全体、解除はOwnerのみ) | 実装済み(2026-10-06)。通知は接続済み |
| [paper-trading-server.md](paper-trading-server.md) | ペーパートレードの Worker と DB を AWS(EC2 + RDS)で動かし続ける | 2026-10-05 承認(東京、CloudFormation)。Unit 0(利用者による AWS の準備)待ち。それまでは手元 PC の自動起動でつなぐ |

## 一部完了・一部先送り

| 計画書 | 内容 | 状態 |
|---|---|---|
| [horizon5-implementation-plan.md](horizon5-implementation-plan.md) | Horizon 5: 認証・RBAC・配布運用 | グループA(OIDC・RBAC)・D(Outbox・通知)・E(リリースゲート)完了。B(AWS Secret管理)・C(ライセンス・商用配布)は未着手 |
| [realtime-market-data-stream.md](realtime-market-data-stream.md) | Horizon 2: リアルタイム市場データ配信 | 実装済み(フロントエンドまで)。24時間soak testはADR 0001で延期 |
| [ci-quality-gate-hardening.md](ci-quality-gate-hardening.md) | CI品質ゲートの強化 | 1〜3完了。4(Playwright E2E)は先送り |
| [candle-chart-and-coverage.md](candle-chart-and-coverage.md) | 市場データ・チャート・リアルタイムのロードマップ | 項目ごとに`[x]`/`[~]`/`[ ]`で管理。一部は将来の設計フェーズ |

## 完了

| 計画書 | 内容 |
|---|---|
| [market-data-services-consolidation.md](market-data-services-consolidation.md) | 市場データの旧サービス層を app/market_data/ に統合(閲覧系の認可も同じ条件に揃えた) |
| [horizon4-lite-backtest.md](horizon4-lite-backtest.md) | バックテスト基盤(Unit 1〜6) |
| [trading-halt-mvp.md](trading-halt-mvp.md) | trading_haltの発動・解除(MVP) |
| [durable-market-data-worker.md](durable-market-data-worker.md) | 市場データのDurable Worker(Horizon 1) |
| [market-data-application-boundary.md](market-data-application-boundary.md) | 市場データのApplication境界 |
| [candle-ingestion.md](candle-ingestion.md) | ローソク足の取り込み(一部はDurable Workerに置き換え) |
| [instrument-sync.md](instrument-sync.md) | 銘柄の同期 |
| [observability-logging.md](observability-logging.md) | ログ基盤 |
| [python-quality-checks.md](python-quality-checks.md) | Ruff・mypyの導入 |
| [local-launcher.md](local-launcher.md) | Windowsの一括起動 |

## 戦略の研究(結論が出たもの)

| 計画書 | 結論 |
|---|---|
| [rolling-walk-forward.md](rolling-walk-forward.md) | 4h足のトレンド追随(Donchian 55/20)に、買い持ちを上回る非対称性(上昇を取り、下落で失う量が小さい)が見られた。決済は「損切りのみ」を採用し、固定の利確は不採用 |
| [confluence-filters.md](confluence-filters.md) | 複数インジケーターでの絞り込みは利益を増やさなかった。パラメータを直近の相場に合わせる方式は不採用 |
| [multi-asset-diversification.md](multi-asset-diversification.md) | 6銘柄への分散で最大DD 5.8%と「損をしない」基準を満たした。銘柄単独では頑健ではないので、ポートフォリオ全体で運用する |

## 保留・未着手

| 計画書 | 内容 | 状態 |
|---|---|---|
| [horizon6-entry-checklist.md](horizon6-entry-checklist.md) | Horizon 6(AI Model Lab)の着手前タスク | 未着手 |
| [license-checkin-and-customer-management.md](license-checkin-and-customer-management.md) | ライセンスcheck-in・顧客管理 | ドラフト。2026-09-25から保留 |
| [horizon5-distribution-and-auth.md](horizon5-distribution-and-auth.md) | Horizon 5のドラフト計画 | horizon5-implementation-plan.md が具体化して引き継いだ |
