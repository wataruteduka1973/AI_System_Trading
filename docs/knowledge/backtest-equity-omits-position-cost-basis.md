# Equity must add the position's market value, not its unrealized P&L

発見・修正: 2026-09-30(ローリングwalk-forward実装時に発見)。

## 不具合

`order_flow._record_ledger`(と `backtest_replay.run_replay` の `cash_equity`)は
約定時に notional 全額を cash に計上する(buy: -notional、sell: +notional)。
それにもかかわらず、`account_valuation.compute_equity` と `run_replay` は equity を
`cash + unrealized P&L` で計算していた。そのため、ポジション保有中の equity は次のようにずれていた。

- long: エントリー時のnotional分だけ過小(100万円の口座で約9.5万円分のBTCを
  保有すると、equityが約90万円に見えた)
- short: エントリー時のnotional分だけ過大

フラットに戻ると正しい値に戻るため、決済済み取引の損益やフラットからの
エントリーのサイジングは影響を受けず、気付きにくかった。

## 修正

equity = `cash + 保有ポジションの時価`(long: `+qty*price`、short: `-qty*price`)。
これは `エントリー前のcash + unrealized P&L` と等しい。

- ライブ: `account_valuation._open_position_valuation` が時価と含み損益の両方を返し、
  `compute_equity` / `record_account_snapshot` は時価を足す。
  `account_snapshot.unrealized_pnl` 列は引き続き含み損益を記録する。
- バックテスト: `backtest_replay._market_value` を equity 計算に使う。
  `existing_open_risk`(Risk Gateの既存リスク)は引き続き含み損を使う。

## 修正で変わったこと

- 保有中の `peak_equity`・日次/週次の起点 equity・risk budget が正しい値になった。
  shortでは保有中に `peak_equity` が水増しされ、決済後に `peak_drawdown_limit_hard`
  が発動し続ける経路もあったが、それも解消された。
- `max_drawdown_pct` が、ポジションサイズではなく実際の損失を反映するようになった。
- 修正前に記録したバックテスト結果(max DD、保有中のequity、Risk Gateの判定が
  変わったdummy_signalの結果など)は比較に使わないこと。

## equityと利用可能cashを区別する

Binanceの `order_limit_pct_of_available`(10%)は、仕様上「利用可能なJPY残高」に
対する上限。修正前のequityは保有中もほぼcashと同じ値だったため、equityで代用しても
差が出なかった。equityにポジションの時価を含めた結果、保有中は両者が一致しなくなった。
そのため `account_valuation.value_account` がcash・含み損益・equityをまとめて返し、
Risk Gateは `RiskState.available_cash`(ライブ: `value_account(...).cash`、
バックテスト: `_ReplayState.cash_equity`)で注文上限を判定する。クエリの数と順序は
変わらない。

equityを使うべき判定(risk budget、損失上限、peak drawdown、BTC保有上限)と、
cashを使うべき判定(買える金額の上限)を混同しないこと。

## 再発防止

- `tests/test_account_valuation.py` と
  `tests/test_backtest_replay.py::test_equity_while_holding_is_initial_equity_plus_unrealized_pnl`
  が、保有中の equity = 初期資金 + 含み損益 を long/short 両方で検証する。
- `tests/test_risk_gate.py::test_binance_order_limit_is_a_share_of_available_cash_not_equity`
  と `tests/test_backtest_replay.py::test_risk_gate_sees_cash_not_equity_as_available_cash_while_holding`
  が、注文上限がcashで判定されることを検証する。
- `RiskState.available_cash` は必須フィールドなので、渡し忘れは mypy で検出される。
