# リアルタイム市場データ配信 実装計画（Horizon 2）

- 2026-09-17: ①設計を作成。実装は未着手。
- 設計: [Module](../design/modules/realtime-market-data-stream.md)
- 対象: `docs/architecture-alignment-and-long-term-roadmap.md` の Horizon 2
  「リアルタイム観測と運用可視性」。開始条件（Durable Workerの安定稼働、履歴RESTの
  ギャップ補完の信頼性）は `durable-market-data-worker.md` の実装・実地確認により充足済み。

## 開始条件（充足確認）

- [x] Durable Workerが独立プロセスで稼働し、運用DBへ切替済み（`durable-market-data-worker.md` ⑤）。
- [x] 履歴REST（cursor pagination、IngestionReport、coverage判定）がPhase A/Bで完成している
  （`candle-chart-and-coverage.md`）。

## アーキテクチャ決定（2026-09-17、利用者確認済み）

取引所へのストリーム接続（OANDA `PricingStream` / Binance `kline_socket`）は、
**FastAPI APIプロセス内**でasyncio background taskとして保持する。独立Workerプロセスへは
置かない。

理由:

- OANDAのストリームはWebSocketではなくHTTP chunked stream（`oandapyV20.endpoints.pricing.
  PricingStream`）で、内部的にblockingなgeneratorのため`asyncio.to_thread`等で動かす必要がある。
  Binanceは`python-binance`の`BinanceSocketManager.kline_socket`（`websockets`+`aiohttp`前提）。
  いずれも「取引所と直接会話するadapter」という性質はexchanges層の既存設計と変わらない。
- ブラウザ側WebSocket終端も同じAPIプロセスに置くため、取引所stream→正規化→ブラウザ配信を
  同一プロセス内のin-memory pub-subで完結でき、Worker↔API間の新しいIPC（Postgres LISTEN/NOTIFY等）
  を導入せずに済む。ロードマップ6.2「当面採用しないもの」の「初期からのRedis/Kafka導入」を
  避ける方針にも合致する。
- Durable WorkerはHorizon 1で「claimして実行し終わるjob/purchase」を扱う設計（feed lease、
  heartbeat、fair round-robin）であり、常時接続でブラウザに直接pushし続けるリアルタイム配信とは
  ライフサイクルの性質が異なる。無理に同じ抽象へ寄せない。
- API再起動でストリームは切断されるが、確定足はWorkerが引き続きREST経由で取得・保存するため
  データが失われることはない。ブラウザ側は再接続時にREST gap-fillで補完する
  （`candle-chart-and-coverage.md`の「Future real-time data」節と同じ設計）。
- 単一Workspace・少人数運用を前提とするため、API複数プロセス化によるstream重複は現時点では
  対象外とする（将来複数プロセス化する場合は別途承認ゲートとして扱う）。

## 既存の維持事項

- 既存の履歴REST API、Worker、backfill/coverage/subscriptionは無変更で併存する。
- ブラウザは取引所に直接接続しない（既存方針を維持）。
- Owner tokenはWebSocket URLに含めない。短命one-time stream ticketで認可する
  （`candle-chart-and-coverage.md`の提案エンドポイントを踏襲）。
- 確定足のみをPostgreSQLへ永続化する。provisional（未確定）更新はチャート表示専用で、
  DBには保存しない。

## スコープ縮小方針（MVP）

- 配信対象timeframeは「フロントエンドが現在表示中の1本」に限定する（全7 timeframeの同時配信は
  行わない）。同じ(exchange, symbol, timeframe)を複数ブラウザが見ている場合のみ、
  取引所への接続を1本に共有する。
- OANDAは価格tick配信のみでtimeframe別candleを配信しないため、tickをtimeframeごとに
  bucketingしてprovisional candleを合成する正規化ロジックを新設する（②）。
  Binanceはtimeframe別のkline streamがネイティブに存在するため合成不要。この非対称性は
  設計上の既知差異として扱う（詳細は module設計の該当節）。
- Binance側は`python-binance`の`AsyncClient`/`BinanceSocketManager`経由が`aiohttp`に依存する。
  現行venvで`import binance`が`ModuleNotFoundError: No module named 'async_timeout'`で失敗する
  ことを確認済み（③実装前に依存関係を解消する。詳細は「未解決の技術的リスク」）。

## 作業単位

1. **設計（本ドキュメント・module設計）**: event/ticket形式、reconnect protocol、
   feedのライフサイクル（起動・共有・grace period・teardown）、OANDA tick→candle正規化方針を定義。
