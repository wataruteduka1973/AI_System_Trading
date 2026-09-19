# リアルタイム市場データ配信 実装計画（Horizon 2）

- 2026-09-17: ①設計を作成。実装は未着手。
- 2026-09-17: ②Stream ticket発行を実装（`POST /workspaces/{workspace_id}/market-stream-tickets`）。
  `app/market_data/infrastructure/stream_tickets.py`（PyJWT署名・検証・one-time-use guard）、
  `app/market_data/application/stream_tickets.py`（`PageAccess.resolve`を再利用した認可、
  `MarketDataApplicationError`への変換）、`MarketStreamTicketCreate`/`MarketStreamTicketRead`
  スキーマ、`market_stream_ticket_secret`/`market_stream_ticket_ttl_seconds`設定を追加。
  `PyJWT[crypto]`を`pyproject.toml`へ追加（`requirements.txt`には既に記載済み）。
  ruff format/lint（既知のI001誤検知を除く）はローカルで確認済み。pytest/mypyの実行は
  NOT VERIFIED（ローカル実行環境にPython 3.13の依存関係一式がないため。CI結果で確認する）。
  WebSocket終端（⑤）はticketの検証・one-time-use消費までは実装済みだが、実際のWS接続処理は
  未実装（次単位）。
- 2026-09-17: ③OANDA adapter（tick→candle正規化 + in-memory pub-sub）を実装。
  `app/market_data/infrastructure/candle_stream.py`（`FeedHub`: feedキー単位のライフサイクル、
  RT-04/05/06のsharing/grace-period-teardown/resubscribe-reuse、ring bufferによるbacklog、
  gap_notice報告）、`app/market_data/infrastructure/oanda_stream.py`（`PricingStream`を
  専用threadで駆動し`loop.call_soon_threadsafe`でevent loopへ橋渡しする`OandaFeedWorker`、
  UTC epoch境界でのtick→candle正規化`TickToCandleNormalizer`、RT-09のbucket確定検出）を追加。
  `tests/test_candle_stream.py`（7ケース）、`tests/test_oanda_stream.py`（13ケース）を追加。
  ローカル実行環境にpytest/ruff/mypyをインストールできなかったため（両shellともPyPIへの
  egressが403で拒否される）、公式のpytest CLI・ruff・mypyそのものの実行はCI結果待ち
  （NOT VERIFIED）。ただし、Windows側`.venv313`の実際にpin済みoandapyV20パッケージを
  クラウド側へ個別ファイル転送して組み立てたモジュールmirrorに対し、上記20テスト関数
  すべてをプレーンなPythonスクリプトとして実行し全件成功を確認した（テストロジック自体の
  実機能検証は完了。pytest CLIというランナーの実行のみが未検証）。この検証の過程で
  `FeedHub._start_feed`の実装順序バグ（starter起動時の同期publishがfeed未登録により
  ring bufferへ届かず消える）を実際に検出・修正し、push前に解消済み。
