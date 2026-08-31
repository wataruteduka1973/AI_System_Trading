# Durable Worker DB変更・移行計画

- 2026-08-31 / 設計後、②で追加revision `20260831_0005` とWorker用マッピングを実装。
  適用・切戻し試験は専用DBのみ。運用DBへの適用、旧job正規化、実行経路切替は未実施。
- 調査基準: commit `4597927`、リポジトリのAlembic head `20260825_0004`。
  実DBの現在revision・件数・制約差分は移行前に別途確認する。
- 処理契約: [Worker詳細設計](../modules/durable-market-data-worker.md)

## 1. 変更方式

②実装時の互換性判断: `app/models/catalog.py` に新列を直接追加すると、未移行の運用DBへ
現行APIがSELECT/INSERTする際に未存在列を参照してしまう。この段階では
`app/market_data/infrastructure/models.py` の独立metadataにWorker専用ORMを定義する。
旧ORMは変更せず、後続の切替時に統合を再検討する。新ORMはDDL生成用ではなく、
AlembicをDDL/制約の正とする。metadataからの自動差分migrationは使用しない。
現行migrationに合わせてWorkerのschemaはfx固定で、別schema設定なら起動前に拒否する。

既存テーブル・既存列を削除/改名しない。新規leaseテーブルと進捗列を追加する。
`candle`、`market_data_gap`、接続、資格情報、subscription.enabledのデータは移行で変更しない。
初期DDLは0001から参照されるため、履歴DDLを直接改変せず新Alembic revisionを追加する。
実装時にheadを再確認してrevision番号を決める。スキーマ名は現行migrationの `fx` に合わせる。

## 2. 新設 fx.market_data_lease

| 列 | 型 / NULL / 初期値 | 用途 |
|---|---|---|
| workspace_id | uuid / NOT NULL | workspace FK、ON DELETE CASCADE |
| instrument_id | uuid / NOT NULL | instrument FK、ON DELETE CASCADE |
| timeframe | text / NOT NULL | 現行7時間足CHECK |
| owner_id | uuid / NULL / NULL | Worker起動ごとのID |
| lease_token | uuid / NULL / NULL | claimごとのfencing token |
| lease_until | timestamptz / NULL / NULL | DB時計基準の期限 |
| heartbeat_at | timestamptz / NULL / NULL | 最終更新時刻 |
| work_kind | text / NULL / NULL | backfill または polling |
| work_id | uuid / NULL / NULL | 対象job/subscription ID |

主キーは `(workspace_id, instrument_id, timeframe)`。
CHECK: 所有情報6列はすべてNULL（空き）かすべてNOT NULL（取得中）。
CHECK: work_kindは上記2値、取得中はlease_until > heartbeat_at。
期限切れ検索index: `(lease_until)` WHERE lease_token IS NOT NULL。

work_idは2テーブルを参照するため多相FKは作らない。Applicationで対象行の存在と
Workspace・銘柄・時間足の一致をclaim/保存時に強制する。対象消滅時は結果を破棄してlease解放。
lease行を取得のたびにDELETEせず、解放時に所有6列をNULLにする。

## 3. fx.backfill_job 追加列

| 列 | 型 / NULL / 初期値 | 用途 |
|---|---|---|
| next_fetch_at | timestamptz / NULL / NULL | NULLはfrom_time、次区間のcursor |
| next_run_at | timestamptz / NOT NULL / CURRENT_TIMESTAMP | 取得・再試行可能時刻 |
| consecutive_failures | integer / NOT NULL / 0 | 連続失敗回数 |
| progress_report | jsonb / NOT NULL / '{}' | ページcommit済み集計 |

CHECK: next_fetch_at IS NULL または from_time <= next_fetch_at <= to_time。
CHECK: consecutive_failures >= 0、progress_reportはJSON object。
既存attempts/rows_written/validation_resultを維持する。rows_writtenは保存操作件数であり、
ユニークな足数ではない。重なり再取得の更新件数も含む。
progress_reportはversion=1、受信/insert/update件数、空区間件数/最大20サンプル、
最初/最後の足時刻を持つ。秘密やSDKレスポンスは入れない。集計とcursorは必ず同時commit。
旧active jobの既存rows_writtenはlegacy_rows_writtenとして集計の基準値を保持し、
過去のinsert/update内訳を推測しない。新しい保存操作件数を加算する。

due index: `(next_run_at, created_at, id)` WHERE status IN ('queued','running','validating')。
overlap index: `(workspace_id, instrument_id, timeframe, from_time, to_time)` WHERE
status IN ('queued','running','validating')。既存DBのindex重複を確認してから作成する。
queued/running/validatingは再試行待ちも含めて重複依頼を409にする。
現行の「5分経過したjobをAPI受付でfailedにする」処理は切替時に外し、lease回復へ一本化する。