2. **Stream ticket発行**: `POST /workspaces/{workspace_id}/market-stream-tickets`。
   Workspace・instrument・timeframe・exchange・選択口座のverified状態を確認し、短命
   （60秒程度）・one-time・署名付きticketを発行する。DBテーブルは追加せず、APIプロセス内の
   in-memory replay防止setで一意性を保証する（ticket検証もAPIプロセス内で完結するため可能な
   設計。既存`PyJWT[crypto]`を採用し、`pyproject.toml`へ追加する）。
3. **正規化とin-memory pub-sub、OANDA adapter**: 取引所tickを`event_id`/`sequence`付き
   provisional/finalized eventへ正規化し、feedキー（exchange, symbol, timeframe）単位で
   購読者へfan-outする。OANDA `PricingStream`をまず接続し、tick→candle合成を実装・試験する。
4. **Binance adapter**: `BinanceSocketManager.kline_socket`を接続し、同じ正規化・pub-sub経路へ
   接続する。依存関係（aiohttp/async_timeout/websockets）を解消し、pyproject.toml/requirements.txt
   の両方を更新する。
5. **ブラウザWebSocket終端**: `WS /ws/v1/market-stream?ticket=<ticket>`。ticket検証、
   feed購読、heartbeat、切断時のfeed参照カウント減算、grace period後のupstream teardownを実装。
6. **reconnect/gap-fill**: クライアント側で`last_sequence`を保持し、再接続時は新しいticketを
   要求し、feedがまだ生存していれば直近ring bufferから再送、feedが再生成されていれば
   最新確定足時刻からのREST gap-fillにフォールバックする。
7. **フロントエンド**: `CandleChart`へ`series.update(...)`によるprovisional更新を追加し、
   接続状態（connected/reconnecting/delayed/disconnected）、直近データ時刻、gap件数、
   Worker heartbeat状態を画面表示する（`App.tsx`または将来のfeature分割後のmarket-data feature）。
8. **耐障害性・運用可視性の試験**: 切断/再接続、重複、順不同、clock skew、backpressure、
   24時間soak testを専用環境で実施する。

## 受入試験

| ID | 条件 | 合格条件 |
|---|---|---|
| RT-01 | ticket発行直後にWS接続 | 認可され、feedへ購読される |
| RT-02 | ticketの期限切れ後にWS接続 | 拒否され、feed情報が漏れない |
| RT-03 | 同じticketで2回目のWS接続 | 拒否される（one-time） |
| RT-04 | 同じ(exchange, symbol, timeframe)を2ブラウザが購読 | 取引所接続は1本のみ、双方に同じeventが届く |
| RT-05 | 最後の購読者切断 | grace period後にupstream接続がteardownされる |
| RT-06 | grace period内に再購読 | 新しいupstream接続を張らず既存feedを再利用する |
| RT-07 | WS切断→再接続（feed生存中） | ring bufferから欠損なく再送される |
| RT-08 | WS切断→再接続（feed消滅後、API再起動想定） | REST gap-fillで欠損なく補完される |
| RT-09 | OANDA tick列からのcandle合成 | timeframe境界を正しく検出し、確定前後でevent種別が変わる |
| RT-10 | Binance kline streamの`is_closed=false/true` | provisional/finalizedへ正しく分類される |
| RT-11 | 通信断・429・認証失敗 | 安全なエラーコードでfeedがdelayed/disconnected表示になり、秘密漏えいなし |
| RT-12 | 異なるWorkspace/失効口座 | 他Workspaceのfeed/秘密へアクセスできない |
| RT-13 | 重複event・順不同event | sequenceにより重複排除・順序復元される |
| RT-14 | 24時間soak test | メモリ増大、再接続ループ、event滞留が許容範囲内 |

## 未解決の技術的リスク

- `python-binance`のAsync/WebSocket機能が現行venvで`async_timeout`欠如によりimport不能
  （③実装前に解消要。`async_timeout`を明示依存へ追加するか、aiohttp/python-binanceの
  バージョン調整で不要にできるか要調査）。
- OANDA `PricingStream`はblocking generatorのため、`asyncio.to_thread`または専用threadでの
  実行方式を②で確定する。
- OANDA Practice実データでの動作確認は、既存のHorizon 0の制約（APIキー生成不可）が
  解消されない限り引き続きNOT VERIFIEDとなる可能性がある。

## 完了ゲートと既知制限

各単位で既存＋追加テスト、変更箇所format/lint、型検査、frontend build/lintを実施する。
UIを変更する単位（⑦）はブラウザ試験を行う。未実行検証を成功に読み替えない。
Durable Worker（①〜⑤）と同様、専用PostgreSQL・架空のexchange応答での回帰試験を優先し、
運用DBや実取引所APIへは明示的な承認・接続確認の後にのみ接続する。
