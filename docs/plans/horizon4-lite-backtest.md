# Horizon4-lite Backtest 実装計画

対象ADR: `docs/decisions/0003-horizon4-lite-backtest-before-chronos.md`

本計画は、ADR 0003が定義した「Horizon4-lite」のスコープを、AGENTS.md §4の要求項目
(実装目的・変更対象・新規作成対象・データフロー・依存関係・実装順序・テスト方法・
想定リスク)に沿って具体化したものである。実装単位はできるだけ小さく分割している。

## 実装目的

Chronos導入(Horizon 6)着手前に、Chronos vs non-AI baselineを客観的に比較するために
必要な最小限のBacktest機能を実装し、ADR 0003が指摘した「Backtestなしでは戦略の
品質保証ができない」という構造的ギャップを埋める。

## 事前調査で判明した設計上の論点(実装着手前に確認したい点)

計画作成にあたり既存コードを調査した結果、ADR 0003の時点では明示していなかった
以下の設計判断が必要だと分かった。いずれも「最小限のBacktest機能」というスコープ
自体は変えないが、実装方法に関わるため事前に共有する。

1. **`dataset_snapshot`もORMマッピングが必要**: `backtest_run.dataset_snapshot_id`は
   NOT NULLのFKであり、`dataset_snapshot`テーブル(DB上に存在、`feature_schema`
   `quality_report`等ML学習パイプライン向けの列を含む)もORM未マッピングだった。
   Horizon4-liteでは特徴量エンジニアリングは行わないため、`feature_schema`には
   最小限の値(使用カラム名程度)、`quality_report`には`fx.market_data_gap`ベースの
   簡易欠損サマリ、`storage_uri`にはDB参照(別ストレージへのエクスポートは行わない)
   を入れる想定。将来Horizon 6本体でこのテーブルを使う際に再設計が必要になりうる。

2. **既存のライブ実行パス(`risk_gate.evaluate_signal`、`order_flow.place_order`)を
   そのまま流用しない**: 両者とも実際の`TradingAccount`/`TradingPosition`/
   `InstrumentSpread`等、DBに永続化された「現在」の状態に強く結合している
   (`risk_gate.py`は`datetime.now(UTC)`、`order_flow.py`は「DB上の最新確定バー」に
   常に依存)。これをそのまま過去時点のリプレイに転用しようとすると、本物の
   Paper Trading用アカウント・ポジション・台帳を書き換えてしまうリスクがある
   (`trading_account.mode`のCHECK制約は`paper`/`live`のみで`backtest`用の値がなく、
   専用アカウントを作る場合もライブのPaper Trading一覧に混在してしまう)。
   このため、**conservative-v1のルール判定そのもの(純粋なロジック)だけを
   `risk_gate.py`から抽出・共有し、DBの現在状態を読みに行く部分とは分離する**方針とする。
   約定シミュレーションも同様に、`order_flow.py`の計算式(fee/slippage)を参照しつつ
   backtest専用の関数として新規実装し、`order_flow.place_order`自体は呼ばない。
   これによりライブ実行パスの挙動は一切変更されない。

この2点について、想定通りで進めてよいか、別の方針(例: 専用のbacktest用
TradingAccountを許容するmigrationを追加する等)を検討すべきか、確認したい。

## 変更対象

- `app/trading/application/risk_gate.py`: conservative-v1のルール判定を、DB状態
  収集部分と純粋関数部分に分離するリファクタ。**外部から見た`evaluate_signal`の
  シグネチャ・挙動は変更しない**(regression防止)

## 新規作成対象

- `app/models/backtest.py`: `DatasetSnapshot`, `BacktestRun`, `BacktestTrade`の
  ORMモデル(既存DBスキーマへの接続のみ、migration不要)
- `app/trading/application/backtest_fill.py`(仮称): backtest専用の約定シミュレーション
  純粋関数(過去candle・spread近似値・fee rateを入力に約定価格・fee・position更新を計算)
- `app/trading/application/backtest_replay.py`(仮称): リプレイハーネス本体
- `app/trading/application/backtest_metrics.py`(仮称): 評価指標算出
  (PnL・最大DD・Profit Factor・勝率・取引回数・baseline比較)

## データフロー

1. instrument + timeframe + 期間 + StrategyVersion + RiskProfileVersionを指定して
   backtestを起動