- 2026-09-17: ④Binance adapter（`BinanceSocketManager.kline_socket`接続）を実装。
  `app/market_data/infrastructure/binance_stream.py`を追加。Binanceのkline streamは
  provisional/finalized candleをtimeframe単位でネイティブに配信するため（`k.x`の
  is_closedフラグで判別）、OANDAと違いtick→candle合成は不要（設計doc section 6の通り）。
  `BinanceSocketManager`は完全にasyncio nativeなAPIであり`PricingStream`のような
  blocking generatorではないため、OANDA版（専用thread + `loop.call_soon_threadsafe`）とは
  異なり、`BinanceFeedWorker`は単純な`asyncio.Task`としてFeedHubと同じevent loop上で
  動作し、`FeedSink`メソッドを直接呼び出す（thread/call_soon_threadsafeのブリッジは
  不要）。
  訂正: 作業単位④の当初の記述は「`websockets`を自コードから直接importする最初の単位に
  なるためpyproject.tomlへ追加する」としていたが、実装してみると`BinanceSocketManager`/
  `ReconnectingWebsocket`が接続・エラー処理を完全に抽象化しており、生の`websockets`
  例外や型がアダプタ側コードへ一切露出しない（接続断は`{"e": "error", ...}`辞書message
  または`binance.exceptions.ReadLoopClosed`として現れる）ことが判明したため、
  `websockets`の直接importは発生せず、`pyproject.toml`への追加は不要だった。
  `tests/test_binance_stream.py`（9ケース）を追加。ローカル実行環境にpytest/ruff/mypyを
  インストールできなかったため（両shellともPyPIへのegressが403で拒否される）、公式の
  pytest CLI・ruff・mypyそのものの実行はCI結果待ち（NOT VERIFIED）。ただし、
  python-binanceの実際にpin済みのバージョン（Windows側`.venv313`）をクラウド側へ転送し、
  Windows専用のコンパイル済み拡張（aiohttp/websockets/multidict/yarl/frozenlist/
  pycryptodomeがいずれもwin_amd64向け`.pyd`を持ち、Linux上では実行不能）に依存する
  部分のみ最小限のfake stubへ置き換えることで、python-binance本体の実コード
  （`AsyncClient`/`BinanceSocketManager`/`ReconnectingWebsocket`等）を実際にimportした
  状態で上記9テストを実行し全件成功を確認した（実際のネットワーク接続自体は
  client_factory/socket_manager_factoryの差し替えにより行わず、コード自体の実行パスを
  検証）。
