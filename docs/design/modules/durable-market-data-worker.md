# 独立Market Data Worker 詳細設計

- 設計日: 2026-08-31
- 状態: DB/leaseおよび区間取得Applicationを実装。既存経路との切替・独立Worker稼働は未実装。
- 対象: Horizon 1。OANDA Practice / Binance Spot Testnetの市場データのみ。
- DB契約: [DB変更・移行計画](../database/durable-market-data-worker.md)
- 実装順序・検証: [実装計画](../../plans/durable-market-data-worker.md)

## 1. 目的と境界

APIの再起動で取得を止めず、Worker中断後に保存済みの進捗から再開する。
FR-MD-04/06/08の品質・一意性・補完、FR-AUTH-04/08/10の監査・Workspace・秘密分離を維持する。
Bot、実注文、リアルタイム配信、Windowsサービス化、PC起動時の自動起動は対象外。
PC・DB・Workerが停止している間の取得や、取引所が提供しない履歴の復元は保証しない。

APIは認証、取得依頼/設定の保存、結果表示を担当する。WorkerはDBから仕事を取得し、
Applicationの取得ユースケースを呼ぶ。Redis等は導入せず、既存PostgreSQLを使用する。
APIのlifespan pollingとBackgroundTasks dispatchは切替時に削除する。

## 2. 現行との差分

- 現行のjob状態はDBにあるが、受付commit後の実行開始はプロセスローカル。
- `CandleIngestionService.sync` は複数ページを処理し、呼出元が最後にcommitする。
  したがって現状の進捗を、そのままページ単位の復旧位置と解釈してはいけない。
- 現行のowner advisory lockは実行中のjob保護。古いjobの回復は失敗扱いで、自動再開しない。
- 新方式はDB lease、ページ単位commit、再開cursor、期限付き再試行を導入する。
  同一Workspace・銘柄・時間足の手動backfillとpollingを共通leaseで直列化する。

## 3. 処理単位と所有権

②の実装は `app/market_data/infrastructure/leases.py` の `LeaseStore`。
claim/heartbeat/release/recover_expiredは各々短いtransactionを所有し、
guarded_writeは同一transaction内で書込と進捗を確定できるSession/対象行を渡す。
この境界の中で外部I/Oや独自commitを行ってはならない。権限・接続再検証および
実際のcandle保存/進捗更新は③の `ExecuteMarketDataPage` → `PageStore` で接続した。
準備時は `guarded_write(release=False)` で所有権を保持し、外部通信前にtransactionを閉じる。
現行pollerからは呼ばない。DB例外は呼出側へ伝え、そのtokenを再利用せず回復処理へ委ねる。
新規feed行作成時の一意キー待ちを避けるため、claimはfeed単位のtransaction try-advisory-lockも使う。
lease失効時の回復は同じtransaction内でflush後にdueを再検査し、再試行待機を飛ばさない。
待機30/120秒にjob/subscription ID由来の0〜5秒jitterを加える。正常releaseでは失敗回数を増やさない。

feedキーを `(workspace_id, instrument_id, timeframe)` とする。
新設 `market_data_lease` の1行がfeedの所有権を表す。取得ごとに新しいランダムUUIDの
`lease_token` を発行し、Worker起動ごとの `owner_id` と期限を記録する。
PIDは所有権判定に使わない。期限判定はDBの `clock_timestamp()` を使用する。

1. dueなjob/subscription候補を読む。この時点では実行確定ではない。
2. feed lease行を作成（競合時は既存行を利用）し、`FOR UPDATE SKIP LOCKED` で取得する。
3. leaseが空きまたは期限切れであること、対象のscope/状態/dueを再確認する。
4. 対象行もロックし、token・期限・対象種別/ID・開始状態を同じ短いtransactionでcommit。
5. transactionの外で外部APIから1ページだけ取得する。
6. 保存transactionでlease行を再ロックし、token一致・期限内・対象一致を確認する。
   不一致なら取得結果を破棄し、candle、job、subscription、gap、監査のいずれも書かない。
7. 対象行をロックし、データと進捗を同時保存してleaseを解放する。

ロック順序は常に feed lease → job/subscription → 既存gap用lock。
候補をjob行ロックしたままfeed取得を待つ実装は禁止する。heartbeat/recoveryも同じ順序。
外部通信中にDB行ロックを保持しない。heartbeatは別Session/connectionで短時間更新する。
heartbeatもtoken一致かつ期限内が条件。期限切れの自己延長は禁止。

lease行をロックした有効な保存transactionが先に始まった場合、そのcommit後に次所有者が
取得する。期限だけを見て、保存中の行ロックを無視した横取りはできない。
更新0件、DB切断、heartbeat喪失は所有権喪失として扱い、再接続後も古いtokenで保存しない。

保証は「有効な所有者だけが進捗を確定できる」こと。障害時の外部読取要求は再送され得る。
exactly-once通信は保証しない。candleの既存一意制約とupsertで二重行を防ぐ。
Workspaceをまたぐcandleは現行どおり共通データであり、別Workspace間の読取要求自体は
直列化しない。共通gap更新の既存lockとcandle一意制約は維持する。

## 4. 区間保存と再開

jobの依頼範囲 `[from_time, to_time)` は固定。`next_fetch_at` は「要求処理済み区間の上端」であり、
完全なデータが存在する時刻ではない。NULLはfrom_timeから開始する。
1ページは既存adapter上限以内（現状OANDA 4900、Binance 950足）。
最初以外のページは最大1足分を重ねて読み、区間の境界にかかった確定足を取りこぼさない。
時刻境界・閉場判定はadapterと既存calendar規則を使用し、OANDAの日足をUTC日付に再定義しない。

