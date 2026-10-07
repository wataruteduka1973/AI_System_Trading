# 通知の発行元の追加(市場データ・通知自体)

状態: 実装済み(2026-10-07)。`notification-wiring.md`(取引停止)、`worker-failure-handling.md`(Botの失敗・トレーディングWorkerの停止)に続く単位。

これで、次の出来事が Owner と Operator に知らされる。宛先・経路・再送は `notification-wiring.md` のとおり。

| 出来事 | イベント | 重大度 | 発行する場所 |
|---|---|---|---|
| 取引停止の発動・格上げ | `trading_halt.*` / `user_emergency_stop` | warning〜critical | `trading_halt.activate_or_escalate` |
| Botの評価が続けて失敗して停止 | `bot_failed` | error | `bot_lifecycle.fail_bot` |
| トレーディングWorkerの停止 | `trading_worker_stalled` | error | `trading/worker_watchdog` |
| ローソク足の自動取得(購読)が止まった | `market_data_stopped` | error | `market_data/infrastructure/stop_notice` |
| 過去データの取得(backfill)が失敗で終わった | `market_data_stopped` | warning | 同上 |
| 市場データWorkerの停止 | `market_data_worker_stalled` | error | `market_data/application/worker_watchdog` |
| 通知を再送の末に届けられなかった | `notification_delivery_failed` | error | `deliver_notifications` |

## 決めたこと

- **市場データの停止**: Worker が対象を諦める2か所(`pages.PageStore.fail` と `leases.LeaseStore.recover_expired`)で、
  購読が `blocked` になる、または backfill が `failed` になる**瞬間だけ**、その同じトランザクションで知らせる。
  再試行する失敗は知らせない。原因の種類(`authentication_failed` など)を日本語で書き、例外のメッセージは書かない。
- **まとめ**: 認証失敗などは7つの時間足が同時に止まる。同じワークスペース・銘柄・原因で1時間以内に知らせていれば、
  2件目以降は知らせない。
- **市場データWorkerの停止**: 通知Workerが30秒ごとに、有効で止まっていない購読の `next_run_at` が10分
  (`MARKET_DATA_WORKER_OVERDUE_SECONDS`)より過ぎていないか確認する。Worker は期限の来た購読を取り、`next_run_at` を先へ進めるので、
  大きく過ぎているなら誰も処理していない。トレーディングWorkerと同じく、1回の停止につき1回(一度戻ってまた止まれば、もう一度)。
  2026年9月に市場データWorkerが2週間止まって気づかれなかったことが、この確認の理由。
- **通知の打ち切り**: 再送を5回で諦めた(`failed` にした)とき、新しいイベント `notification_delivery_failed` を作り、元の文面と届かなかった経路を知らせる。
  届けるのは `in_app`(常に使える)。**このイベント自体が届かなくて打ち切られたときは、さらに知らせない**(自分の失敗を知らせ続けて終わらなくなる)。
  元のイベントに `SystemEvent` が無く打ち切った場合は、宛先のワークスペースが分からないので知らせられず、ログに残るだけ。

## 検証

- 単体: `test_market_data_stop_notice.py`(購読と backfill の文面・重大度、まとめ、市場データWorkerの停止の確認)、
  `test_deliver_notifications.py`(打ち切りの知らせ、最後でない再試行は知らせない、打ち切りの知らせ自体は繰り返さない)。
- 実 PostgreSQL: `test_worker_leases_postgres.py` に、実際の `PageStore.fail` と `recover_expired` が知らせること
  (再試行の間は知らせず、諦めたときに1回、機密値を含まない)を追加した。
- ローカル DB(全部 rollback): 市場データの購読14件が約3,200分(約53時間)遅れていることを検出し、2ワークスペースにそれぞれ1件の警告を作る。
  同じ原因の停止を2回知らせても1件にまとまる。この間、市場データ Worker は動いていなかった。

## 既知の制限

- 接続の認証失敗の検知は、取り込みが認証で失敗して購読が止まる(`authentication_failed`)ときの通知だけ。取引口座の接続を定期的に確かめる仕組みは無い。
- 市場データの停止を知らせるのは、止まった購読・失敗した backfill のみ。ローソク足の欠損(`market_data_gap`)は知らせない。
- 通知を届けられなかったことを、メールだけに頼る設定(アプリ内を見ない運用)では知る手段が無い。
- ストリーム(リアルタイム配信)の切断は、まだ知らせない。
