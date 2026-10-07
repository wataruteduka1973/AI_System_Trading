# 通知の配線(取引停止を知らせる)

状態: 実装済み(2026-10-07)。アプリ内通知の API(一覧・既読)は 2026-10-07 に追加。画面は次の単位。

対象: ロードマップ Horizon 5 の「Event Log、Outbox、Notification Worker」。土台(Outbox/Notification の ORM、
SMTP アダプタ、配信ループ、Worker)は Horizon 5 グループ D(Unit 8)で作ってあったが、**発行元が1つも無く、
配信の失敗は再送されず、受信者も決まっていなかった**。この単位で、最初の発行元(取引停止)と、再送・重複防止、
受信者を足した。

## 決めたこと

| 論点 | 決定 |
|---|---|
| 何を知らせるか | 取引停止(`trading_halt`)の**発動と格上げ**。Risk Gate の自動停止(データ遅延、日次・週次損失・最大DD)と、利用者の緊急停止。緩和・解除は知らせない。同じ停止が続いている間の再判定も知らせない |
| 発行の仕方 | `trading_halt.activate_or_escalate` が、停止を作る・重くする同じトランザクションで `SystemEvent` と `OutboxEvent`(共通の `correlation_id`)を書く(`publish_event.publish_system_event`)。停止が rollback されれば通知も消える |
| 重大度 | `warning`/`entry_halted` → warning、`all_trading_halted` → error、`emergency_stopped` → critical |
| 誰に | そのワークスペースの Owner と Operator(動かせる役割)。Viewer と無効なアカウントには送らない |
| 経路 | `in_app`(常に使える)と `email`(SMTP が設定されているときだけ)。SMTP が無くても Worker は動き、アプリ内の通知だけ作る。メールは宛先が無効でも「失敗」ではなく「対象外」 |
| 再送 | 全員に届くまで `published` にしない。失敗したら `pending` のまま、待ち時間(30秒×2^回数)を置いて再試行し、5回で `failed`。再試行は、届いていない相手にだけ送り、失敗した `notification` 行を使い回す(`delivery_attempts` が試行回数を数える) |
| 文面 | 件名 `[重大度] 本文`、本文に原因・範囲・レベル・ID などの `payload`。認証情報や取引所の生の応答は入れない |

## 実装

- `app/notifications/application/publish_event.py`: 発行(commit はしない)。
- `app/trading/application/trading_halt.py`: `activate_or_escalate` が発動・格上げを知らせる(`HaltEvent` で原因ごとの文言も渡せる)。
  緊急停止(`emergency_stop.py`)は自分の文言を渡す。
- `app/notifications/application/deliver_notifications.py`: 再送・重複防止・チャネルごとのアダプタ。`available_at` が来た行だけ取る。
- `app/notifications/application/recipients.py`: 受信者の決め方。`adapters/in_app.py`: アプリ内チャネル(送る処理は無い)。
- `app/notifications/worker/__main__.py`: SMTP を任意に。`scripts/start_local.py` が通知 Worker も起動する。

## アプリ内通知の API(2026-10-07)

自分宛(`recipient_ref` が自分のユーザーID)の `in_app` 通知だけを扱う。他人の通知は、同じワークスペースの人のものでも 404。
読めるのはワークスペースのメンバー全員(Viewer 以上)だが、通知が届くのは Owner と Operator だけ。

- `GET /workspaces/{id}/notifications?unacknowledged_only=&limit=`: 新しい順。各件にイベントの重大度・分類・文面・`payload`・発生時刻、
  `status`(`sent`=未読、`acknowledged`=既読)。`unacknowledged_count` は `limit` に関係なく、そのワークスペースの未読の総数。
- `POST /workspaces/{id}/notifications/{notification_id}/acknowledge`: 既読にする(いつ・誰が)。再実行しても変わらない(200)。
- `POST /workspaces/{id}/notifications/acknowledge-all`: 自分の未読をすべて既読にし、件数を返す。

## 検証

- 単体・結合: `tests/test_deliver_notifications.py`(MagicMock と実 PostgreSQL)、`test_trading_halt.py`、`test_emergency_stop.py`、
  `test_notification_worker.py`。実 PostgreSQL で、停止の発動→Owner と Operator だけに届く(Viewer と無効なユーザーには届かない)、
  メール障害→再送でアプリ内は二重に送らない、格上げは再通知・同じ重さの再判定は通知しない、を確認した。
- ローカル DB(全部 rollback): 緊急停止 → Outbox 1件 → 通知 Worker の処理 → Owner にアプリ内とメールが各1件、を確認した。

## 既知の制限

- **画面は未実装。** アプリ内通知を見る API は `app/api/routes/notifications.py`(下記)。それを使う画面は次の単位。
  それまでは、API を直接呼ぶか、メール(SMTP の設定が要る)で確かめる。
- 発行元の一覧は `notification-sources.md`(取引停止、Botの失敗、Workerの停止、市場データの停止、通知の打ち切り)。接続の認証失敗の定期確認などは、まだ無い。
- データ遅延の停止が出たり消えたりすると、そのたびに知らせる(まとめる処理は無い)。
- メールの再送は5回(約15分)で打ち切る。打ち切りは `notification_delivery_failed` として知らせる(`notification-sources.md`)。
- 既存の通知がある `event_id` に対して `(event, channel, recipient)` で重複を判定する。受信者の役割が途中で変わった場合は、
  その時点の役割で決まる。