## 4. fx.market_data_subscription 追加列

| 列 | 型 / NULL / 初期値 | 用途 |
|---|---|---|
| next_fetch_at | timestamptz / NULL / NULL | 巡回中の次区間 |
| scan_to | timestamptz / NULL / NULL | 今回の固定上端 |
| next_run_at | timestamptz / NOT NULL / CURRENT_TIMESTAMP | 次回実行時刻 |
| consecutive_failures | integer / NOT NULL / 0 | 連続失敗回数 |
| blocked_reason | text / NULL / NULL | 自動再試行を止めた安全な原因コード |

CHECK: cursorと上端は両方NULLか両方NOT NULLで next_fetch_at <= scan_to。
CHECK: consecutive_failures >= 0。blocked_reasonはNULLまたは空でない安全なコード。
due index: `(next_run_at, id)` WHERE enabled = true AND blocked_reason IS NULL。
既存のenabled/last_polled_at等とindexは維持する。blockedは利用者によるdisabledと区別する。
初期next_run_atは移行時に last_polled_at + poll_interval_seconds、未巡回はDB現在時刻で埋める。
enabled=falseの行にも同じ規則を使うが、Workerの取得対象にはしない。

## 5. 競合と整合性

lease取得は行ロックと条件付き更新を同一transactionで行う。空き/期限切れを確認した後、
対象状態を再確認し、所有列6個を一緒に更新する。DB書込中に外部APIを待たない。
保存時はtoken一致・期限内のlease行をロックし、対象cursorが読取時の値と一致することも確認する。
candle/gap変更の前に検査し、終端状態更新も同じtransactionに含める。
heartbeat・失敗処理・解放もtoken一致が必要。所有権を失ったWorkerは新所有者の状態を変更しない。
APIの手動受付用advisory lockと全時間足更新用lock、candle一意制約は残す。
同じ時間足で非重複の複数jobは受付可能だが、feed leaseで実行を直列化する。

## 6. 移行手順（停止時間あり）

1. DB接続先・revision・テーブル/制約を読取確認。DDL-only差分とactive job一覧を記録。
   DBバックアップを作り、別DBで復元確認する。秘密Storeは既存手順で安全に保全し表示しない。
2. 新規受付を止め、旧API全プロセスと旧poller/backfillを停止する。未完了の外部要求・DB処理が
   終了したことを確認する。旧版はleaseを知らないため、新旧同時稼働は禁止。
3. 新revisionで列・テーブル・CHECK・indexを追加する。短いlock_timeoutを設定し、
   ロックできなければ失敗させて再計画する。初期規模では停止中の通常index作成を使う。
4. 切替用の明示的な正規化処理をtransactionで行う（schema upgradeの副作用にしない）。
   旧queuedは維持、旧running/validatingは停止確認後にqueuedへ戻して同じIDで再開予定にする。
   cursor不明ならfrom_timeから再走査。attempts、依頼範囲、保存済みcandleを消さない。
   active jobのfinished_atをNULL、next_run_atをDB現在時刻にし、移行前後をAuditLogへ記録する。
   succeeded/failed/cancelledの状態と結果は維持し、既存failedを勝手に再実行しない。
   正規化は再実行可能とし、既に新方式cursorのある行は初期化しない。
5. APIを新方式（受付のみ）で起動。required revision未適用ならWorkerは取得を開始せず終了。
   additive schema自体は旧読取を壊さないが、旧書込プロセスへ戻してよいという意味ではない。
6. 最初はWorker1個を起動し、保持した依頼・設定を確認。既存enabled設定に従って取得が始まるため、
   本番相当DBでの初回起動はデータ取得を伴う操作として扱う。
7. 分離した検証DBでの障害試験成功後に通常運用へ。件数・重複・監査・再開位置を確認する。

## 7. 切戻し

優先は新schemaを残したままWorker停止・修正版への前進復旧。自動downgradeしない。
旧アプリへ戻す必要がある場合は全取得を停止し、旧孤児回復が新active jobを壊さないよう
active jobを明示的に終端化（監査付き）してから旧版を起動する。必要な再取得は新依頼とする。
再開位置とblocked状態を旧版は理解しないため、schema互換だけで安全な切戻しとはしない。
旧版pollerがblockedを無視する点も含め、対象の自動取得を利用者確認のうえ停止しておく。

列削除downgradeは進捗を失う。必要な場合だけバックアップ・復元確認・承認後に行う。
履歴0001/0004のdowngradeは既存データを削除するので、切戻し手順として呼ばない。
