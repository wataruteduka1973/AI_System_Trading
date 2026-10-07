# トレーディングWorkerの失敗の扱い

状態: 実装済み(2026-10-07)。

## 何が問題だったか

- Botの評価が例外になっても、ログに警告を出して次へ進むだけで、誰にも知らされなかった。
- Botの `failed` 状態は、どこにも設定されていなかった。
- 評価を1つのトランザクションにまとめた(`bot_evaluation`、2026-10-06)ため、失敗した評価は毎回の poll(5秒)で最初から再試行され続ける。
  止める仕組みも、知らせる仕組みも無かった。
- Workerが止まったことの検知は、画面側の計算(`botStaleness.ts`)だけで、サーバーには無く、通知できなかった。
- 停止(halt)中の Bot が新規のシグナルを出すと、`place_order` が `trading_halted` を投げ、上の再試行が**止まるまで毎回**続いた
  (評価をまとめる前は、1回失敗して終わっていた)。

## 決めたこと

| 論点 | 決定 |
|---|---|
| 失敗の数え方 | Botごとに連続した失敗を数える(Worker のメモリ内)。成功で0に戻る。**5回**続いたら `failed` にする(poll 5秒なら約25秒)。Worker を再起動すると数え直し、本当に壊れている Bot の停止が少し遅れるだけ |
| `failed` にする | `bot_lifecycle.fail_bot`: 未約定注文を取消、`BotRun` を `failed` で終了、`desired_state='stopped'`・`actual_state='failed'`(DBの制約上 `desired_state` に `failed` は無い)。原因の**種類**(例外の型名)だけを記録し、メッセージは記録しない |
| 復旧 | 原因を直したあと、普通の停止中のBotと同じく「開始」で新しい `BotRun` が作られる |
| 建玉 | 触らない。ただし損切りの監視は評価の中にあるので、失敗した Bot の建玉は**監視されなくなる**。通知にその旨を書く(`holds_position`) |
| DB障害 | 接続エラー(`OperationalError`/`InterfaceError`)は Bot のせいではなく、全Botが同時に失敗するので、数えない |
| 停止中の新規シグナル | `place_order` に投げさせず、結果(`action: "halted"`)として記録して終える。Risk Gate は先に走るので、halt の自動緩和は止まらない |
| Workerの死活 | Worker は評価を試みるたび(失敗でも)`bot_run.heartbeat_at` を更新する。**通知Worker**(別プロセス)が30秒ごとに確認し、稼働中のBotの最終更新が **180秒**(`TRADING_WORKER_STALE_SECONDS`)より古ければ、ワークスペースごとに `trading_worker_stalled` を1回知らせる |
| 同じ停止の再通知 | 知らせるのは1回の停止につき1回。その Bot の最終更新が前回の通知より新しければ(一度戻って、また止まった)、もう一度知らせる。「復旧した」通知は出さない |
| Workerの1回の失敗 | トレーディングWorkerの1回の pass が例外になっても、プロセスは終了せず、次の pass へ進む |

通知の宛先・経路・再送は `notification-wiring.md` のとおり(Owner と Operator、アプリ内とメール)。

## 実装

- `app/trading/application/bot_execution_loop.py`: `EvaluationFailureTracker`、失敗の記録、heartbeat。
- `app/trading/application/bot_lifecycle.py`: `fail_bot`。
- `app/trading/application/bot_evaluation.py`・`order_flow.py`: 停止中の新規シグナルを `halted` として終える(`entry_blocked_by_halt`)。
- `app/trading/application/worker_watchdog.py`: 死活の確認。`app/notifications/worker/__main__.py` が呼ぶ。
- `app/trading/worker/__main__.py`: pass の例外でプロセスを止めない。
- 画面: `failed` の Bot は「失敗(要確認)」と表示する(`TradingPanel.tsx`)。

## 検証

- 単体: `test_bot_execution_loop.py`、`test_bot_lifecycle.py`(`fail_bot`)、`test_worker_watchdog.py`、`test_bot_evaluation.py`、
  `test_trading_worker.py`、フロントの `TradingPanel.test.tsx`。
- ローカル DB(全部 rollback): 1つの Bot だけ評価が失敗する状態で5回 pass を回すと、その Bot だけが `failed` になり
  (`BotRun` も `failed`)、他の11の Bot は評価を続けて heartbeat が更新される。Owner に `[ERROR]` の通知が届く(例外のメッセージは含まれない)。
  heartbeat を10分古くすると、watchdog が1回だけ知らせ、2回目は知らせない。戻って、また止まると、もう一度知らせる。

## 既知の制限

- 失敗の数は Worker のメモリ内。Worker が失敗のたびに落ちて再起動を繰り返す場合は、`failed` にならない(その場合は watchdog が
  「止まっている」と知らせる)。
- watchdog は通知Workerが動いていることが前提。マシンごと止まった場合は、外からの監視が要る(このアプリには無い)。
- Worker の止まった時間が、180秒より長い pass(公開価格の取得が何度もタイムアウトする場合)を誤って「停止」と判定することがある。
  その場合は `TRADING_WORKER_STALE_SECONDS` を延ばす。
- 市場データ側(ローソク足の取り込み)の停止は、まだ通知しない。
- `failed` の Bot を直したあとの再開は、人が「開始」を押す。自動では再開しない。
