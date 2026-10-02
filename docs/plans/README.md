# 計画書の一覧

各計画書の状態を一か所で見るための索引。詳細と正の記録は各計画書にある。計画書を追加したとき、
または状態が変わったときは、この表も更新する(2026-10-02 時点、マージ済みPRと照合)。

長期の実行順序は [architecture/architecture-alignment-and-long-term-roadmap.md](../architecture/architecture-alignment-and-long-term-roadmap.md) を参照。

## 進行中

| 計画書 | 内容 | 状態 |
|---|---|---|
| [paper-trading-live-data.md](paper-trading-live-data.md) | 本番の公開価格でのペーパートレード | Unit 1〜5完了(6銘柄のボットが稼働中)。Unit 6(ライブの成績とバックテストの期待値の比較)は未着手 |

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