- 2026-09-17: ⑤ブラウザWebSocket終端（`WS /ws/v1/market-stream?ticket=<ticket>`）と
  ⑥reconnect/gap-fillを実装。feedの参照カウント減算・grace period後のupstream teardown
  自体は③で実装済みの`FeedHub.subscribe`/`FeedSubscription.close`がそのまま提供するため、
  ⑤で新たに実装したのは接続のオーケストレーションのみ。
  `app/market_data/infrastructure/stream_connection_access.py`（新規）: ticketは
  workspace_id/exchange/symbol/timeframeのみを保持しinstrument_idを持たないため、
  `PageAccess.resolve`（②③、instrument_id起点）を再利用せず、(workspace_id, exchange)
  起点でExchangeConnection/ExternalAccountを直接解決する専用resolverを新設した。WS接続時にも
  再度この解決を行うことで、設計doc section 4の「Workspace境界はticket発行時とWS接続時の
  両方で確認する」を実装した（RT-12: ticket発行後に接続無効化・口座変更があった場合、
  ここで拒否される）。
  `app/market_data/infrastructure/stream_protocol.py`（新規）: browser向けevent JSON
  encoding（`workspace_id`をここで初めて付与）、per-connection heartbeat（FeedHubの
  feed単位sequenceを消費せず、直前のsequenceを変更せず繰り返すことでクライアントの
  gap検出を乱さない設計）、および⑥のreconnect/resume判定（`decide_resume`: fresh /
  replayed / gap_fill_required の3モード、RT-07/RT-08）を実装。
  `app/market_data/infrastructure/stream_session.py`（新規）: 実際のfastapi.WebSocketに
  依存しない、`Transport`という小さなProtocolを介した接続オーケストレーション本体
  （ticket検証→credential解決→`FeedHub.subscribe`→resume判定→heartbeat/event送信loop）。
  fastapi非依存にしたことで、後述の検証がpytestで直接可能になった。
  `app/api/routes/market_stream_ws.py`（新規）: 実際の`fastapi.WebSocket`を`Transport`へ
  変換し`run_stream_session`へ渡す薄いアダプタと、DB資格情報解決からFeedStarter
  （OANDA/Binance）を構築する`_build_starter`。`app/main.py`へ`include_router`を追加
  （`/api/v1`配下ではなく設計doc通り`/ws/v1/market-stream`に直接マウント）。
  `app/core/config.py`へ`market_stream_grace_period_seconds`/
  `market_stream_heartbeat_interval_seconds`（両方デフォルト30秒、設計doc section 3の
  「既定30秒、設定可能」に対応）を追加。新規の外部パッケージ依存は無し（fastapi/
  structlog/sqlalchemy/PyJWTはいずれも既存依存の再利用）。
  `tests/test_stream_connection_access.py`（11ケース）、`tests/test_stream_protocol.py`
  （12ケース）、`tests/test_stream_session.py`（6ケース、fakeなTransport/FeedStarterを
  用いてticket検証・access拒否・feed起動失敗・切断後のgrace period teardown・heartbeat
  発火までの接続オーケストレーション全体を統合的に検証）を追加。この29ケースは、
  ④までとは異なりpytest自体を含めて実際に実行し全件成功を確認した
  （`pytest`本体・`_pytest`・`pluggy`・`iniconfig`・`packaging`・`py`はいずれもコンパイル
  済み拡張を持たない純Pythonパッケージであることを確認し、Windows側`.venv313`から
  個別ファイル転送して実行環境を構築した。同様にSQLAlchemy 2.0.52もcyextension
  （5ファイルのみ、いずれも純Python実装へのfallbackを持つ最適化用オプション拡張）を
  除外して転送すれば純Python環境で問題なくimportできることを新たに確認し、
  `app.models.connections`/`app.models.workspace`等の実際のORMモデル定義・実際の
  `MarketDataAccessError`・実際の`FeedHub`・実際のPyJWT ticket発行/検証を、モックは
  使わず本物のコードとして読み込んだ状態でテストを実行した。モックが必要だったのは
  `app.core.config`（pydantic-core依存、Rust製コンパイル拡張でLinux向けビルドが
  この環境に存在しない）とcryptography（同様にコンパイル拡張）の2箇所のみで、
  それぞれ最小限のfakeモジュールに置き換えた）。
  一方、`app/api/routes/market_stream_ws.py`自体（実際の`fastapi.WebSocket`を使う薄い
  アダプタ部分）はfastapi/starlette自体がpydantic-coreに依存するため、この環境では
  importも実行もできなかった（NOT VERIFIED、CI結果待ち）。この部分は
  `tests/test_market_stream_ws.py`として既存の`test_market_stream_ticket_api.py`
  （②）と同じ`TestClient`/`monkeypatch`/`dependency_overrides`の慣習に沿ってテストを
  作成済みだが、ローカルでは未実行。オーケストレーション本体（`stream_session.py`）を
  fastapi非依存に切り出したことで、検証できない範囲をこの薄いアダプタ1ファイルのみに
  最小化した。
  フロントエンド（⑦）は未着手。
