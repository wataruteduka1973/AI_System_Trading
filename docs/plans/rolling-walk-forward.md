# Rolling walk-forward (複数区間検証)

## 目的

`backtest_walk_forward.run_walk_forward` は70/30の単一分割しか持たず、
「検証区間がたまたま戦略に不利な相場だった」のか「戦略にエッジが無い」のかを
切り分けられない(2026-09-28 EMA trend検証で顕在化)。1年分のデータを
複数のfoldに分け、各foldで訓練/検証を独立に評価できるようにする。

## 変更対象

- `app/trading/application/backtest_replay.py`: `run_replay` に `warmup_bars`
  (先頭N本はシグナル計算用の履歴としてのみ使い、取引・equity曲線に含めない)を追加。
  既定値0で既存挙動は不変。
- `app/trading/application/backtest_walk_forward.py`: 固定幅ローリング分割
  `split_candles_rolling` と、全foldを評価する `run_rolling_walk_forward` を追加。
  既存の70/30 API・永続化(`run_and_persist_walk_forward`)は変更しない。
- `scripts/run_walk_forward_report.py`: DB上の実データに対して戦略×時間足で
  ローリング検証を実行し、fold別の結果を表示するCLI(研究用、API/UIは対象外)。

## データフロー

candles(全期間) → `split_candles_rolling(train_bars, test_bars)` → foldごとに
`[start - warmup, end)` をスライス → `run_replay(warmup_bars=...)` →
`compute_metrics` → `RollingFoldResult` のリスト。

## 設計判断

- **固定幅ローリング(anchoredではない)**: 各foldの訓練区間の長さを揃え、
  fold間の比較条件を同じにする。step = test_bars(検証区間は重複しない)。
- **末尾の端数は捨てる**: 検証区間が `test_bars` 未満になる端数foldは作らない
  (区間長の違う結果を並べて比較しない)。
- **warm-up**: 各区間の開始位置より前の足を最大 `_HISTORY_WINDOW - 1` 本、
  履歴として渡す。区間より前の足だけなのでlook-aheadにはならない。
  EMA200などの長いlookbackを持つ戦略が、各区間の先頭約200本を取引不能のまま
  失うのを防ぐ。
- **foldごとに初期資金から独立評価**: 既存の70/30と同じ方針。
  `peak_drawdown_limit_hard` による恒久ロックもfoldごとにリセットされる。
- **永続化・API・UIは対象外**: 現段階は戦略の研究用途。必要になった時点で追加する。

## テスト

- 分割: fold数・境界・非重複・端数の切り捨て・不正引数
- `run_replay(warmup_bars)`: warm-up区間で取引しない / equity曲線に含まれない /
  シグナル生成関数はwarm-upを含む履歴を見る / 未来の足を見ない
- `run_rolling_walk_forward`: fold数と各foldの区間、warm-upが直前の足から取られること

## 想定リスク

- 検証区間の末尾で未決済のポジションは `net_pnl`(決済済み取引のみ)に含まれない。
  レポートでは該当foldに `open` を表示し、時価評価の損益(mtm)を併記する。
- 15mで1年分(約35,000本)をfold数×2回replayするため実行時間が伸びる。

## 検証結果 (2026-09-30, BTCJPY 1年, train=90日 / test=30日, 9 fold)

`python scripts/run_walk_forward_report.py` の結果(equity修正と、注文上限をcashで判定する修正の後。
docs/knowledge/backtest-equity-omits-position-cost-basis.md)。netは決済済み取引のみ、
mtmは区間末に保有中のポジションも時価で含めた損益。

| 時間足 | 戦略 | 検証foldでmtm>0 | 検証fold合計net | 検証fold合計mtm |
|---|---|---|---|---|
| 15m | dummy_sma5 | 0/9 | -14,274 | -29,287 |
| 15m | ema_trend | 0/9 | -20,866 | -28,755 |
| 1h | dummy_sma5 | 1/9 | -23,210 | -42,962 |
| 1h | ema_trend | 2/9 | -13,825 | -19,321 |
| 4h | dummy_sma5 | 1/9 | -43,178 | -56,962 |
| 4h | ema_trend | 4/9 | -11,614 | +13,730 |

- **「検証区間が下落相場だっただけ」という仮説は否定された**: 直近3 fold
  (2026-06-23〜09-21)は買い持ちで+6〜15%の上昇相場だったが、2戦略×3時間足×3 fold
  の18区間のうちmtmが黒字なのは2区間だけで、どちらも4h ema_trendが区間末に
  ロングを保有していた区間だった。負けている原因はロングオンリー制約ではなく、
  戦略にエッジが無いことだと判断する。
- 4h ema_trendの合計mtmがプラスなのは、決済済み取引(合計net -11,614)ではなく、
  上昇相場の区間末に保有していたロング4件の含み益による。取引数も1区間0〜3件と
  少なく、エッジの根拠にはならない。
- 70/30分割で有望に見えた15m ema_trendの訓練区間のnet(+6,920)は、fold 0の
  訓練区間(最初の90日)のnetと同額。利益は最初の90日だけで出ていた。
- 以後の戦略候補は、この9 fold全体で評価すること(単一分割や訓練区間だけの結果で
  判断しない)。

### Donchianブレイクアウト (2026-09-30)

`app/trading/application/donchian_breakout_signal.py`。パラメータは結果を見る前に
タートルの定番値に固定した(20/10 = System 1、55/20 = System 2)。条件は上表と同じ。

| 時間足 | 戦略 | 検証foldでmtm>0 | 検証fold合計net | 検証fold合計mtm |
|---|---|---|---|---|
| 15m | donchian_20_10 | 1/9 | -29,648 | -41,778 |
| 15m | donchian_55_20 | 1/9 | -40,587 | -54,530 |
| 1h | donchian_20_10 | 3/9 | -30,631 | -13,174 |
| 1h | donchian_55_20 | 3/9 | -34,893 | -1,010 |
| 4h | donchian_20_10 | 4/9 | -33,231 | +7,023 |
| 4h | donchian_55_20 | 4/9 | -37,020 | +24,314 |

- 決済済み取引(net)は全時間足・両パラメータで赤字。ema_trendよりも赤字幅が大きい。
- 4h 55/20のmtmがプラスなのは、決済が0件のまま上昇相場の区間末にロングを保有していた
  3 fold(fold 3/6/7、合計+54,542)による。決済した取引7件はすべて負け。
  上昇相場で買い持ちしていたのと同じで、ブレイクアウトのエッジではない。
- 結論: Donchianブレイクアウトにもエッジは見られない。パラメータ調整は行わない
  (同じ1年分のデータへの過剰適合になるため)。
