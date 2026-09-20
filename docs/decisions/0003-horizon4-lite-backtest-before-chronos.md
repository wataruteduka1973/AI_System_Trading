# ADR 0003: Horizon 6(Chronos導入)着手前に、縮小版Backtest基盤(Horizon4-lite)を先行実装する

- 状態: 承認済み
- 日付: 2026-09-20
- 決定者: 利用者
- 記録者: Claude(このリポジトリのAIエージェント)

## 背景

Horizon 6(AI Model Lab、Chronos導入)の開始条件を確認したところ、
`architecture-alignment-and-long-term-roadmap.md` §Horizon6は以下を要求している。

> - non-AI baseline、Backtest、Paper実績の比較基準がある
> - AI導入で改善したい評価指標と許容リスクが明文化されている
> - 学習データの権利、保存、再現性、計算資源の方針が承認済み

一方、Horizon 3(Paper Trading)のダミーシグナルは`dummy_signal.py`のモジュール
docstringに明記されている通り、意図的にbacktest評価対象外の scaffolding であり、
non-AI baselineとしての役割は代替できても「Backtest実績」そのものは存在しない。
Backtestの再現可能な仕組み(Horizon 4)自体が未着手のため、Horizon 6の開始条件を
構造的に満たせない状態にある。

この状況を受け、「Backtestなしで品質保証は可能か」を検討した。Paper Trading
(forward test)のみに頼る案は、以下の理由で不十分と判断した。

- 統計的に意味のある結果が出るまで数週間〜数か月かかり、単一の相場レジームしか
  検証できない
- overfitting、look-ahead bias、データ漏洩を検出する手段がない
- 過去の急変相場(暴落・トレンド相場・レンジ相場)でどう振る舞うか事前に確認できない
- 「改善したか」を定量的に示すbenchmark比較の手段がない

これは`architecture-alignment-and-long-term-roadmap.md`のHorizon 4完了条件
(walk-forward、out-of-sample、benchmark比較、look-ahead bias検出試験)がそもそも
解決しようとしている問題そのものであり、Horizon 6の開始条件は恣意的なゲートではなく、
AI戦略に対する現状唯一の客観的品質保証手段として機能していると判断した。

一方で、Horizon 4のフルスコープ(StrategyVersionのDraft/Validated/Approved/Retired
承認ワークフロー等の運用ガバナンス機能を含む、8〜11か月見積り)をすべて完了させてから
でなければHorizon 6に進めない、とするのは、単独開発・非公開運用の現段階に対して
過剰である可能性がある。

### 調査の結果判明した既存資産

- `fx.backtest_run`/`fx.backtest_trade`テーブルは初期migration
  (`database/postgresql_schema_v0.1.sql`)で既にDB上に作成済みだが、
  `app/models/`配下にORMマッピングは存在しない
- `app/market_data/application/indicators.py`(ATR等)はDB非依存の純粋関数で、
  過去データにそのまま適用可能
- `app/trading/application/risk_gate.py`は`datetime.now(UTC)`を直接呼んでおり、
  `app/trading/application/order_flow.py`の約定シミュレーションはDB上の
  最新確定バーを常に検索する設計のため、いずれも過去時点のリプレイには改修が必要
- `docs/concept/FXtrading_rebuild/06_AI学習と外部モデル探索設計.md`は既に
  「① Dataset Snapshotと時系列分割 → ② 単純baseline vs テクニカル戦略比較 →
  ③ 学習ジョブ…」という実装順を明記しており、まずbaselineで評価パイプライン
  自体の正しさを確立する方針を示している
- spread/slippageの過去再現に必要なtick履歴は保存されておらず
  (`fx.instrument_spread`は最新値のみのUPSERT)、厳密な再現は現状不可能

## 決定内容

Horizon 6(Chronos導入)着手前に、フルスコープのHorizon 4ではなく、
Chronos vs non-AI baseline比較に客観的な判断材料を与えるために必要な
最小限のBacktest機能(以下「Horizon4-lite」)を先行実装する。

### Horizon4-liteのスコープ(含む)

- `backtest_run`/`backtest_trade`のORMマッピング(既存テーブルへの接続)
- 過去データリプレイハーネス(StrategyVersion + 日付範囲を受け取り、過去バーを
  1本ずつ進めながら同じシグナル/Risk Gate/約定ロジックを呼ぶ実行エンジン、新規)