- 2026-09-17: ⑦フロントエンド（`CandleChart`のprovisional更新、接続状態表示）を実装。
  `app/api/routes/market_stream_ws.py`（⑤）・`stream_protocol.py`（⑥）が定義する
  event/ticket契約（設計doc section 5/7）をそのままクライアント側で消費する。
  `frontend/src/features/market-data/marketStream.ts`（新規）: WS/Reactに依存しない
  純粋な protocol 層。event JSONのparse、`stream_state`envelopeとevent messageの
  判別、provisional/finalized eventから既存`ChartCandle`形状への変換（`series.update()`へ
  そのまま渡せるようにするため、REST candle APIと同じ形にした）、接続状態ラベル、
  reason_codeの日本語ラベル化（未知のcodeはそのまま表示しフェイルセーフとする）、
  heartbeat無音検知（既定30秒間隔の1.5倍=45秒を閾値としたMVP簡略化。実際の
  `market_stream_heartbeat_interval_seconds`設定値は現時点でブラウザへ渡していない）、
  WS URL構築（`resume_last_sequence`/`resume_feed_started_at`はサーバ側`ResumeRequest.
  is_present`と同じく両方揃った時のみ付与）、close code判定（サーバ側`stream_session.py`の
  `CLOSE_TICKET_REJECTED=4401`/`CLOSE_ACCESS_DENIED=4403`のみ再試行しない。それ以外
  （`CLOSE_FEED_START_FAILED=1011`含む）は通常の切断と同様に再接続する）を実装。
  `frontend/src/features/market-data/useMarketStream.ts`（新規）: 実際のWebSocketの
  接続・再接続オーケストレーション本体。ticket発行APIを呼び、WSを開き、
  `stream_state`で`gap_fill_required`を受けた場合は既存の`reloadMarketData`
  （`useMarketData.ts`へ追加）でREST再取得を発火する。ticket発行失敗・WS切断は
  いずれもbackoffの上で再接続する（`isRetryableClose`がfalseを返す場合のみ諦めて
  `disconnected`表示に留める）。
  `frontend/src/components/CandleChart.tsx`: `liveCandle`（省略可、既定null）propを追加。
  変更時に`series.setData(...)`は呼ばず`series.update(...)`のみを呼ぶことで、
  5秒ごとのREST再読込（既存の確定足取得）とは別経路でチャート最新バーだけを
  即時更新する。lightweight-chartsが非単調な更新（チャートが既に進んだ後に来た
  古いevent）を例外で拒否するケースはtry/catchで無視する（次のeventで復帰する）。
  `frontend/src/features/market-data/MarketDataPanel.tsx`・`App.tsx`: 接続状態
  （connecting/connected/reconnecting/delayed/disconnected）、直近データ受信時刻、
  gap件数、直近の遅延理由を表示する`stream-status`ブロックを追加し、`useMarketStream`を
  `App.tsx`で呼び出して結果を渡す。配信は市場データ画面表示中（`route.kind === 'market'`）
  のみ有効化し、接続管理画面等では取引所へのstream接続を張らない
  （スコープ縮小方針「表示中の1本に限定する」に対応）。

  検証状況（正直な開示）: `npx tsc -b --noEmit`と`npx eslint .`は、Windows側で
  実際にインストール済みのnode_modulesをそのまま使い、変更後の全ファイルに対して
  実際に実行し両方エラーなしを確認した（型検査・lintは本物の実行）。一方、
  `npx vitest run`（`frontend/src/features/market-data/marketStream.test.ts`新規12
  ケース、および`CandleChart.test.tsx`へ追加した2ケース含む）はこの環境では実行
  できなかった。原因はテストの内容ではなく、vitestが内部で使うVite 8のbundler
  `rolldown`のネイティブbinding（`@rolldown/binding-win32-x64-msvc`のみWindows側で
  インストール済みで、Linux版`@rolldown/binding-linux-x64-gnu`は未インストール）が、
  このLinux実行環境と一致しないため（`tsc`/`eslint`自体は純粋なJavaScriptパッケージで
  ネイティブ拡張を持たないため問題なく動作した）。npm registryへの新規fetchで
  この1パッケージのみを補うことも試みたが、この環境ではnpm registryへの新規
  egressそのものが拒否される（クラウド側サンドボックスでも同様に拒否された）ため
  断念した。したがってテストコード自体は既存の`CandleChart.test.tsx`と同じ
  vitest/testing-libraryの慣習で作成済みだが、実行結果はNOT VERIFIED（CI結果待ち）。
  Windows側の実開発環境（利用者の`.venv313`相当、node_modulesがネイティブに一致する
  環境）であれば`npm test`はそのまま実行できるはずなので、利用者側での実行を推奨する。

  耐障害性・運用可視性の試験（⑧、24時間soak test等）は実環境接続が前提のため未着手。
