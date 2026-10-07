# 利用者の緊急停止

状態: 実装済み(2026-10-06)。通知は 2026-10-07 に接続した(`notification-wiring.md`)。

対象: 取引停止マトリクス(`concept/FXtrading_rebuild/05_アーキテクチャと移行計画.md`)の「利用者の緊急停止」、
FR-RISK-03/10、FR-UI-03、`04_API再設計.md` の `POST /bots/{id}/emergency-stop`、
05 §5「緊急停止はAPI障害時にも実行できる運用手順を用意する」。
ADR 0004 が「含まない」としていた残り8原因のうちの1つ。

## 決めたこと

| 論点 | 決定 |
|---|---|
| 範囲(「選択範囲」) | Bot単位と、ワークスペース全体の2つ。ワークスペース全体は `workspace` スコープのhaltを1つ置き、全Botを止める。口座単位は、1口座=1Botの現状ではBot単位と同じなので作らない |
| 権限 | 発動はOperator以上。解除はOwnerのみ(既存の `/trading-halts/{id}/emergency-release`)。解除してもBotは自動では再開しない |
| 決済の方針(「選択ポリシー」) | 発動時に `close_positions` で選ぶ。既定は建玉を残す。`true` なら停止を確定(commit)した後に、建玉ごとに成行の決済注文を出す。価格が無い等で決済できなくても、停止は取り消さない |
| 冪等 | 有効な緊急停止がある間に再度実行しても、何も変えず200を返す(`already_active: true`)。二重クリックで安全にするため |
| 効果 | 新規・増し玉の注文は `place_order` が拒否(workspaceスコープも確認する)。`start`/`resume` は `validate_bot_startup` が拒否する。決済注文は通る |
| 記録 | 1トランザクションで、halt(`emergency_stopped`、自動解除なし)・`SystemEvent`(`trigger_event_id`)・`AuditLog`(誰が・範囲・方針・停止したBot)を書く。通知は未接続 |
| API停止時 | `scripts/emergency_stop.py`(DBに直接。操作者は記録されず `operator_script`) |

## 実装

- `app/trading/application/emergency_stop.py`: `emergency_stop_bot` / `emergency_stop_workspace`。
- `trading_halt.HaltScope.scope_id` を `None` 可に(workspaceスコープ)。`find_active_halt` を公開。
- `order_flow.place_order`: workspaceスコープのhaltも確認。`cancel_order` に `commit` を追加。
- `bot_lifecycle`: `stop_bot` に `commit` を追加。`validate_bot_startup` が緊急停止中のstart/resumeを拒否。
- API: `POST /workspaces/{id}/bots/{bot_id}/emergency-stop`、`POST /workspaces/{id}/emergency-stop`。
- UI(Bot管理): 「全Botを緊急停止」・Botごとの「緊急停止」(確認ダイアログ、決済の選択)、緊急停止中の表示と解除ボタン。

## 検証

- 単体: `tests/test_emergency_stop.py`、`test_trading_api.py`、`test_bot_lifecycle.py`、`test_order_flow.py`、
  フロントの `TradingPanel.test.tsx`。
- ローカルDB(最後に全部rollback): 建玉あり→停止+決済、start拒否、新規注文拒否、再実行が冪等、解除後にstart可、
  ワークスペース全体で12 Botが停止し、以後のstartが拒否されることを確認。

## 既知の制限

- 通知: 2026-10-07 に配線済み(`notification-wiring.md`)。停止の発動は Owner と Operator に届く。
- 決済は直近の確定足の終値で約定する(ペーパーのシミュレーション)。価格が無いと決済できず、`close_failures` に残る。
- 解除は1つのhaltごと。ワークスペース全体の停止を解除しても、止まったBotは個別に開始し直す。
- Workerが評価の途中で停止と重なっても、`place_order` が注文時にhaltを見るので新規注文は通らない。
