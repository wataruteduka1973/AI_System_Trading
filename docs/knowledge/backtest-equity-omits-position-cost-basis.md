# Equity while a position is held omits its cost basis (open issue)

発見: 2026-09-30、ローリングwalk-forward実装時。未修正。

## 症状

`backtest_replay.run_replay` の mark-to-market equity と、ライブ側
`account_valuation.compute_equity` はどちらも `cash + unrealized P&L` で計算する。
一方、`order_flow._record_ledger`(と `run_replay` の `cash_equity`)は約定時に
notional 全額を cash に計上する(buy: -notional、sell: +notional)。

その結果、ポジション保有中の equity は:

- long: エントリー時のnotional分だけ過小(例: 100万円の口座で約9.5万円分のBTCを
  保有すると、equityが約90万円に見える)
- short: エントリー時のnotional分だけ過大

フラットに戻ると正しい値に戻る。

## 影響

- `ReplayResult.ending_equity` と `equity_curve` がポジション保有中は誤り。
  `compute_metrics` の `max_drawdown_pct` は、損失ではなくポジションサイズを
  反映した値になる。
- 検証区間の末尾でポジションを保有していると、`ending_equity - initial_equity` が
  -9万円前後になる(scripts/run_walk_forward_report.py では mark-to-market を
  表示せず、`open` フラグだけを出している)。
- Risk Gate: 保有中に評価される `equity`・`peak_equity`・日次/週次の起点 equity が
  歪む。shortでは保有中に `peak_equity` が水増しされるため、決済後に
  `peak_drawdown_limit_hard` が発動し続ける(恒久ロック)原因になりうる。
- ロングオンリー(Binance)で、フラットからエントリーする場合のサイジングは
  フラット時の正しいequityで行われるため、決済済み取引の `net_pnl` や
  `realized_pnl` は影響を受けない。

## 修正方針の候補(未決定)

equity を `cash + 保有ポジションの時価(long: +qty*price, short: -qty*price)` と
定義し直す。ライブ側 Risk Gate の挙動も変わるため、利用者の確認後に着手する。
