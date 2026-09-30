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

### RSI逆張り + EMA(200)トレンドフィルター (2026-09-30)

`app/trading/application/rsi_mean_reversion_signal.py`。パラメータは結果を見る前に
定番値に固定した: RSI(14) 30/70(Wilder)と RSI(2) 10/70(Connors)。
終値 > EMA(200) の間にRSIが売られすぎ水準を下抜けた最初の足でbuy、RSIがexit水準を
上抜けた最初の足でsell(exitはトレンドフィルターしない)。

**判定はmtm(時価込み)で行う**: `net` はエントリー時の手数料を含まない既知の不具合が
ある(下記)。取引回数が多い戦略ほど、`net` が実際より良く見える。

| 時間足 | 戦略 | 検証foldでmtm>0 | 検証fold合計mtm | 検証foldの取引数合計 |
|---|---|---|---|---|
| 15m | rsi14_30_70 | 7/9 | +11,562 | 18 |
| 15m | rsi2_10_70 | 0/9 | -83,600 | 434 |
| 1h | rsi14_30_70 | 6/9 | +15,259 | 8 |
| 1h | rsi2_10_70 | 2/9 | -5,749 | 118 |
| 4h | rsi14_30_70 | 2/9 | +4,115 | 1 |
| 4h | rsi2_10_70 | 4/9 | +1,687 | 29 |

頑健性の確認(rsi14_30_70、同じパラメータのまま、まだ評価していない時間足で実行):

| 時間足 | 検証foldでmtm>0 | 検証fold合計mtm | 検証foldの取引数合計 |
|---|---|---|---|
| 30m | 4/9 | +2,774 | 8 |
| 5m | 3/9 | -8,137 | 73 |

- rsi14_30_70は、これまでの候補で初めて15m・1hの検証区間でプラスになった。
  買い持ちで-27%・-18%だった下落区間でもプラスだった(15mのfold 1・5、1hのfold 5)。
- ただし**有望だが未確定**:
  - 取引数が少ない(15mで18件、1hで8件)。この件数では偶然と区別できない。
  - 隣の時間足では安定しない(30mはほぼ損益ゼロで、訓練区間に-28,000前後の区間がある。
    5mは赤字)。
- rsi2_10_70は取引回数が多く、手数料負けしている(15mで434件、mtm -83,600)。
- 次の検証: パラメータは変えず、データ期間を延ばして検証区間の取引数を増やす
  (Binanceの公開履歴は1年より前まで取得できる)。

### 既知の不具合: `net_pnl` / `total_fees` にエントリー手数料が含まれない

`backtest_replay._apply_and_record` は、ポジションを減らす約定でだけ `TradeRecord` を
作り、その `fees` には決済約定の手数料しか入れない。エントリー約定の手数料はcashからは
引かれるが、どの `TradeRecord` にも計上されない。そのため `compute_metrics` の
`net_pnl`・`total_fees`・勝率・Profit Factorは、1往復あたりエントリー手数料分
(Binanceで約定代金の0.1%)だけ楽観的になる。equity(`ending_equity`・`equity_curve`)は
正しい。未修正。