- 2026-09-19: ⑧の最初の24時間soak testを`scripts/stream_soak_monitor.py`で実施し
  （`soak_test_log.jsonl`）、2件の耐障害性欠陥を発見・修正した。
  1. **Binance stream切断後、再接続が一度も行われない**: `BinanceFeedWorker`は
     `report_failure`でgap_noticeを発行した後、taskをそのまま終了していた。実行中に
     upstream側のqueue overflow（python-binance `ReconnectingWebsocket`が内部queueの
     溢れを`{"e": "error", ...}`通知として`recv()`へ注入する既知の経路）が発生し、
     この経路で`report_failure`は正しく呼ばれたが、その後何も再試行しなかったため、
     feedが約4時間、サイレントに`disconnected`状態のまま放置された
     （ログ07:31:15の`gap_notice`以降、11:31:14にAPIプロセスが再起動されるまで
     `candle_finalized`/`provisional_update`が一切増えていない）。
     修正: `BinanceFeedWorker`に指数backoff+jitter（`compute_reconnect_backoff_seconds`、
     初期1秒・上限60秒・equal jitter）付きの再接続loopを追加した。接続確立後に
     一度でもメッセージを受信していれば次回失敗時のattemptを0へ戻す（一時的な瞬断と
     継続的な障害を区別する）。3回連続で接続に失敗するまではFeedState`delayed`、
     それ以降は`disconnected`とし、再接続自体は`stop()`（feed teardown）以外では
     止まらない。
  2. **メッセージが一切来ない「本当に無音」な切断を検知できない**: 上記1の経路は
     python-binance自身が何らかのエラー通知を`recv()`へ注入した場合のみ機能する。
     Binance側が通知すら送らずに応答を止める、より悪いケースを想定し、
     `stream.recv()`を`asyncio.wait_for(..., timeout=silence_timeout_seconds)`
     （既定90秒。design doc section 6の通りkline streamは通常1〜2秒間隔で配信される
     ため十分な余裕を持つ）でラップし、無応答が続けば`binance_stream_silent`という
     専用reason_codeで同じ再接続経路へ合流させる（ウォッチドッグ）。
  3. **`scripts/stream_soak_monitor.py`自身の再接続にもbackoffがなかった**:
     ログ11:31:19〜11:34:56の間、拒否された接続（`WinError 10061`、APIプロセス自体が
     停止していた）に対し固定3秒間隔で再試行し続けていた（12分強で90回以上）。
     試験ツール側にも同じ指数backoff+jitterを実装（appの`binance_stream.py`とは
     意図的に非同期import不可のため関数を複製、モジュールdocstring参照）した。
  4. **受信キューのバックプレッシャー化**: `candle_stream.FeedHub`の購読者queueは
     従来無制限（モジュールdocstringで明示済みの既知の未対応事項）だった。
     `subscriber_queue_maxsize`（既定1000）で有界化し、溢れた場合は例外を投げず、
     また古いeventを黙って捨てる（mid-session sequence gapを検知するclient側実装が
     存在しないため、これは無音の欠損になり得る）のでもなく、`SubscriberOverflow`
     sentinelを押し込んで当該購読者だけをforce-disconnectする。切断されたclientは
     既存のreconnect + REST gap-fill経路（RT-08）で自然に復旧するため、新しい種類の
     検知不能なgapを発明せずに済む。他の購読者のfan-outには影響しない。
  5. **構造化ログ**: `binance_stream.py`/`candle_stream.py`にstructlog
     （`app/core/logging.py`、既存基盤を再利用）を配線し、feed開始/失敗/回復/teardown、
     再接続試行のattempt/backoff_seconds/code、購読者queue overflowを記録した。
  6. 利用者からの要望のうち「DB書き込みのバッチflush」は、ストリーム側（表示専用、
     確定足の永続化はWorkerの`ExecuteMarketDataPage`が独占する既存アーキテクチャ決定）に
     新設するとWorkerの経路と並行するDB書き込み経路が生まれるため、意図的に対象外とした
     （利用者判断待ちのまま今回はスキップ）。ストリーム側は現状も一切DBへ書き込まない。
  検証: `tests/test_binance_stream.py`（再接続2件、backoff純関数2件を追加）、
  `tests/test_candle_stream.py`（queue overflow 2件を追加）、
  `tests/test_stream_session.py`（overflow→force-disconnect 1件を追加）。
  既存29+9+38+13ケースを含む変更ファイル関連の非DB試験は全件成功（新規9件含む）。
  Ruff lint/format、mypy（該当3ファイル）はいずれもクリーン。24時間soak testの
  再実行によるこの修正自体の実地確認はNOT VERIFIED（次回soak testで確認する）。
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
- Binance側は`python-binance`の`AsyncClient`/`BinanceSocketManager`経由が`aiohttp`に依存するが、
  `app/exchanges/binance.py`が既に`from binance import AsyncClient`でこの依存経路を使っており、
  既存CI（Python 3.13.15）で問題なく動作している。追加の依存解消は不要（2026-09-17訂正、
  詳細は「未解決の技術的リスク」）。

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
   接続する。`BinanceSocketManager`は完全にasyncio nativeなAPIのため、OANDA版のような
   専用thread + `loop.call_soon_threadsafe`ブリッジは不要（実装確認済み、詳細はstatus log）。