- Risk Gateへの基準時刻注入対応(`datetime.now(UTC)`直呼びの置き換え)
- 約定シミュレーションの対象バー指定対応(最新バー固定の解消)
- 評価指標算出: 手数料込み損益、最大DD、Profit Factor、勝率、取引回数、
  baseline比較
- walk-forward/out-of-sample分割(訓練期間と検証期間を分けるだけの最小版)
- look-ahead bias検出テスト(as_of時点より先のデータにアクセスしていないかを
  機械的に検証する自動テスト)

### Horizon4-liteのスコープ外(含まない、将来のフルHorizon4に先送り)

- StrategyVersionのDraft/Validated/Approved/Retired承認ワークフロー
- spread/slippageの厳密な過去再現(tick履歴が存在しないため、当面は固定値/
  近似での代替とし、既知の制約として明記する)
- backtest結果のUI/ダッシュボード
- 複数戦略・複数instrumentの並行backtest運用基盤

Horizon4-lite完了後、Chronos導入(Horizon 6)の着手を検討する。フルスコープの
Horizon 4(運用ガバナンス機能等)は、Horizon 6着手の前提条件とはせず、
公開運用・複数戦略運用が具体化した時点で改めて着手する。

## 検討した代替案

### (a) 例外承認してBacktestなしで先行実装

**不採用の理由**: 「改善したか」を客観的に示す手段が存在しないまま進めることになり、
AGENTS.mdが求める品質保証原則(「動いた」だけでは完了ではない)に反する。特にAIモデルは
overfittingやレジーム依存など非決定的な失敗モードを持ちやすく、forward testのみでの
検証は問題発見までの時間が長すぎ、実資金投入前の安全網として機能しない。

### (b) フルスコープのHorizon 4を完了させてから着手

**不採用の理由**: StrategyVersion承認ワークフロー等の運用ガバナンス機能は、複数の
開発者・戦略・利用者が関与する運用を前提にした機能であり、単独開発・非公開運用の
現段階では必要性が低い。8〜11か月の見積りに対し、Chronos比較に真に必要な要素は
一部(Dataset差し替え可能なリプレイ基盤と評価指標)に限られ、全機能の完了を待つことは
開発速度上のコストに見合わない。

### (c) 最小限の統制(revision固定・provenance記録)のみ追加して先行実装

**不採用の理由**: 再現性・監査証跡は担保できるが、「戦略が実際に良いか」を検証する
機能(baseline比較、walk-forward、look-ahead bias検出)が抜け落ちたままであり、
品質保証の核心的なギャップは埋まらない。(a)と実質的に同じ問題を抱える。

### 採用: Horizon4-lite(上記いずれでもない新たな案)

必要最小限のBacktest機能のみを先行実装することで、(a)/(c)が抱える品質保証の
欠落と、(b)が抱える開発速度上のコストを両方回避できると判断した。

## 影響範囲

- `architecture-alignment-and-long-term-roadmap.md`のHorizon 4/Horizon 6
  セクションに、本ADRへの参照を1行追記する(完了条件本文は書き換えない、
  ADR 0001と同様の方針)
- Horizon 4(フルスコープ)は、本ADR時点でHorizon4-liteの実装をもって
  「一部着手」したとみなすが、「完了」とは扱わない
- Risk Gate/order_flowへの基準時刻注入・対象バー指定改修は、既存のPaper Trading
  本番相当パス(リアルタイム運用)の挙動を変えないよう、後方互換性
  (デフォルト引数で現在時刻/最新バーを使う等)を維持する必要がある。
  この改修自体の詳細設計は別途Implementation Planで扱う
- spread/slippageの近似を許容する判断は、将来「精度が甘い」との指摘に
  つながりうるリスクを明示的に受容するものである

## 将来の見直し条件

- Chronos比較の結果、近似slippage/spreadでは判断を誤る(baseline比較の結論が
  覆る)ことが判明した場合、tick履歴の保存・厳密な過去再現の実装を改めて検討する
- 公開運用・複数戦略運用が具体化した時点で、StrategyVersion承認ワークフロー等
  フルHorizon 4機能の着手を検討する