2. `dataset_snapshot`を作成(対象期間のcandle件数・欠損チェック・checksum算出)
3. `backtest_run`を`queued` → `running`で作成
4. リプレイハーネスが対象期間のcandleを時系列順に1本ずつ処理:
   a. その時点(as_of)までのcandle履歴のみを見てシグナル生成
      (`dummy_signal.py`相当のロジックを流用。AIモデル比較時はここをChronos推論に
      差し替えられるインターフェースにする)
   b. conservative-v1の純粋判定関数で risk decision を算出(in-memoryの
      simulated equity/positionを入力とする。DBの実アカウントは参照しない)
   c. 承認されれば`backtest_fill.py`でsimulated fill(spread/fee適用)、
      in-memory position更新
   d. 決済発生時に`backtest_trade`を1件作成(`sequence_no`採番)
5. 全期間処理後、`backtest_run.summary_metrics`にbaseline比較を含む指標を書き込み
   `succeeded`/`failed`へ遷移
6. walk-forward/out-of-sample分割は、指定期間を訓練用/検証用に分けて同じハーネスを
   2回呼ぶ形で実現する(専用のオーケストレーション機構は作らない)

## 依存関係

- `fx.candle`の対象期間・timeframeでの実データ蓄積量(不足していれば先にbackfillが
  必要になる可能性。実装着手前にDB上の実データ件数を確認する)
- 既存の`StrategyVersion`/`RiskProfileVersion`モデル、`dummy_signal.py`のロジック
- 新規`DatasetSnapshot`/`BacktestRun`/`BacktestTrade` ORMモデル(Unit 1)

## 実装順序

### Unit 1: DatasetSnapshot/BacktestRun/BacktestTrade ORMマッピング
- 既存DBスキーマへの接続のみ、挙動変更なし
- `app/models/backtest.py`新規、relationship配線
- テスト: モデルのCRUD unit test(作成、FK制約違反時のエラー)

### Unit 2: risk_gate.pyの状態収集/純粋判定の分離リファクタ
- conservative-v1のルール判定をDB非依存の純粋関数として抽出し、ライブ・backtest
  両パスから呼べるようにする。既存`evaluate_signal`はDB状態を集めてこの関数に
  委譲する形にリファクタ(シグネチャ・挙動は不変)
- テスト: 既存のrisk_gate関連テストが全て成功すること(regression)。新規純粋関数への
  直接unit testを追加

### Unit 3: backtest用の約定シミュレーション
- `order_flow.py`のfee/slippage計算式を参照しつつ、backtest専用の純粋関数として
  新規実装(`order_flow.place_order`は呼ばない・変更しない)
- テスト: 既知の入力に対する約定価格・fee計算のunit test

### Unit 4: リプレイハーネス本体
- Unit 1〜3を組み合わせ、指定期間のcandleを順に処理して`backtest_trade`を生成
- テスト: 小さな固定candleセットでの結合テスト(既知の入力→既知の取引結果)、
  look-ahead biasが無いことを検証するテスト(as_of以降のcandleにアクセスしていないことを
  アサートする専用テスト)

### Unit 5: 評価指標算出
- `backtest_trade`からPnL・最大DD・Profit Factor・勝率・取引回数・baseline比較を
  計算し`summary_metrics`に保存
- テスト: 既知のtrade列に対する指標計算のunit test

### Unit 6: walk-forward分割 + look-ahead bias検出テストの仕上げ
- 期間を訓練/検証に分けて2回リプレイする最小限のオーケストレーション
- look-ahead bias検出テストをHorizon4完了条件相当の回帰テストとして整備

## テスト方法

- 各Unitごとにunit test必須(pytest)
- Unit 4完了時点で結合テスト(固定candleセット、決定的な結果を期待)
- 既存のrisk_gate/order_flow関連テストは全てregressionとして再実行し成功を確認する
- look-ahead bias検出テストは必須(AGENTS.md Testing原則、Horizon4完了条件にも明記)

## 想定リスク

- `fx.candle`の実データ蓄積量が対象期間で不足している場合、意味のあるbacktestが
  組めない(要事前確認、不足時は先にbackfillタスクが必要になる)
- `risk_gate.py`のリファクタは既存のライブ実行パスの挙動に影響を与えうるため、
  regressionテストを厳格に実施する必要がある
- spread近似(現在値のみ、tick履歴なし)を使うため、backtestの損益は実際の過去
  spread環境と乖離する可能性がある(ADR 0003で明記済みの既知の制約であり本計画では
  再論しない)
- `dataset_snapshot`の`feature_schema`/`quality_report`等、本来ML学習パイプライン
  向けに設計された列を簡易的に埋めることになるため、将来Horizon 6本体でこのテーブルを
  使う際に再設計が必要になる可能性がある

## Definition of Done

`docs/quality/definition-of-done.md`に従う。各Unit完了時にFormatter/Linter/
TypeChecker/Unit Testを実行し成功を確認する。
