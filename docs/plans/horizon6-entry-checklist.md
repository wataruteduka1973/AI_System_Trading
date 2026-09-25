# Horizon 6(AI Model Lab)着手前タスク一覧

対象: `docs/architecture-alignment-and-long-term-roadmap.md`
[Horizon 6: AI Model Lab](../architecture-alignment-and-long-term-roadmap.md)節、
`docs/decisions/0001-defer-realtime-stream-soak-test.md`、
`docs/decisions/0003-horizon4-lite-backtest-before-chronos.md`

本ドキュメントは新しい決定を含まない。既存のロードマップ・ADR・コードコメントに
散らばっている「Horizon 6のために後回しにした/Horizon 6の前提として必要な」項目を
横断的に一覧化したものである。個々の項目の根拠・不採用案の検討経緯は、各参照先の
原文書を正とする。矛盾が見つかった場合はこの一覧ではなく原文書を修正すること。

## 1. Horizon 6の開始条件(揃うまで着手しない)

`architecture-alignment-and-long-term-roadmap.md` §Horizon 6より。

- [ ] non-AI baseline・Backtest・Paper実績の比較基準がある
- [ ] AI導入で改善したい評価指標と許容リスクが明文化されている
- [ ] 学習データの権利・保存・再現性・計算資源の方針が承認済み

「Backtest」実績の充足方法はフルスコープのHorizon 4ではなく、Horizon4-lite
(ADR 0003、下記§3)の完了をもって充足とする、との決定が既にある。

## 2. Horizon 6のために保留している承認ゲート

`architecture-alignment-and-long-term-roadmap.md` §9「承認ゲート」より、
Horizon 6に直結するもの。

- [ ] **外部AIモデル**: license・revision・artifact形式・sandbox・評価基準の承認

## 3. Horizon 6着手のために先行実装した/する範囲(ADR 0003, Horizon4-lite)

`docs/decisions/0003-horizon4-lite-backtest-before-chronos.md`が、フルHorizon 4を
待たずHorizon 6着手に最小限必要な範囲だけを先行実装する方針を決定している。
進捗は`docs/plans/horizon4-lite-backtest.md`を参照。

### 3.1 Horizon4-liteのスコープ(Horizon 6着手の前提)

- `backtest_run`/`backtest_trade`のORMマッピング
- 過去データリプレイハーネス
- Risk Gateへの基準時刻注入対応
- 約定シミュレーションの対象バー指定対応
- 評価指標算出(手数料込み損益・最大DD・Profit Factor・勝率・取引回数・baseline比較)
- walk-forward/out-of-sample分割(最小版)
- look-ahead bias検出テスト

### 3.2 Horizon4-liteのスコープ外(フルHorizon 4へ先送り、Horizon 6着手の前提にはしない)

ADR 0003が明示的に「Horizon 6着手の前提条件とはせず、公開運用・複数戦略運用が
具体化した時点で改めて着手する」としている項目。Horizon 6より**さらに先**の扱い。

- StrategyVersionのDraft/Validated/Approved/Retired承認ワークフロー
- spread/slippageの厳密な過去再現(tick履歴が存在しないため当面は近似値で代替)
- backtest結果のUI/ダッシュボード
- 複数戦略・複数instrumentの並行backtest運用基盤

## 4. Horizon 6ゲートを理由にコード上で意図的に未実装/未マッピングの項目

- [ ] `model_artifact`/`model_candidate`/`training_run`/`model_source`テーブル
  クラスタのORMマッピング。`app/models/strategy.py`のモジュールdocstringに、
  複数回の監査でHorizon 6の開始条件未達を確認済みとして意図的に未マッピングと
  明記されている。
  - 例外: `dataset_snapshot`のみHorizon4-liteのため`app/models/backtest.py`で
    先行マッピング済み(`backtest_run.dataset_snapshot_id`がNOT NULLのFKのため)。
    `feature_schema`/`quality_report`等はML学習パイプライン向けの列を簡易的に
    埋めているのみで、Horizon 6本体で使う際に再設計が必要になりうる
    (`docs/plans/horizon4-lite-backtest.md`参照)。
- [ ] `Signal.model_artifact_id`へのForeignKeyオブジェクト付与。DB側のFK/RESTRICTは
  既に効いているが、ORM側の宣言は`model_artifact`クラスタが未マッピングのため
  意図的に省略されている(`app/models/strategy.py`)。

## 5. Horizon 6完了後まで先送りされている別項目(ADR 0001)

`docs/decisions/0001-defer-realtime-stream-soak-test.md` — リアルタイム配信の
24時間soak test(Horizon 2単位⑧、RT-14)の**本番相当環境における最終確認**は、
Horizon 3の開始条件から切り離し、**Horizon 6完了後・本番相当サーバーデプロイ時**の
検証タスクとして実施する。Horizon 6の中身ではなく、Horizon 6の**後**に回された項目
であることに注意。

このADRの決定により、Horizon 2は定義上、Horizon 6完了までは「完了」扱いにならない
(`architecture-alignment-and-long-term-roadmap.md` §7の状態表示はこの前提で読む)。

## 6. Horizon 6の実装・整備そのもの(未着手)

開始条件が揃った後に着手する本体スコープ。
`architecture-alignment-and-long-term-roadmap.md` §Horizon 6より。

- `DatasetVersion`/`FeatureSetVersion`/`TrainingRun`/`ModelArtifact`/`EvaluationRun`の追加
- 学習・評価・昇格・rollbackを売買実行から分離
- artifact checksum・provenance・quarantine・sandboxを設ける
- 外部モデルはrevision固定・`safetensors`優先・remote code無効を原則とする
- AI出力を直接注文せず、Signalから既存Risk Gateを必ず通す

### 完了条件

- baselineを上回る効果をout-of-sampleとPaper期間の双方で説明できる
- 学習データ・feature・code・seed・artifactの系譜を再現できる
- model failureやdrift時に安全にbaselineへ戻せる
- 外部artifactを信頼境界の内側へ入れる審査記録がある

## この一覧の更新方針

- 新しいADRやロードマップ改訂でHorizon 6の前提条件が変わった場合、この一覧を
  同じコミットで更新する。
- チェックボックスは進捗管理用であり、完了マークは実装と検証証拠が揃った時だけ
  付ける(AGENTS.mdの原則どおり)。
