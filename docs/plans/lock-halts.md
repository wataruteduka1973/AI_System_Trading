# 連敗・最大ドローダウンのロックを取引停止(halt)にする

状態: 実装済み(2026-10-07)。2026-10-07 の利用者決定(「haltに変えてOwnerが解除」)による。

## 何が問題だったか

Risk Gate の連敗(`consecutive_loss_limit` = 3 を超える、つまり4連敗)と最大ドローダウン(`peak_drawdown_limit` = 5% を超える)は、
超えると新規の注文を**拒否**する。しかし、測っているものが、取引をやめた Bot では変わらない。

- 連敗は、台帳の「直近の連続した負け」。新規の取引が無ければ、数は変わらない。
- ピークは、記録された**過去最高**の資産。取引しなければ、新しい最高値は出ない。

つまり一度超えると、**二度と解除されない**。しかも、`trading_halt` にも通知にもならず、`risk_decision` の拒否が積まれるだけで、
静かに止まったままになる。計画書は「ライブでは人が解除する前提」と書いていたが、解除の実装が無かった。
バックテストにも同じ性質があり、単一の連続実行は、最初のロックで取引を終える
(BTCUSDT 4h・2024-01〜2026-09 の実行で、取引10件がすべて2024年内、1hは2024年5月、5分足は2024年1月で終わっていた)。
(日次・週次の損失は暦で戻るので、この問題は無い。)

## 決めたこと

| 論点 | 決定 |
|---|---|
| ロックの正体 | `trading_halt`(`consecutive_loss_limit` / `peak_drawdown_limit`、口座スコープ、`entry_halted`、自動解除なし)。他の停止原因と同じく、画面に出て、Owner と Operator に通知が届く |
| いつ発動するか | 新しい足ごとに、各 Bot の評価の最初に確認する(`risk_locks.sync_lock_halts`)。限界を超えた負けが確定した足の次の評価で、エントリーのシグナルを待たずに発動する。限界は Risk Gate が拒否を始める「初期閾値」と同じ(3連敗を超える、5%を超える) |
| 解除 | **Owner のみ**。既存の `POST /trading-halts/{id}/release` が、この2つに限り、**1回で `released` にする**(他の原因のような「1段ずつ緩和」はしない。`warning` に下げても、数が変わらないので次の評価で戻ってしまう) |
| 解除すると | その時点から**数え直す**。連敗は解除後に確定した取引だけ、ピークは解除後に記録された資産だけを数える(`risk_gate._consecutive_losses`/`_peak_equity` の `since`) |
| 自動緩和 | しない。紙の上で戻っても、戦略が信頼できる状態に戻ったとは言えない。人が判断する |
| 発動中の注文 | `place_order` が新規・増し玉を拒否する(既存の halt と同じ)。`bot_evaluation` は先に確認して `halted` として記録し、例外にしない |
| バックテスト | API の実行は、30日ごとに解除した前提で評価する(`LOCK_RELEASE_DAYS = 30`、`run_replay(lock_release_days=)`)。ロックがかかってから30日たつと、連敗を0に、ピークを現在の資産に戻す。研究用のスクリプトは従来どおり(窓を連結する方式)。実行の `parameters` に `lock_release_days` を残す |

## 実装

- `app/trading/application/risk_locks.py`(新規): 理由の定数、`sync_lock_halts`。
- `trading_halt.py`: `last_release_time`、`release_lock`、通知の文言(理由のラベル)。
- `risk_gate.py`: `_consecutive_losses`・`_peak_equity` が `since` を受け取り、`evaluate_signal` が各ロックの最後の解除時刻から数える。
- `bot_evaluation.py`: 新しい足ごとに `sync_lock_halts` を呼ぶ。
- `app/api/routes/trading_halts.py`: 2つのロックは直接解除。
- `backtest_replay.py`・`backtest_metrics.py`・`backtest_walk_forward.py`・`backtest_provisioning.py`: 30日ごとの解除。

## 検証

- 単体: `test_risk_locks.py`、`test_trading_halts_api.py`、`test_bot_evaluation.py`、`test_backtest_replay.py`。
- ローカル DB(全部 rollback): 3連敗では発動せず、4連敗で発動して通知が2件届く。発動中は新規が拒否される。Owner の解除で
  連敗が0に戻り、解除後の3連敗では発動せず、4連敗でもう一度発動する。
- 実データのバックテスト(BTCUSDT 4h、2024-01〜2026-09): 解除なしでは取引10件・2024年9月で終わるが、30日解除では40件・2026年9月まで続く。1hは28件から119件。

## 既知の制限

- 解除後、新しい資産の記録(`account_snapshot`)ができるまで(次の約定まで)は、ピークが無く最大ドローダウンの確認が「履歴なし」で通る。
  解除した時点の資産を記録していない。
- ロックの halt は口座単位。1口座に Bot が複数あっても、1つの halt になる(現在は1口座=1 Bot)。
- バックテストの30日は固定。研究用の窓連結方式(`rolling-walk-forward.md`)とは別の近似で、完全には一致しない。
- 既存の `daily_loss_dd_limit`(日次・週次・ピークの**ハード**上限)の halt はそのまま残る。ピークのハード上限(10%)を超えると、
  この2つとは別の halt も発動し、緩和は自動(基準が戻れば段階的に下がる)。