5. **ブラウザWebSocket終端**: `WS /ws/v1/market-stream?ticket=<ticket>`。ticket検証、
   feed購読、heartbeat、切断時のfeed参照カウント減算、grace period後のupstream teardownを実装。
6. **reconnect/gap-fill**: クライアント側で`last_sequence`を保持し、再接続時は新しいticketを
   要求し、feedがまだ生存していれば直近ring bufferから再送、feedが再生成されていれば
   最新確定足時刻からのREST gap-fillにフォールバックする。
7. **フロントエンド**: `CandleChart`へ`series.update(...)`によるprovisional更新を追加し、
   接続状態（connected/reconnecting/delayed/disconnected）、直近データ時刻、gap件数、
   Worker heartbeat状態を画面表示する（`App.tsx`または将来のfeature分割後のmarket-data feature）。
8. **耐障害性・運用可視性の試験**: 切断/再接続、重複、順不同、clock skew、backpressure、
   24時間soak testを専用環境で実施する。（本番相当環境での最終確認の実施時期は
   `../decisions/0001-defer-realtime-stream-soak-test.md`を参照）

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

- ~~`python-binance`のAsync/WebSocket機能がasync_timeout欠如でimport不能~~
  （2026-09-17訂正: 誤りだったため取り下げ。当初`.venv313`をLinux VM側の素のPython 3.10で
  importして再現したが、これは実行環境の取り違えによる誤検証だった。aiohttpの実ソース
  （`aiohttp/helpers.py`等）は`if sys.version_info >= (3, 11): import asyncio as async_timeout`
  のガードを持ち、Python 3.13では外部`async_timeout`パッケージを一切必要としない。さらに
  `app/exchanges/binance.py`が既に`from binance import AsyncClient`でこの経路をimportしており
  （`binance/__init__.py`は`AsyncClient`と`BinanceSocketManager`を同じimport文で読み込む）、
  既存CIのPython 3.13.15上で現に成功し続けている。依存解消の作業は不要。）
- OANDA `PricingStream`はblocking generatorのため、`asyncio.to_thread`または専用threadでの
  実行方式を②で確定する。
- OANDA Practice実データでの動作確認は、既存のHorizon 0の制約（APIキー生成不可）が
  解消されない限り引き続きNOT VERIFIEDとなる可能性がある。

## 完了ゲートと既知制限

各単位で既存＋追加テスト、変更箇所format/lint、型検査、frontend build/lintを実施する。
UIを変更する単位（⑦）はブラウザ試験を行う。未実行検証を成功に読み替えない。
Durable Worker（①〜⑤）と同様、専用PostgreSQL・架空のexchange応答での回帰試験を優先し、
運用DBや実取引所APIへは明示的な承認・接続確認の後にのみ接続する。