1回のcommitに、確定足upsert、`rows_written`増分、report累積値、次cursorを含める。
未確定足を保存しない。検証失敗や通信失敗時にはcursorを進めない。
正常な空レスポンスでは処理済み区間を進め、空区間件数と最大20件のサンプルを保存する。
空応答を「gap解消」「coverage完全」と解釈しない。source-limited/emptyは従来の品質判定に従う。

最後の区間保存後、全依頼範囲のgap/coverageを再検証してからsucceededにする。
終端cursorでもrunningなら、再取得せず最終検証を再開する。最終検証もlease fencing対象。
初期実装はDB検証transactionの時間上限を設け、heartbeatを長時間阻害する全件処理を避ける。
大範囲検証が上限内に終わらない場合は、範囲分割検証を実装するまで完成扱いにしない。

pollingも固定した今回の `scan_to` と `next_fetch_at` を持ち、長期停止後の追いつきをページ分割する。
新しい巡回の開始は保存済み最新確定足のclose（なければ現在から2足前）を基準とする。
終了後はcursor/scan_toをNULLに戻す。次巡回は1足重ねて開始し、前回未確定だった足を再確認する。
手動backfillとpollingをページ境界で交互に選択できるようにし、1年分の取得がpollingを占有しない。

## 5. 状態・失敗・停止

jobの公開状態はqueued/running/succeeded/failedを維持し、新しいretry_wait状態は追加しない。
既存DBが許可するvalidating/cancelledは履歴として保持する（新UI操作は作らない）。

| 事象 | job | polling |
|---|---|---|
| 初回取得 | queued → running、attempts増加 | enabledかつdueだけ取得 |
| ページ成功 | running維持、cursor保存、lease解放 | cursor保存、lease解放 |
| 全区間・検証成功 | succeeded、finished_at保存 | last_success_at更新、次巡回を予約 |
| 一時的通信障害 | queued、next_run_at予約、cursor保持 | failure回数と次回時刻更新、cursor保持 |
| lease期限切れ | 旧担当を失効、同じcursorで再予約 | 同様。enabled=falseなら予約しない |
| 恒久エラー/再試行上限 | failed、finished_at・安全なerror_code | blocked_reason設定。enabledは利用者意図として保持 |
| 正常なWorker終了 | 次ページを開始せず、確定済みcursorで再予約 | 同様、停止設定は変更しない |

`attempts`は初回・障害後の再実行開始回数。通常の次ページ、正常終了後の引継ぎでは増やさない。
`consecutive_failures`は失敗とlease失効で増加、ページ成功で0に戻す。正常終了は失敗に数えない。
初期方針は連続3回失敗で停止。1回目後30秒、2回目後120秒待つ（少量のjitterを加える）。
429は取得可能ならRetry-Afterを下回らない。安全に分類できないSDK例外を無条件に再試行しない。
認証失敗・資格情報不足/復号不能・接続/口座利用不可・入力不正・未知の内部エラーは即時停止。
DBに書けない障害ではlease期限に回復を委ね、失敗回数をメモリだけで確定したことにしない。

pollingのblocked解除は、利用者の開始操作で設定を再検証した後に行う。停止は秘密復号不要。
全時間足の開始/停止は既存の単一transactionを維持し、Workerはenabledを決してtrueへ変更しない。
停止前に開始済みの外部要求は即時キャンセルを保証しないが、保存前にenabledを再確認する。
停止済みなら結果を破棄し、leaseを返す。手動backfillの取消しとは別機能。
Workerは各ページ開始時にWorkspace・active銘柄・選択口座・verified接続を再検証し、
保存前にもアクセス失効を確認する。秘密はメモリ上のみ。生例外やtokenをAPI/監査へ出さない。

## 6. 初期運用パラメーターと監査

設計初期値: scan 2秒、lease 90秒、heartbeat 15秒、外部要求timeout 30秒、
DB保存/検証transaction目標上限10秒、終了猶予45秒、Worker内同時要求1件。
これらは性能実測前の値であり、設定化して試験する。外部SDKの同期threadをcancelしても
実通信が止まるとは限らないため、通信timeoutと保存時token検査の両方が必要。

claim/recovery/retry/blocked/completionを既存AuditLogに、Workspace・対象ID・相関ID・
旧/新状態・安全な原因コードとして保存する。heartbeatと各正常ページは監査を大量生成しない。
稼働中feedはlease heartbeat、成功/失敗は既存job/subscription欄から判断する。
アイドルWorkerの常時監視画面や新しい監視サービスは後続工程。

起動batは将来3プロセスを所有する。RはAPI/画面だけ、別キーAで全体再起動、Qで全体停止とする。
Workerが落ちても受付済み依頼はDBに残る。ランチャーはWorker終了を明示し、黙って稼働中と表示しない。
既存batの10秒終了猶予をWorkerにも流用しない。Workerの45秒猶予と整合させる。

## 7. 技術根拠

PostgreSQLのSKIP LOCKEDはロック中の行を飛ばすため、キュー取得に利用する。
通常のcoverage集計には使用しない。lease/fencing方式は本プロジェクトの設計であり、
PostgreSQLが自動で実装してくれる機能ではない。

- [PostgreSQL 16 SELECT — Locking Clause](https://www.postgresql.org/docs/16/sql-select.html#SQL-FOR-UPDATE-SHARE)
- [PostgreSQL 16 Explicit Locking](https://www.postgresql.org/docs/16/explicit-locking.html)
